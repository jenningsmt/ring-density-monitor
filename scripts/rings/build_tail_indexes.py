from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from scripts.rings.metric_resolver import resolve_moi_metric, sanitize_identifier_for_index
from contextlib import closing


def build_tail_indexes(
    conn: sqlite3.Connection,
    with_asc: bool = False,
    moi_metric: str | None = None,
) -> tuple[str, list[str]]:
    resolved_metric = resolve_moi_metric(conn, preferred=moi_metric)
    metric_token = sanitize_identifier_for_index(resolved_metric)
    desc_index = f"idx_scored_v_rt_{metric_token}_desc"
    asc_index = f"idx_scored_v_rt_{metric_token}_asc"
    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {desc_index}
        ON rings_scored(score_version, ring_type, {resolved_metric} DESC, ring_id ASC)
        """
    )
    if with_asc:
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {asc_index}
            ON rings_scored(score_version, ring_type, {resolved_metric} ASC, ring_id ASC)
            """
        )
    rows = conn.execute(
        f"""
        SELECT name
        FROM sqlite_master
        WHERE type='index' AND name LIKE 'idx_scored_v_rt_{metric_token}_%'
        ORDER BY name
        """
    ).fetchall()
    return resolved_metric, [row[0] for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build deterministic tail indexes on rings_scored.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--score-version", default="moi_v1", help="Reserved for compatibility.")
    parser.add_argument("--moi-metric", default=None, help="Optional MOI metric column to index.")
    parser.add_argument("--with-asc", action="store_true", help="Also create ascending MOI index.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            metric, names = build_tail_indexes(conn, with_asc=args.with_asc, moi_metric=args.moi_metric)
            conn.commit()
    except Exception as exc:
        print(f"Failed to build indexes: {exc}")
        return 1

    print(f"Database: {db_path}")
    print(f"Resolved MOI metric: {metric}")
    print(f"Indexes found ({len(names)}):")
    for name in names:
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
