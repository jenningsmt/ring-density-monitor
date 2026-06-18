from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _create_analysis_indexes(conn: sqlite3.Connection, verbose: bool = False) -> None:
    if not _table_exists(conn, "rings_raw"):
        raise ValueError("Missing required table: rings_raw")
    if not _table_exists(conn, "rings_scored"):
        raise ValueError("Missing required table: rings_scored")
    scored_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(rings_scored)").fetchall()
    }
    if "ring_type" not in scored_cols:
        conn.execute("ALTER TABLE rings_scored ADD COLUMN ring_type TEXT NULL")

    if verbose:
        print("Creating analysis indexes...")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rings_raw_ring_type_ring_id
        ON rings_raw(ring_type, ring_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rings_scored_moi_final_desc_notnull
        ON rings_scored(moi_final DESC)
        WHERE moi_final IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rings_scored_ring_type_moi_final_desc_notnull
        ON rings_scored(ring_type, moi_final DESC)
        WHERE moi_final IS NOT NULL
        """
    )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build post-ingest Ring Hunter indexes.")
    parser.add_argument(
        "--db",
        default="data/ring_hunter_library/rings_master.sqlite",
        help="Path to rings_master.sqlite",
    )
    parser.add_argument(
        "--analysis",
        action="store_true",
        help="Build analysis/top-list indexes.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        if args.analysis:
            conn.execute("BEGIN")
            try:
                _create_analysis_indexes(conn, verbose=args.verbose)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        else:
            if args.verbose:
                print("No index profile selected; nothing to do.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
