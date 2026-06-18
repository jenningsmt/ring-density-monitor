from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_OUT_DIR = Path("data/ring_hunter_library/icycore_quadrants")


def export_quadrant_lists(
    db_path: Path,
    score_version: str = DEFAULT_SCORE_VERSION,
    cohort_name: str = DEFAULT_COHORT_NAME,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with closing(sqlite3.connect(db_path)) as conn:
        quadrants = [
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT quadrant
                FROM icy_quadrants
                WHERE score_version=? AND cohort_name=?
                ORDER BY quadrant ASC
                """,
                (score_version, cohort_name),
            ).fetchall()
        ]
        for quadrant in quadrants:
            rows = conn.execute(
                """
                SELECT rank, ring_id, moi_metric, system_name, body_name, ring_name, x, y, z, theta_deg
                FROM icy_quadrants
                WHERE score_version=? AND cohort_name=? AND quadrant=?
                ORDER BY (moi_metric IS NULL) ASC, moi_metric DESC, ring_id ASC
                """,
                (score_version, cohort_name, quadrant),
            ).fetchall()
            path = out_dir / f"quadrant_{quadrant}.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["rank", "ring_id", "moi_metric", "system_name", "body_name", "ring_name", "x", "y", "z", "theta_deg"]
                )
                for row in rows:
                    writer.writerow(
                        [
                            row[0],
                            row[1],
                            "" if row[2] is None else f"{float(row[2]):.6f}",
                            row[3] or "",
                            row[4] or "",
                            row[5] or "",
                            f"{float(row[6]):.6f}",
                            f"{float(row[7]):.6f}",
                            f"{float(row[8]):.6f}",
                            f"{float(row[9]):.6f}",
                        ]
                    )
            counts[quadrant] = len(rows)
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export per-quadrant CSV lists for IcyCore.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    counts = export_quadrant_lists(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        out_dir=Path(args.out_dir),
    )
    for quadrant in sorted(counts):
        print(f"{quadrant}: {counts[quadrant]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
