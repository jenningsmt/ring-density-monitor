from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.rings.metric_resolver import get_table_columns, resolve_moi_metric
from contextlib import closing

REQUIRED_PHASE3_TABLES = ("global_norms", "cohort_cutoffs", "cohort_members")
REQUIRED_SCORED_COLUMNS = ("score_version", "ring_type", "ring_id")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_phase3_tables(conn: sqlite3.Connection) -> None:
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
            REQUIRED_PHASE3_TABLES,
        ).fetchall()
    }
    missing = [name for name in REQUIRED_PHASE3_TABLES if name not in existing]
    if missing:
        raise RuntimeError(
            f"Missing Phase 3 tables: {', '.join(missing)}. Run python -m scripts.rings.apply_schema --db <PATH>."
        )


def ensure_rings_scored_columns(conn: sqlite3.Connection, moi_metric: str) -> None:
    cols = get_table_columns(conn, "rings_scored")
    missing = [name for name in (*REQUIRED_SCORED_COLUMNS, moi_metric) if name not in cols]
    if missing:
        raise RuntimeError(f"Missing required rings_scored columns: {', '.join(missing)}")


def fetch_nth_theta(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    target_n: int,
    moi_metric: str,
) -> tuple[str, float]:
    if target_n <= 0:
        raise RuntimeError("target_n must be >= 1")
    row = conn.execute(
        f"""
        SELECT ring_id, {moi_metric}
        FROM rings_scored
        WHERE score_version=? AND ring_type=? AND {moi_metric} IS NOT NULL
        ORDER BY {moi_metric} DESC, ring_id ASC
        LIMIT 1 OFFSET ?
        """,
        (score_version, ring_type, target_n - 1),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"Insufficient rows for score_version={score_version}, ring_type={ring_type}, target_n={target_n}"
        )
    return row[0], float(row[1])


def fetch_top_n(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    n: int,
    moi_metric: str,
) -> list[tuple[str, float]]:
    if n <= 0:
        return []
    rows = conn.execute(
        f"""
        SELECT ring_id, {moi_metric}
        FROM rings_scored
        WHERE score_version=? AND ring_type=? AND {moi_metric} IS NOT NULL
        ORDER BY {moi_metric} DESC, ring_id ASC
        LIMIT ?
        """,
        (score_version, ring_type, n),
    ).fetchall()
    return [(row[0], float(row[1])) for row in rows]


def compute_and_materialize(
    conn: sqlite3.Connection,
    score_version: str,
    cohort_name: str,
    ring_type: str,
    target_n: int,
    algo_version: str,
    moi_metric: str | None = None,
    dry_run: bool = False,
) -> tuple[str, float, int]:
    ensure_phase3_tables(conn)
    resolved_moi_metric = resolve_moi_metric(conn, preferred=moi_metric)
    ensure_rings_scored_columns(conn, resolved_moi_metric)

    theta_ring_id, theta_value = fetch_nth_theta(conn, score_version, ring_type, target_n, resolved_moi_metric)
    top_rows = fetch_top_n(conn, score_version, ring_type, target_n, resolved_moi_metric)
    if len(top_rows) < target_n:
        raise RuntimeError(
            f"Insufficient rows to materialize top {target_n}: got {len(top_rows)} for score_version={score_version}, ring_type={ring_type}"
        )

    if not dry_run:
        computed_at = utc_now_iso()
        conn.execute("BEGIN")
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO cohort_cutoffs (
                    score_version,
                    cohort_name,
                    ring_type,
                    target_n,
                    theta_value,
                    theta_ring_id,
                    computed_at,
                    algo_version,
                    notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    score_version,
                    cohort_name,
                    ring_type,
                    target_n,
                    theta_value,
                    theta_ring_id,
                    computed_at,
                    algo_version,
                ),
            )
            conn.execute(
                """
                DELETE FROM cohort_members
                WHERE score_version=? AND cohort_name=?
                """,
                (score_version, cohort_name),
            )
            payload = [
                (score_version, cohort_name, ring_id, rank_idx, moi0)
                for rank_idx, (ring_id, moi0) in enumerate(top_rows, start=1)
            ]
            conn.executemany(
                """
                INSERT INTO cohort_members (
                    score_version,
                    cohort_name,
                    ring_id,
                    rank_in_cohort,
                    moi0
                ) VALUES (?, ?, ?, ?, ?)
                """,
                payload,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return theta_ring_id, theta_value, len(top_rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute deterministic cohort cutoff and materialized members.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--score-version", default="moi_v1", help="Score version filter.")
    parser.add_argument("--moi-metric", default=None, help="Optional MOI metric column override.")
    parser.add_argument("--cohort-name", required=True, help="Cohort name key.")
    parser.add_argument("--ring-type", required=True, help="Ring type filter.")
    parser.add_argument("--target-n", required=True, type=int, help="Top-N target for this cohort.")
    parser.add_argument("--algo-version", default="cutoff_v1", help="Algorithm version tag to store.")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print without writing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            theta_ring_id, theta_value, inserted_count = compute_and_materialize(
                conn=conn,
                score_version=args.score_version,
                cohort_name=args.cohort_name,
                ring_type=args.ring_type,
                target_n=args.target_n,
                algo_version=args.algo_version,
                moi_metric=args.moi_metric,
                dry_run=args.dry_run,
            )
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    print(
        "cutoff "
        f"cohort_name={args.cohort_name} "
        f"ring_type={args.ring_type} "
        f"target_n={args.target_n} "
        f"theta_value={theta_value} "
        f"theta_ring_id={theta_ring_id}"
    )
    print(f"members_inserted={inserted_count}")
    if args.dry_run:
        print("dry-run enabled: no database writes were performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
