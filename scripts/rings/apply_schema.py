from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from contextlib import closing


DEFAULT_SCHEMA = Path("scripts/rings/schema_phase3.sql")
PHASE3_TABLES = ("global_norms", "cohort_cutoffs", "cohort_members")


def apply_schema_file(db_path: Path, schema_path: Path) -> list[str]:
    schema_sql = schema_path.read_text(encoding="utf-8")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(schema_sql)
        conn.commit()
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name IN (?, ?, ?)
            ORDER BY name
            """,
            PHASE3_TABLES,
        ).fetchall()
    return [row[0] for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply a SQL schema file to a SQLite database.")
    parser.add_argument("--db", required=True, help="Path to the SQLite database.")
    parser.add_argument(
        "--schema",
        default=str(DEFAULT_SCHEMA),
        help="Path to schema SQL file (default: scripts/rings/schema_phase3.sql).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    schema_path = Path(args.schema)

    try:
        created = apply_schema_file(db_path, schema_path)
    except Exception as exc:
        print(f"Failed to apply schema: {exc}")
        return 1

    print(f"Schema file: {schema_path}")
    print(f"Database: {db_path}")
    print(f"Phase3 tables present ({len(created)}): {', '.join(created)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
