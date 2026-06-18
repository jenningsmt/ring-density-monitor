from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Callable

from scripts.rings.apply_schema import DEFAULT_SCHEMA, apply_schema_file
from scripts.rings.build_tail_indexes import build_tail_indexes
from scripts.rings.compute_cohort_cutoffs import compute_and_materialize
from scripts.rings.compute_global_norms import (
    DEFAULT_QUANTILES,
    compute_global_norms,
    parse_csv_list,
    parse_quantiles,
)


def run_phase3(
    db_path: Path,
    score_version: str = "moi_v1",
    metrics: list[str] | str = "moi0",
    ring_types: list[str] | str | None = None,
    moi_metric: str | None = None,
    icy_n: int = 1000,
    met_n: int = 2000,
    with_asc: bool = False,
    algo_norm: str = "norm_v1",
    algo_cutoff: str = "cutoff_v1",
    dry_run: bool = False,
    metrics_were_explicit: bool = False,
    emit: Callable[[str], None] | None = None,
) -> dict[str, object]:
    metrics_list = parse_csv_list(metrics) if isinstance(metrics, str) else list(metrics)
    ring_types_list: list[str] | None
    if ring_types is None:
        ring_types_list = None
    elif isinstance(ring_types, str):
        ring_types_list = parse_csv_list(ring_types)
    else:
        ring_types_list = list(ring_types)
    if moi_metric is not None and not metrics_were_explicit:
        metrics_list = [moi_metric]

    # a) apply schema
    apply_schema_file(db_path, DEFAULT_SCHEMA)

    # b) build indexes
    with closing(sqlite3.connect(db_path)) as conn:
        resolved_index_metric, index_names = build_tail_indexes(conn, with_asc=with_asc, moi_metric=moi_metric)
        conn.commit()

    # c) compute norms
    with closing(sqlite3.connect(db_path)) as conn:
        norms_upserted = compute_global_norms(
            conn=conn,
            score_version=score_version,
            ring_types=ring_types_list,
            metrics=metrics_list,
            quantiles=parse_quantiles(DEFAULT_QUANTILES),
            algo_version=algo_norm,
            dry_run=dry_run,
            allow_default_auto_resolve=not metrics_were_explicit,
            preferred_moi_metric=moi_metric,
        )

    # d) compute cutoffs twice
    with closing(sqlite3.connect(db_path)) as conn:
        icy_theta = compute_and_materialize(
            conn=conn,
            score_version=score_version,
            cohort_name="IcyCore",
            ring_type="Icy",
            target_n=icy_n,
            algo_version=algo_cutoff,
            moi_metric=moi_metric,
            dry_run=dry_run,
        )
        met_theta = compute_and_materialize(
            conn=conn,
            score_version=score_version,
            cohort_name="MetTail",
            ring_type="Metallic",
            target_n=met_n,
            algo_version=algo_cutoff,
            moi_metric=moi_metric,
            dry_run=dry_run,
        )

    # Final summary
    with closing(sqlite3.connect(db_path)) as conn:
        global_norms_count = conn.execute(
            "SELECT COUNT(*) FROM global_norms WHERE score_version=?",
            (score_version,),
        ).fetchone()[0]
        cutoff_rows = conn.execute(
            """
            SELECT cohort_name, theta_value, theta_ring_id
            FROM cohort_cutoffs
            WHERE score_version=? AND cohort_name IN ('IcyCore', 'MetTail')
            ORDER BY cohort_name
            """,
            (score_version,),
        ).fetchall()
        member_rows = conn.execute(
            """
            SELECT cohort_name, COUNT(*)
            FROM cohort_members
            WHERE score_version=? AND cohort_name IN ('IcyCore', 'MetTail')
            GROUP BY cohort_name
            ORDER BY cohort_name
            """,
            (score_version,),
        ).fetchall()

    if emit is not None:
        emit(f"global_norms_count score_version={score_version} count={global_norms_count}")
        for row in cutoff_rows:
            emit(f"cutoff cohort_name={row[0]} theta_value={row[1]} theta_ring_id={row[2]}")
        for row in member_rows:
            emit(f"cohort_members cohort_name={row[0]} count={row[1]}")

    return {
        "indexes": index_names,
        "resolved_index_metric": resolved_index_metric,
        "norms_upserted": norms_upserted,
        "icy_theta": icy_theta,
        "met_theta": met_theta,
        "global_norms_count": global_norms_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 3 driver: schema + indexes + norms + cohorts.")
    parser.add_argument("--db", required=True, help="Path to rings_master SQLite database.")
    parser.add_argument("--score-version", default="moi_v1", help="Score version to process.")
    parser.add_argument("--metrics", default="moi0", help="Comma-separated metric list.")
    parser.add_argument("--ring-types", default=None, help="Optional comma-separated ring types.")
    parser.add_argument("--moi-metric", default=None, help="Optional MOI metric override/resolution hint.")
    parser.add_argument("--icy-n", type=int, default=1000, help="Target N for IcyCore.")
    parser.add_argument("--met-n", type=int, default=2000, help="Target N for MetTail.")
    parser.add_argument("--with-asc", action="store_true", help="Build ASC moi0 index too.")
    parser.add_argument("--algo-norm", default="norm_v1", help="Algo version for global norms.")
    parser.add_argument("--algo-cutoff", default="cutoff_v1", help="Algo version for cohort cutoffs.")
    parser.add_argument("--dry-run", action="store_true", help="Run norms/cutoffs in dry-run mode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_argv = argv if argv is not None else sys.argv[1:]
    metrics_explicit = "--metrics" in raw_argv
    metrics_arg: list[str] | str = args.metrics
    if args.moi_metric is not None and not metrics_explicit:
        metrics_arg = args.moi_metric
    try:
        run_phase3(
            db_path=Path(args.db),
            score_version=args.score_version,
            metrics=metrics_arg,
            ring_types=args.ring_types,
            moi_metric=args.moi_metric,
            icy_n=args.icy_n,
            met_n=args.met_n,
            with_asc=args.with_asc,
            algo_norm=args.algo_norm,
            algo_cutoff=args.algo_cutoff,
            dry_run=args.dry_run,
            metrics_were_explicit=metrics_explicit,
            emit=print,
        )
    except Exception as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
