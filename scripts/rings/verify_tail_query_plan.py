from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from scripts.rings.metric_resolver import resolve_moi_metric
from contextlib import closing

def get_query_plan_rows(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    moi_metric: str,
) -> tuple[list[tuple], list[tuple]]:
    top_sql = f"""
    EXPLAIN QUERY PLAN
    SELECT ring_id, {moi_metric}
    FROM rings_scored
    WHERE score_version=? AND ring_type=? AND {moi_metric} IS NOT NULL
    ORDER BY {moi_metric} DESC, ring_id ASC
    LIMIT 1000
    """
    nth_sql = f"""
    EXPLAIN QUERY PLAN
    SELECT ring_id, {moi_metric}
    FROM rings_scored
    WHERE score_version=? AND ring_type=? AND {moi_metric} IS NOT NULL
    ORDER BY {moi_metric} DESC, ring_id ASC
    LIMIT 1 OFFSET 999
    """
    top_rows = conn.execute(top_sql, (score_version, ring_type)).fetchall()
    nth_rows = conn.execute(nth_sql, (score_version, ring_type)).fetchall()
    return top_rows, nth_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect query plan for deterministic tail queries.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--score-version", default="moi_v1", help="Score version filter.")
    parser.add_argument("--moi-metric", default=None, help="Optional MOI metric column override.")
    parser.add_argument("--ring-type", required=True, help="Ring type filter.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            moi_metric = resolve_moi_metric(conn, preferred=args.moi_metric)
            top_rows, nth_rows = get_query_plan_rows(conn, args.score_version, args.ring_type, moi_metric)
    except Exception as exc:
        print(f"Failed to inspect query plans: {exc}")
        return 0

    print(f"Resolved MOI metric: {moi_metric}")
    print("Top-N plan:")
    for row in top_rows:
        print(tuple(row))
    print("Nth plan:")
    for row in nth_rows:
        print(tuple(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
