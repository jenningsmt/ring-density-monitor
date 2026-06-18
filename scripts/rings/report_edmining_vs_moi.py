from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_OUT = Path("docs/ring_hunter/edmining_vs_moi_report.md")


@dataclass(frozen=True)
class EDMRow:
    source_url: str
    system_name: str
    planets: str


@dataclass(frozen=True)
class MatchRow:
    ring_id: str
    system_name: str
    body_name: str | None
    ring_name: str | None


@dataclass(frozen=True)
class ScoreRow:
    ring_id: str
    ring_type: str | None
    moi_final: float | None


@dataclass(frozen=True)
class RankedCandidate:
    ring_id: str
    ring_type: str | None
    moi_final: float | None
    boundary_match: bool
    exactish_match: bool


def _norm_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().split()).lower()


def normalize_planet_tokens(planets: str | None) -> list[str]:
    text = _norm_text(planets).replace("rings", "")
    if not text:
        return []
    # Split primarily by "and", but also by commas for defensive handling.
    parts: list[str] = []
    for block in text.split(" and "):
        parts.extend(block.split(","))
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = " ".join(part.split()).strip()
        token = re.sub(r"([a-z]+)(\d+)", r"\1 \2", token)
        token = " ".join(token.split())
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _require_schema(conn: sqlite3.Connection) -> None:
    required = ("known_sites_edmining", "rings_raw", "rings_scored", "cohort_members")
    missing = [t for t in required if not _table_exists(conn, t)]
    if missing:
        raise RuntimeError(f"Missing required tables: {', '.join(missing)}")
    ks_cols = _table_cols(conn, "known_sites_edmining")
    rr_cols = _table_cols(conn, "rings_raw")
    rs_cols = _table_cols(conn, "rings_scored")
    cm_cols = _table_cols(conn, "cohort_members")
    if not {"source_url", "system_name", "planets"}.issubset(ks_cols):
        raise RuntimeError("known_sites_edmining must have source_url, system_name and planets.")
    if not {"ring_id", "system_name", "body_name", "ring_name"}.issubset(rr_cols):
        raise RuntimeError("rings_raw must have ring_id, system_name, body_name, ring_name.")
    if not {"ring_id", "score_version", "moi_final"}.issubset(rs_cols):
        raise RuntimeError("rings_scored must have ring_id, score_version, moi_final.")
    if not {"ring_id", "score_version", "cohort_name"}.issubset(cm_cols):
        raise RuntimeError("cohort_members must have ring_id, score_version, cohort_name.")


def ensure_matching_index(conn: sqlite3.Connection, emit: Callable[[str], None] | None = None) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rings_raw_system_name_nocase
        ON rings_raw(system_name COLLATE NOCASE)
        """
    )
    conn.commit()
    if emit is not None:
        emit("Ensured index: idx_rings_raw_system_name_nocase")


def load_edmining_rows(conn: sqlite3.Connection) -> list[EDMRow]:
    rows = conn.execute(
        """
        SELECT COALESCE(source_url, ''), COALESCE(system_name, ''), COALESCE(planets, '')
        FROM known_sites_edmining
        ORDER BY system_name ASC, planets ASC, source_url ASC
        """
    ).fetchall()
    return [EDMRow(source_url=row[0], system_name=row[1], planets=row[2]) for row in rows]


def find_candidate_rings(conn: sqlite3.Connection, system_key: str, planet_tokens: list[str]) -> list[MatchRow]:
    if not system_key or not planet_tokens:
        return []
    rows = conn.execute(
        """
        SELECT ring_id, system_name, body_name, ring_name
        FROM rings_raw
        WHERE system_name = ? COLLATE NOCASE
        ORDER BY ring_id ASC
        """,
        (system_key,),
    ).fetchall()
    out: list[MatchRow] = []
    for row in rows:
        body = _norm_text(row[2])
        ring = _norm_text(row[3])
        if any(token_matches_text(tok, body) or token_matches_text(tok, ring) for tok in planet_tokens):
            out.append(MatchRow(ring_id=row[0], system_name=row[1], body_name=row[2], ring_name=row[3]))
    return out


def load_scores(conn: sqlite3.Connection, score_version: str, ring_ids: list[str]) -> dict[str, ScoreRow]:
    if not ring_ids:
        return {}
    qmarks = ",".join("?" for _ in ring_ids)
    rows = conn.execute(
        f"""
        SELECT ring_id, ring_type, moi_final
        FROM rings_scored
        WHERE score_version=? AND ring_id IN ({qmarks})
        """,
        (score_version, *ring_ids),
    ).fetchall()
    return {row[0]: ScoreRow(ring_id=row[0], ring_type=row[1], moi_final=row[2]) for row in rows}


def load_icycore_members(conn: sqlite3.Connection, score_version: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT ring_id
        FROM cohort_members
        WHERE score_version=? AND cohort_name='IcyCore'
        """,
        (score_version,),
    ).fetchall()
    return {row[0] for row in rows}


def _count_nonnull_by_ring_type(conn: sqlite3.Connection, score_version: str, ring_type: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM rings_scored
        WHERE score_version=? AND ring_type=? AND moi_final IS NOT NULL
        """,
        (score_version, ring_type),
    ).fetchone()
    return int(row[0]) if row else 0


def _count_le_moi(conn: sqlite3.Connection, score_version: str, ring_type: str, moi_value: float) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM rings_scored
        WHERE score_version=? AND ring_type=? AND moi_final <= ?
        """,
        (score_version, ring_type, moi_value),
    ).fetchone()
    return int(row[0]) if row else 0


def percentile_for_ring(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str | None,
    moi_value: float | None,
    n_cache: dict[str, int],
) -> float | None:
    if ring_type is None or moi_value is None:
        return None
    if ring_type not in n_cache:
        n_cache[ring_type] = _count_nonnull_by_ring_type(conn, score_version, ring_type)
    n = n_cache[ring_type]
    if n <= 0:
        return None
    count_le = _count_le_moi(conn, score_version, ring_type, float(moi_value))
    return float(count_le) / float(n)


def token_matches_text(token: str, text: str) -> bool:
    if not token:
        return False
    if token.isdigit():
        return re.search(rf"\b{re.escape(token)}\b", text) is not None
    return token in text


def token_boundary_match(token: str, text: str) -> bool:
    if not token:
        return False
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def evaluate_candidate(match: MatchRow, score: ScoreRow | None, tokens: list[str]) -> RankedCandidate:
    body_norm = _norm_text(match.body_name)
    return RankedCandidate(
        ring_id=match.ring_id,
        ring_type=score.ring_type if score else None,
        moi_final=score.moi_final if score else None,
        boundary_match=any(token_boundary_match(tok, body_norm) for tok in tokens),
        exactish_match=any(tok in body_norm for tok in tokens if tok),
    )


def sort_ranked_candidates(candidates: list[RankedCandidate]) -> list[RankedCandidate]:
    return sorted(
        candidates,
        key=lambda c: (
            0 if c.ring_type == "Icy" else 1,
            0 if c.boundary_match else 1,
            0 if c.exactish_match else 1,
            0 if c.moi_final is not None else 1,
            -(c.moi_final if c.moi_final is not None else 0.0),
            c.ring_id,
        ),
    )


def format_top_candidates(candidates: list[RankedCandidate], top_n: int = 3) -> str:
    parts: list[str] = []
    for candidate in candidates[:top_n]:
        moi = "" if candidate.moi_final is None else f"{candidate.moi_final:.6f}"
        ring_type = candidate.ring_type or ""
        parts.append(f"{candidate.ring_id}:{moi}:{ring_type}")
    return "; ".join(parts)


def status_for_match_count(n: int) -> str:
    if n == 0:
        return "NOT_FOUND"
    if n == 1:
        return "MATCHED"
    return "MATCHED_MULTIPLE"


def _format_opt_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def apply_phase4_schema(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema_phase4_edmining.sql")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()


def upsert_best_matches(
    conn: sqlite3.Connection,
    score_version: str,
    computed_at: str,
    rows: list[dict[str, object]],
) -> None:
    payload = [
        (
            str(row["source_url"]),
            str(row["system_name"]),
            str(row["planets"]),
            str(row["status"]),
            int(row["candidate_count"]),
            str(row["best_ring_id"]) or None,
            str(row["best_ring_type"]) or None,
            row["best_moi_final"],
            row["best_percentile"],
            int(row["best_in_icycore"]),
            str(row["top_candidates"]),
            computed_at,
            score_version,
        )
        for row in rows
    ]
    conn.executemany(
        """
        INSERT INTO edmining_best_matches (
            source_url, system_name, planets, status, candidate_count,
            best_ring_id, best_ring_type, best_moi_final, best_percentile,
            best_in_icycore, top_candidates, computed_at, score_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_url) DO UPDATE SET
            system_name=excluded.system_name,
            planets=excluded.planets,
            status=excluded.status,
            candidate_count=excluded.candidate_count,
            best_ring_id=excluded.best_ring_id,
            best_ring_type=excluded.best_ring_type,
            best_moi_final=excluded.best_moi_final,
            best_percentile=excluded.best_percentile,
            best_in_icycore=excluded.best_in_icycore,
            top_candidates=excluded.top_candidates,
            computed_at=excluded.computed_at,
            score_version=excluded.score_version
        """,
        payload,
    )
    conn.commit()


def write_csv(out_csv: Path, rows: list[dict[str, object]]) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            str(row["system_name"]).lower(),
            str(row["planets"]).lower(),
            str(row["source_url"]).lower(),
        ),
    )
    headers = [
        "source_url",
        "system_name",
        "planets",
        "status",
        "candidate_count",
        "best_ring_id",
        "best_ring_type",
        "best_moi_final",
        "best_percentile",
        "best_in_icycore",
        "top_candidates",
        "score_version",
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in sorted_rows:
            writer.writerow(
                {
                    "source_url": row["source_url"],
                    "system_name": row["system_name"],
                    "planets": row["planets"],
                    "status": row["status"],
                    "candidate_count": row["candidate_count"],
                    "best_ring_id": row["best_ring_id"],
                    "best_ring_type": row["best_ring_type"],
                    "best_moi_final": _format_opt_float(row["best_moi_final"]),  # type: ignore[arg-type]
                    "best_percentile": _format_opt_float(row["best_percentile"]),  # type: ignore[arg-type]
                    "best_in_icycore": row["best_in_icycore"],
                    "top_candidates": row["top_candidates"],
                    "score_version": row["score_version"],
                }
            )


def write_report(
    db_path: Path,
    score_version: str,
    out_path: Path,
    write_db: bool = False,
    out_csv: Path | None = None,
    emit: Callable[[str], None] | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        _require_schema(conn)
        ensure_matching_index(conn, emit=emit)
        ed_rows = load_edmining_rows(conn)
        icycore_set = load_icycore_members(conn, score_version)
        n_cache: dict[str, int] = {}
        total_sites = len(ed_rows)
        started = time.perf_counter()

        report_rows: list[dict[str, object]] = []
        in_icycore_count = 0
        matched_count = 0
        matched_multiple_count = 0
        not_found_count = 0
        best_icycore_count = 0

        for ed in ed_rows:
            system_key = _norm_text(ed.system_name)
            tokens = normalize_planet_tokens(ed.planets)
            matches = find_candidate_rings(conn, system_key, tokens)
            status = status_for_match_count(len(matches))
            if status == "MATCHED":
                matched_count += 1
            elif status == "MATCHED_MULTIPLE":
                matched_multiple_count += 1
            else:
                not_found_count += 1

            ring_ids = [m.ring_id for m in matches]
            scores = load_scores(conn, score_version, ring_ids)
            ranked = sort_ranked_candidates([evaluate_candidate(m, scores.get(m.ring_id), tokens) for m in matches])
            top = ranked[0] if ranked else None
            in_icycore = any(rid in icycore_set for rid in ring_ids)
            if in_icycore:
                in_icycore_count += 1
            if top and top.ring_id in icycore_set:
                best_icycore_count += 1

            ring_type = top.ring_type if top else None
            moi_final = top.moi_final if top else None
            percentile = percentile_for_ring(conn, score_version, ring_type, moi_final, n_cache)

            report_rows.append(
                {
                    "source_url": ed.source_url,
                    "system_name": ed.system_name,
                    "planets": ed.planets,
                    "status": status,
                    "candidate_count": len(matches),
                    "best_ring_id": top.ring_id if top else "",
                    "best_ring_type": ring_type or "",
                    "best_moi_final": moi_final,
                    "best_percentile": percentile,
                    "best_in_icycore": 1 if (top and top.ring_id in icycore_set) else 0,
                    "top_candidates": format_top_candidates(ranked),
                    "score_version": score_version,
                }
            )

            if len(report_rows) % 5 == 0:
                elapsed = time.perf_counter() - started
                if emit is not None:
                    emit(
                        f"processed {len(report_rows)}/{total_sites}; "
                        f"matched={matched_count} multiple={matched_multiple_count} "
                        f"not_found={not_found_count}; elapsed={elapsed:.2f}s"
                    )
    finally:
        conn.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# EDMining vs MOI Crosswalk Report")
    lines.append("")
    lines.append(f"- DB: `{db_path}`")
    lines.append(f"- score_version: `{score_version}`")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- total_sites: {len(report_rows)}")
    lines.append(f"- matched_count: {matched_count}")
    lines.append(f"- matched_multiple_count: {matched_multiple_count}")
    lines.append(f"- not_found_count: {not_found_count}")
    lines.append(f"- in_icycore_count: {in_icycore_count}")
    lines.append(f"- best_icycore_count: {best_icycore_count}")
    lines.append("")
    lines.append("## Site Table")
    lines.append("")
    lines.append(
        "| system_name | planets | status | candidate_count | best_ring_id | best_ring_type | best_moi_final | best_percentile | best_in_icycore | top_candidates |"
    )
    lines.append("|---|---|---:|---:|---|---|---:|---:|---|---|")
    for row in report_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["system_name"]),
                    str(row["planets"]),
                    str(row["status"]),
                    str(row["candidate_count"]),
                    str(row["best_ring_id"]),
                    str(row["best_ring_type"]),
                    _format_opt_float(row["best_moi_final"]),  # type: ignore[arg-type]
                    _format_opt_float(row["best_percentile"]),  # type: ignore[arg-type]
                    "yes" if int(row["best_in_icycore"]) else "no",
                    str(row["top_candidates"]),
                ]
            )
            + " |"
        )
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")

    if out_csv is not None:
        write_csv(out_csv, report_rows)

    if write_db:
        conn = sqlite3.connect(db_path)
        try:
            apply_phase4_schema(conn)
            computed_at = datetime.now(timezone.utc).isoformat()
            upsert_best_matches(conn, score_version, computed_at, report_rows)
        finally:
            conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate EDMining Tritium vs Ring Hunter MOI crosswalk report.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--out-csv")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress and status output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_csv = Path(args.out_csv) if args.out_csv else None
    write_report(
        Path(args.db),
        args.score_version,
        Path(args.out),
        write_db=args.write_db,
        out_csv=out_csv,
        emit=None if args.quiet else print,
    )
    if not args.quiet:
        print(f"Wrote report: {args.out}")
        if args.out_csv:
            print(f"Wrote CSV: {args.out_csv}")
        if args.write_db:
            print("Upserted edmining_best_matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
