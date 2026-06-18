from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_DB = Path("data/ring_hunter_library/rings_master.sqlite")
DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_PROGRESS_SECONDS = 10
DEFAULT_CHUNK_SIZE = 2000
NORM_POPULATION = "all_non_null_moi_raw"


REQUIRED_RINGS_RAW_COLS = {
    "ring_id",
    "ring_type",
    "surface_density",
    "linear_density",
    "arrival_distance_ls",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def ensure_scoring_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rings_scored (
            ring_id TEXT PRIMARY KEY,
            ring_type TEXT NULL,
            score_version TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            moi_raw REAL NULL,
            moi_normalized REAL NULL,
            moi_final REAL NULL,
            ssd_linear_density REAL NULL,
            ssd_parent_g REAL NULL,
            ssd_score_raw REAL NULL,
            ssd_score REAL NULL,
            norm_population TEXT NULL,
            norm_count INTEGER NULL,
            flags TEXT NULL
        )
        """
    )
    ring_scored_cols = _table_columns(conn, "rings_scored")
    if "ring_type" not in ring_scored_cols:
        conn.execute("ALTER TABLE rings_scored ADD COLUMN ring_type TEXT NULL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS score_runs (
            run_id INTEGER PRIMARY KEY,
            score_version TEXT NOT NULL,
            params_json TEXT NULL,
            git_commit TEXT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NULL
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rings_raw_ring_id ON rings_raw(ring_id)")
    if _table_exists(conn, "ring_survey"):
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ring_survey_ring_id ON ring_survey(ring_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rings_scored_score_version ON rings_scored(score_version)")


def validate_input_schema(conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not _table_exists(conn, "rings_raw"):
        errors.append("Missing required table: rings_raw")
        return False, errors

    cols = _table_columns(conn, "rings_raw")
    missing = sorted(REQUIRED_RINGS_RAW_COLS - cols)
    if missing:
        errors.append("Missing required rings_raw columns: " + ", ".join(missing))
    return len(errors) == 0, errors


def compute_moi_raw(
    surface_density: Optional[float],
    linear_density: Optional[float],
    arrival_distance_ls: Optional[float],
) -> tuple[Optional[float], str]:
    flags: list[str] = []
    if surface_density is None:
        flags.append("missing_surface_density")
    if linear_density is None:
        flags.append("missing_linear_density")
    if arrival_distance_ls is None:
        flags.append("missing_arrival_distance")

    if surface_density is None or linear_density is None:
        return None, "|".join(sorted(flags))

    arrival_term = 0.0
    if arrival_distance_ls is not None:
        arrival_term = 1.0 / (1.0 + max(float(arrival_distance_ls), 0.0))

    moi_raw = (0.70 * float(surface_density)) + (0.25 * float(linear_density)) + (0.05 * arrival_term)
    return moi_raw, "|".join(sorted(flags))


def _iter_rings_raw(
    conn: sqlite3.Connection,
    limit: Optional[int],
):
    sql = """
        SELECT ring_id, ring_type, surface_density, linear_density, arrival_distance_ls
        FROM rings_raw
        ORDER BY ring_id
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return conn.execute(sql, params)


def _build_temp_scores(
    conn: sqlite3.Connection,
    *,
    limit: Optional[int],
    chunk_size: int,
    progress_seconds: int,
) -> tuple[int, int]:
    conn.execute("DROP TABLE IF EXISTS tmp_moi_scores")
    conn.execute(
        """
        CREATE TEMP TABLE tmp_moi_scores (
            ring_id TEXT PRIMARY KEY,
            ring_type TEXT NULL,
            moi_raw REAL NULL,
            flags TEXT NULL
        )
        """
    )

    cursor = _iter_rings_raw(conn, limit)
    processed = 0
    eligible = 0
    batch: list[tuple[str, Optional[str], Optional[float], str]] = []
    last_progress = time.monotonic()

    while True:
        rows = cursor.fetchmany(chunk_size)
        if not rows:
            break
        for row in rows:
            moi_raw, flags = compute_moi_raw(
                surface_density=row["surface_density"],
                linear_density=row["linear_density"],
                arrival_distance_ls=row["arrival_distance_ls"],
            )
            if moi_raw is not None:
                eligible += 1
            batch.append((row["ring_id"], row["ring_type"], moi_raw, flags))
            processed += 1

        conn.executemany(
            "INSERT OR REPLACE INTO tmp_moi_scores (ring_id, ring_type, moi_raw, flags) VALUES (?, ?, ?, ?)",
            batch,
        )
        batch.clear()

        now = time.monotonic()
        if progress_seconds > 0 and (now - last_progress) >= progress_seconds:
            print(f"progress processed={processed} eligible={eligible}", flush=True)
            last_progress = now

    return processed, eligible


def _build_temp_norm(conn: sqlite3.Connection, norm_count: int) -> None:
    conn.execute("DROP TABLE IF EXISTS tmp_moi_norm")
    conn.execute(
        """
        CREATE TEMP TABLE tmp_moi_norm (
            ring_id TEXT PRIMARY KEY,
            moi_normalized REAL NULL
        )
        """
    )

    if norm_count <= 0:
        return

    cursor = conn.execute(
        """
        SELECT ring_id, moi_raw
        FROM tmp_moi_scores
        WHERE moi_raw IS NOT NULL
        ORDER BY moi_raw ASC, ring_id ASC
        """
    )
    rows = cursor.fetchall()
    if norm_count == 1:
        payload = [(rows[0]["ring_id"], 1.0)]
    else:
        denom = float(norm_count - 1)
        payload = [(row["ring_id"], idx / denom) for idx, row in enumerate(rows)]

    conn.executemany(
        "INSERT INTO tmp_moi_norm (ring_id, moi_normalized) VALUES (?, ?)",
        payload,
    )


def _upsert_scores(
    conn: sqlite3.Connection,
    *,
    score_version: str,
    computed_at: str,
    norm_population: str,
    norm_count: int,
) -> int:
    conn.execute(
        """
        INSERT INTO rings_scored (
            ring_id,
            ring_type,
            score_version,
            computed_at,
            moi_raw,
            moi_normalized,
            moi_final,
            ssd_linear_density,
            ssd_parent_g,
            ssd_score_raw,
            ssd_score,
            norm_population,
            norm_count,
            flags
        )
        SELECT
            s.ring_id,
            s.ring_type,
            ?,
            ?,
            s.moi_raw,
            n.moi_normalized,
            n.moi_normalized,
            NULL,
            NULL,
            NULL,
            NULL,
            ?,
            ?,
            s.flags
        FROM tmp_moi_scores s
        LEFT JOIN tmp_moi_norm n ON n.ring_id = s.ring_id
        ON CONFLICT(ring_id) DO UPDATE SET
            ring_type=excluded.ring_type,
            score_version=excluded.score_version,
            computed_at=excluded.computed_at,
            moi_raw=excluded.moi_raw,
            moi_normalized=excluded.moi_normalized,
            moi_final=excluded.moi_final,
            norm_population=excluded.norm_population,
            norm_count=excluded.norm_count,
            flags=excluded.flags
        """,
        (score_version, computed_at, norm_population, norm_count),
    )
    row = conn.execute("SELECT COUNT(*) AS c FROM tmp_moi_scores").fetchone()
    return int(row["c"])


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministically recompute MOI scores into rings_scored.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to rings_master.sqlite")
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION, help="Score version tag")
    parser.add_argument("--dry-run", action="store_true", help="Compute only; do not write DB")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of rings processed (ORDER BY ring_id)")
    parser.add_argument(
        "--progress-seconds",
        type=int,
        default=DEFAULT_PROGRESS_SECONDS,
        help="Progress print interval in seconds",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress and summary output.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.limit is not None and args.limit < 0:
        print("--limit must be >= 0")
        return 2
    if args.progress_seconds <= 0:
        print("--progress-seconds must be >= 1")
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    start = time.monotonic()
    try:
        valid, errors = validate_input_schema(conn)
        if not valid:
            for err in errors:
                print(err)
            return 2

        ensure_scoring_schema(conn)

        processed, eligible = _build_temp_scores(
            conn,
            limit=args.limit,
            chunk_size=DEFAULT_CHUNK_SIZE,
            progress_seconds=0 if args.quiet else args.progress_seconds,
        )
        norm_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM tmp_moi_scores WHERE moi_raw IS NOT NULL"
            ).fetchone()["c"]
        )
        _build_temp_norm(conn, norm_count)
        conn.commit()

        upserted = 0
        run_id: Optional[int] = None
        started_at = utc_now_iso()
        finished_at = utc_now_iso()
        params_json = json.dumps(
            {
                "score_version": args.score_version,
                "population": NORM_POPULATION,
                "options": {
                    "limit": args.limit,
                    "progress_seconds": args.progress_seconds,
                    "dry_run": args.dry_run,
                },
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        git_commit = os.getenv("GIT_COMMIT")

        if not args.dry_run:
            conn.execute("BEGIN")
            try:
                cur = conn.execute(
                    """
                    INSERT INTO score_runs (score_version, params_json, git_commit, started_at, finished_at)
                    VALUES (?, ?, ?, ?, NULL)
                    """,
                    (args.score_version, params_json, git_commit, started_at),
                )
                run_id = int(cur.lastrowid)
                computed_at = utc_now_iso()
                upserted = _upsert_scores(
                    conn,
                    score_version=args.score_version,
                    computed_at=computed_at,
                    norm_population=NORM_POPULATION,
                    norm_count=norm_count,
                )
                finished_at = utc_now_iso()
                conn.execute(
                    "UPDATE score_runs SET finished_at=? WHERE run_id=?",
                    (finished_at, run_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        elapsed = time.monotonic() - start
        if not args.quiet:
            print(f"processed={processed}")
            print(f"eligible={eligible}")
            print(f"norm_population={NORM_POPULATION}")
            print(f"norm_count={norm_count}")
            print(f"upserted={upserted if not args.dry_run else 0}")
            print(f"dry_run={args.dry_run}")
            if run_id is not None:
                print(f"run_id={run_id}")
                print(f"started_at={started_at}")
                print(f"finished_at={finished_at}")
            print(f"elapsed_seconds={elapsed:.3f}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
