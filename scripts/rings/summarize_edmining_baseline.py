from __future__ import annotations

import argparse
import math
import sqlite3
from pathlib import Path
from statistics import median
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_OUT = Path("docs/ring_hunter/edmining_baseline_summary.md")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _quantile_floor(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("values must be non-empty")
    idx = int(math.floor((len(values) - 1) * q))
    return values[idx]


def summarize(db_path: Path, score_version: str, out_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        if not _table_exists(conn, "edmining_best_matches"):
            raise RuntimeError("Missing table edmining_best_matches. Run report with --write-db first.")

        rows = conn.execute(
            """
            SELECT best_moi_final, best_percentile
            FROM edmining_best_matches
            WHERE score_version=?
              AND best_ring_type='Icy'
              AND best_moi_final IS NOT NULL
            ORDER BY best_moi_final ASC
            """,
            (score_version,),
        ).fetchall()

    values = [float(row[0]) for row in rows]
    percentiles = [row[1] for row in rows]

    bucket_counts = {
        "<0.5": 0,
        "0.5-0.8": 0,
        "0.8-0.95": 0,
        "0.95-0.99": 0,
        ">=0.99": 0,
    }
    for pct in percentiles:
        if pct is None:
            continue
        p = float(pct)
        if p < 0.5:
            bucket_counts["<0.5"] += 1
        elif p < 0.8:
            bucket_counts["0.5-0.8"] += 1
        elif p < 0.95:
            bucket_counts["0.8-0.95"] += 1
        elif p < 0.99:
            bucket_counts["0.95-0.99"] += 1
        else:
            bucket_counts[">=0.99"] += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# EDMining Baseline Summary (Icy Best Matches)")
    lines.append("")
    lines.append(f"- DB: `{db_path}`")
    lines.append(f"- score_version: `{score_version}`")
    lines.append("")
    lines.append("## Distribution Stats")
    if not values:
        lines.append("- count: 0")
    else:
        lines.append(f"- count: {len(values)}")
        lines.append(f"- min: {values[0]:.6f}")
        lines.append(f"- median: {median(values):.6f}")
        lines.append(f"- p90: {_quantile_floor(values, 0.90):.6f}")
        lines.append(f"- p99: {_quantile_floor(values, 0.99):.6f}")
        lines.append(f"- max: {values[-1]:.6f}")
    lines.append("")
    lines.append("## Percentile Buckets")
    lines.append(f"- <0.5: {bucket_counts['<0.5']}")
    lines.append(f"- 0.5-0.8: {bucket_counts['0.5-0.8']}")
    lines.append(f"- 0.8-0.95: {bucket_counts['0.8-0.95']}")
    lines.append(f"- 0.95-0.99: {bucket_counts['0.95-0.99']}")
    lines.append(f"- >=0.99: {bucket_counts['>=0.99']}")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize EDMining baseline scores from persisted best matches.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summarize(Path(args.db), args.score_version, Path(args.out))
    print(f"Wrote baseline summary: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
