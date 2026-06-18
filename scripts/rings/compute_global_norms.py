from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from scripts.rings.metric_resolver import (
    get_table_columns,
    resolve_moi_metric,
    sanitize_identifier_for_index,
)

DEFAULT_METRICS = "moi0"
DEFAULT_QUANTILES = "0.95,0.99,0.995,0.999"
DEFAULT_ALGO_VERSION = "norm_v1"
DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_BATCH_SIZE = 10000
CANONICAL_QUANTILES = {
    0.95: "p95",
    0.99: "p99",
    0.995: "p99_5",
    0.999: "p99_9",
}
logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_csv_list(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_quantiles(raw: str) -> list[float]:
    values: list[float] = []
    for token in parse_csv_list(raw):
        q = float(token)
        if q < 0.0 or q > 1.0:
            raise ValueError(f"Quantile out of range [0,1]: {q}")
        values.append(q)
    if not values:
        raise ValueError("At least one quantile is required.")
    return values


def ensure_global_norms_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name='global_norms'
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "Missing Phase 3 table: global_norms. Run python -m scripts.rings.apply_schema --db <PATH>."
        )


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return get_table_columns(conn, table_name)


def ensure_required_scored_columns(conn: sqlite3.Connection, metrics: Iterable[str]) -> None:
    cols = table_columns(conn, "rings_scored")
    required = {"score_version", "ring_type", "ring_id", *metrics}
    missing = sorted(name for name in required if name not in cols)
    if missing:
        raise RuntimeError(f"Missing required rings_scored columns: {', '.join(missing)}")


def detect_ring_types(conn: sqlite3.Connection, score_version: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT ring_type
        FROM rings_scored
        WHERE score_version=? AND ring_type IS NOT NULL
        ORDER BY ring_type ASC
        """,
        (score_version,),
    ).fetchall()
    return [row[0] for row in rows]


def resolve_metrics_for_run(
    conn: sqlite3.Connection,
    metrics: list[str],
    allow_default_auto_resolve: bool,
    preferred_moi_metric: str | None,
) -> tuple[list[str], str]:
    cols = table_columns(conn, "rings_scored")
    resolved_moi_metric = resolve_moi_metric(conn, preferred=preferred_moi_metric)
    if allow_default_auto_resolve and metrics == [DEFAULT_METRICS] and DEFAULT_METRICS not in cols:
        return [resolved_moi_metric], resolved_moi_metric
    return metrics, resolved_moi_metric


def warn_missing_quantile_index(
    conn: sqlite3.Connection,
    metric: str,
    resolved_moi_metric: str,
    enabled: bool,
    emit: Callable[[str], None] | None = None,
) -> None:
    if not enabled:
        return
    if metric != resolved_moi_metric:
        return
    metric_token = sanitize_identifier_for_index(metric)
    expected_idx = f"idx_scored_v_rt_{metric_token}_asc"
    row = conn.execute(
        f"""
        SELECT 1
        FROM sqlite_master
        WHERE type='index' AND name='{expected_idx}'
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        message = f"WARNING: {expected_idx} missing; quantile queries for {metric} may be slow."
        if emit is not None:
            emit(message)
        else:
            logger.warning(message)


def compute_moments_welford(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    metric: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, float, float, float | None, float | None]:
    cursor = conn.execute(
        f"""
        SELECT {metric}
        FROM rings_scored
        WHERE score_version=? AND ring_type=? AND {metric} IS NOT NULL
        """,
        (score_version, ring_type),
    )
    n = 0
    mean = 0.0
    m2 = 0.0
    min_value: float | None = None
    max_value: float | None = None

    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        for row in rows:
            x = float(row[0])
            n += 1
            delta = x - mean
            mean += delta / n
            delta2 = x - mean
            m2 += delta * delta2
            if min_value is None or x < min_value:
                min_value = x
            if max_value is None or x > max_value:
                max_value = x

    stddev = 0.0 if n < 2 else math.sqrt(m2 / n)
    return n, mean if n else 0.0, stddev, min_value, max_value


def fetch_exact_quantile(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    metric: str,
    n: int,
    q: float,
) -> float | None:
    if n <= 0:
        return None
    offset = int(math.floor((n - 1) * q))
    row = conn.execute(
        f"""
        SELECT {metric}
        FROM rings_scored
        WHERE score_version=? AND ring_type=? AND {metric} IS NOT NULL
        ORDER BY {metric} ASC, ring_id ASC
        LIMIT 1 OFFSET ?
        """,
        (score_version, ring_type, offset),
    ).fetchone()
    return None if row is None else float(row[0])


def upsert_global_norm(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    metric: str,
    n: int,
    mean: float,
    stddev: float,
    min_value: float | None,
    max_value: float | None,
    p95: float | None,
    p99: float | None,
    p99_5: float | None,
    p99_9: float | None,
    algo_version: str,
    notes: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO global_norms (
            score_version,
            ring_type,
            metric,
            n,
            mean,
            stddev,
            median,
            mad,
            p95,
            p99,
            p99_5,
            p99_9,
            min_value,
            max_value,
            computed_at,
            algo_version,
            notes
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            score_version,
            ring_type,
            metric,
            n,
            mean,
            stddev,
            p95,
            p99,
            p99_5,
            p99_9,
            min_value,
            max_value,
            utc_now_iso(),
            algo_version,
            notes,
        ),
    )


def compute_global_norms(
    conn: sqlite3.Connection,
    score_version: str,
    ring_types: list[str] | None,
    metrics: list[str],
    quantiles: list[float],
    algo_version: str,
    dry_run: bool = False,
    warn_missing_asc_index: bool = True,
    allow_default_auto_resolve: bool = False,
    preferred_moi_metric: str | None = None,
    emit: Callable[[str], None] | None = None,
) -> int:
    ensure_global_norms_table(conn)
    run_metrics, resolved_moi_metric = resolve_metrics_for_run(
        conn=conn,
        metrics=metrics,
        allow_default_auto_resolve=allow_default_auto_resolve,
        preferred_moi_metric=preferred_moi_metric,
    )
    ensure_required_scored_columns(conn, run_metrics)
    target_ring_types = ring_types if ring_types else detect_ring_types(conn, score_version)
    upserted = 0

    if not dry_run:
        conn.execute("BEGIN")
    try:
        for ring_type in target_ring_types:
            for metric in run_metrics:
                warn_missing_quantile_index(
                    conn,
                    metric,
                    resolved_moi_metric,
                    warn_missing_asc_index,
                    emit=emit,
                )
                n, mean, stddev, min_value, max_value = compute_moments_welford(
                    conn=conn,
                    score_version=score_version,
                    ring_type=ring_type,
                    metric=metric,
                )
                if n == 0:
                    message = f"SKIP ring_type={ring_type} metric={metric} n=0 (no non-null values)"
                    if emit is not None:
                        emit(message)
                    else:
                        logger.info(message)
                    continue

                quantile_values = {q: fetch_exact_quantile(conn, score_version, ring_type, metric, n, q) for q in quantiles}
                p95 = quantile_values.get(0.95)
                p99 = quantile_values.get(0.99)
                p99_5 = quantile_values.get(0.995)
                p99_9 = quantile_values.get(0.999)
                notes = "stddev=pop; quantiles exact; offsets=floor((n-1)*q)"
                if any(q not in CANONICAL_QUANTILES for q in quantiles):
                    notes += "; noncanonical quantiles computed but not persisted"

                message = (
                    f"{ring_type} {metric} n={n} mean={mean} stddev={stddev} "
                    f"min={min_value} max={max_value} p99={p99} p99_9={p99_9}"
                )
                if emit is not None:
                    emit(message)
                else:
                    logger.info(message)

                if not dry_run:
                    upsert_global_norm(
                        conn=conn,
                        score_version=score_version,
                        ring_type=ring_type,
                        metric=metric,
                        n=n,
                        mean=mean,
                        stddev=stddev,
                        min_value=min_value,
                        max_value=max_value,
                        p95=p95,
                        p99=p99,
                        p99_5=p99_5,
                        p99_9=p99_9,
                        algo_version=algo_version,
                        notes=notes,
                    )
                    upserted += 1
        if not dry_run:
            conn.commit()
    except Exception:
        if not dry_run:
            conn.rollback()
        raise
    return upserted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute Phase 3 global norms from rings_scored.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION, help="Score version filter.")
    parser.add_argument("--ring-types", default=None, help="Comma-separated ring types; auto-detect if omitted.")
    parser.add_argument("--metrics", default=DEFAULT_METRICS, help="Comma-separated numeric metric columns.")
    parser.add_argument("--moi-metric", default=None, help="Optional preferred MOI metric for default auto-resolution.")
    parser.add_argument(
        "--algo-version",
        default=DEFAULT_ALGO_VERSION,
        help="Algorithm version value stored in global_norms.",
    )
    parser.add_argument(
        "--quantiles",
        default=DEFAULT_QUANTILES,
        help="Comma-separated quantiles (0..1). Canonical values populate p95/p99/p99_5/p99_9.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute and print without writing.")
    parser.add_argument(
        "--warn-missing-asc-index",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Warn if likely ASC index is missing for quantile ordering.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-ring-type progress output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    raw_argv = argv if argv is not None else sys.argv[1:]
    metrics_explicit = "--metrics" in raw_argv
    metrics = parse_csv_list(args.metrics)
    if not metrics:
        print("Error: at least one metric is required.")
        return 2
    ring_types = parse_csv_list(args.ring_types) if args.ring_types is not None else None
    try:
        quantiles = parse_quantiles(args.quantiles)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2

    try:
        with closing(sqlite3.connect(db_path)) as conn:
            upserted = compute_global_norms(
                conn=conn,
                score_version=args.score_version,
                ring_types=ring_types,
                metrics=metrics,
                quantiles=quantiles,
                algo_version=args.algo_version,
                dry_run=args.dry_run,
                warn_missing_asc_index=args.warn_missing_asc_index,
                allow_default_auto_resolve=not metrics_explicit,
                preferred_moi_metric=args.moi_metric,
                emit=None if args.quiet else print,
            )
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    print(f"total_rows_upserted={upserted}")
    if args.dry_run:
        print("dry-run enabled: no database writes were performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
