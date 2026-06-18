from __future__ import annotations

import argparse
import csv
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from scripts.rings.metric_resolver import resolve_moi_metric
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_THRESHOLDS = "2000"
DEFAULT_OUT_DIR = Path("data/ring_hunter_library/icycore_clusters")
DEFAULT_ALGO_VERSION = "cluster_cc_v1"


@dataclass(frozen=True)
class Point:
    ring_id: str
    rank: int
    x: float
    y: float
    z: float
    system_name: str | None
    body_name: str | None
    ring_name: str | None
    moi_metric: float | None


@dataclass(frozen=True)
class ClusterStats:
    cluster_id: str
    size: int
    centroid_x: float
    centroid_y: float
    centroid_z: float
    radius_ly: float
    moi_max: float | None
    moi_median: float | None
    min_ring_id: str


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _cohort_rank_column(conn: sqlite3.Connection) -> str:
    cols = _table_columns(conn, "cohort_members")
    if "rank" in cols:
        return "rank"
    if "rank_in_cohort" in cols:
        return "rank_in_cohort"
    raise RuntimeError("cohort_members must have rank or rank_in_cohort.")


def parse_thresholds(raw: str) -> list[float]:
    values: list[float] = []
    for part in raw.split(","):
        value = float(part.strip())
        if value <= 0:
            raise ValueError("Thresholds must be positive.")
        values.append(value)
    if not values:
        raise ValueError("At least one threshold is required.")
    return values


def _quantile_floor(values: list[int], q: float) -> int:
    if not values:
        return 0
    idx = int(math.floor((len(values) - 1) * q))
    return values[idx]


def load_points(
    conn: sqlite3.Connection,
    score_version: str,
    cohort_name: str,
    moi_metric: str,
    limit: int | None,
) -> tuple[list[Point], int]:
    rank_col = _cohort_rank_column(conn)
    limit_clause = " LIMIT ?" if limit is not None else ""
    params: list[object] = [score_version, cohort_name, score_version]
    if limit is not None:
        params.append(limit)
    rows = conn.execute(
        f"""
        SELECT
            cm.ring_id,
            cm.{rank_col},
            r.x, r.y, r.z,
            r.system_name, r.body_name, r.ring_name,
            s.{moi_metric}
        FROM cohort_members cm
        JOIN rings_raw r ON r.ring_id = cm.ring_id
        LEFT JOIN rings_scored s ON s.ring_id = cm.ring_id AND s.score_version = ?
        WHERE cm.score_version=? AND cm.cohort_name=?
        ORDER BY cm.{rank_col} ASC, cm.ring_id ASC
        {limit_clause}
        """,
        (score_version, score_version, cohort_name, *( [limit] if limit is not None else [] )),
    ).fetchall()
    points: list[Point] = []
    skipped_missing_coords = 0
    for row in rows:
        if row[2] is None or row[3] is None or row[4] is None:
            skipped_missing_coords += 1
            continue
        points.append(
            Point(
                ring_id=row[0],
                rank=int(row[1]),
                x=float(row[2]),
                y=float(row[3]),
                z=float(row[4]),
                system_name=row[5],
                body_name=row[6],
                ring_name=row[7],
                moi_metric=None if row[8] is None else float(row[8]),
            )
        )
    return points, skipped_missing_coords


def build_components(points: list[Point], threshold: float) -> list[list[int]]:
    n = len(points)
    uf = UnionFind(n)
    threshold2 = threshold * threshold
    for i in range(n - 1):
        pi = points[i]
        for j in range(i + 1, n):
            pj = points[j]
            dx = pi.x - pj.x
            dy = pi.y - pj.y
            dz = pi.z - pj.z
            d2 = dx * dx + dy * dy + dz * dz
            if d2 <= threshold2:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        root = uf.find(idx)
        groups.setdefault(root, []).append(idx)
    return list(groups.values())


def _cluster_sort_key(points: list[Point], member_idxs: list[int]) -> tuple[int, float, str]:
    size = len(member_idxs)
    max_moi = max(
        (points[idx].moi_metric for idx in member_idxs if points[idx].moi_metric is not None),
        default=float("-inf"),
    )
    min_ring_id = min(points[idx].ring_id for idx in member_idxs)
    return (-size, -max_moi, min_ring_id)


def assign_cluster_ids(points: list[Point], components: list[list[int]], threshold: float) -> list[tuple[str, list[int]]]:
    ordered = sorted(components, key=lambda idxs: _cluster_sort_key(points, idxs))
    out: list[tuple[str, list[int]]] = []
    for idx, member_idxs in enumerate(ordered, start=1):
        cluster_id = f"D{int(threshold)}_C{idx:03d}"
        out.append((cluster_id, sorted(member_idxs, key=lambda i: (points[i].rank, points[i].ring_id))))
    return out


def compute_cluster_stats(cluster_id: str, points: list[Point], member_idxs: list[int]) -> ClusterStats:
    members = [points[idx] for idx in member_idxs]
    size = len(members)
    cx = sum(p.x for p in members) / size
    cy = sum(p.y for p in members) / size
    cz = sum(p.z for p in members) / size
    radius = 0.0
    for p in members:
        d = math.sqrt((p.x - cx) ** 2 + (p.y - cy) ** 2 + (p.z - cz) ** 2)
        if d > radius:
            radius = d
    moi_values = sorted(p.moi_metric for p in members if p.moi_metric is not None)
    moi_max = moi_values[-1] if moi_values else None
    moi_med = median(moi_values) if moi_values else None
    min_ring_id = min(p.ring_id for p in members)
    return ClusterStats(
        cluster_id=cluster_id,
        size=size,
        centroid_x=cx,
        centroid_y=cy,
        centroid_z=cz,
        radius_ly=radius,
        moi_max=moi_max,
        moi_median=moi_med,
        min_ring_id=min_ring_id,
    )


def _fmt_opt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def write_threshold_outputs(
    out_dir: Path,
    threshold: float,
    clusters: list[ClusterStats],
    points: list[Point],
    cluster_members: list[tuple[str, list[int]]],
    summary: dict[str, object],
) -> None:
    d_label = int(threshold)
    clusters_csv = out_dir / f"clusters_D{d_label}.csv"
    members_csv = out_dir / f"members_D{d_label}.csv"
    summary_md = out_dir / f"summary_D{d_label}.md"

    with clusters_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "cluster_id",
                "size",
                "centroid_x",
                "centroid_y",
                "centroid_z",
                "radius_ly",
                "moi_max",
                "moi_median",
                "min_ring_id",
            ]
        )
        for c in clusters:
            writer.writerow(
                [
                    c.cluster_id,
                    c.size,
                    f"{c.centroid_x:.6f}",
                    f"{c.centroid_y:.6f}",
                    f"{c.centroid_z:.6f}",
                    f"{c.radius_ly:.6f}",
                    _fmt_opt(c.moi_max),
                    _fmt_opt(c.moi_median),
                    c.min_ring_id,
                ]
            )

    with members_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "cluster_id",
                "ring_id",
                "moi_metric",
                "system_name",
                "body_name",
                "ring_name",
                "x",
                "y",
                "z",
                "rank",
            ]
        )
        for cluster_id, member_idxs in cluster_members:
            for idx in member_idxs:
                p = points[idx]
                writer.writerow(
                    [
                        cluster_id,
                        p.ring_id,
                        _fmt_opt(p.moi_metric),
                        p.system_name or "",
                        p.body_name or "",
                        p.ring_name or "",
                        f"{p.x:.6f}",
                        f"{p.y:.6f}",
                        f"{p.z:.6f}",
                        p.rank,
                    ]
                )

    lines = [
        f"# IcyCore Cluster Summary D={d_label}",
        "",
        f"- n_points: {summary['n_points']}",
        f"- skipped_missing_coords: {summary['skipped_missing_coords']}",
        f"- num_clusters: {summary['num_clusters']}",
        f"- num_singletons: {summary['num_singletons']}",
        f"- largest_cluster_size: {summary['largest_cluster_size']}",
        f"- p50_cluster_size: {summary['p50_cluster_size']}",
        f"- p90_cluster_size: {summary['p90_cluster_size']}",
        f"- max_radius_ly: {summary['max_radius_ly']:.6f}",
        f"- median_radius_ly: {summary['median_radius_ly']:.6f}",
        "",
    ]
    summary_md.write_text("\n".join(lines), encoding="utf-8")


def run_clustering(
    db_path: Path,
    score_version: str = DEFAULT_SCORE_VERSION,
    cohort_name: str = DEFAULT_COHORT_NAME,
    moi_metric: str | None = None,
    thresholds: list[float] | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
    dry_run: bool = False,
    limit: int | None = None,
    algo_version: str = DEFAULT_ALGO_VERSION,
) -> list[dict[str, object]]:
    thresholds = thresholds or parse_thresholds(DEFAULT_THRESHOLDS)
    with closing(sqlite3.connect(db_path)) as conn:
        metric = resolve_moi_metric(conn, preferred=moi_metric)
        points, skipped = load_points(conn, score_version, cohort_name, metric, limit)

    out_dir.mkdir(parents=True, exist_ok=True) if not dry_run else None
    sweep_rows: list[dict[str, object]] = []
    for threshold in thresholds:
        components = build_components(points, threshold)
        cluster_members = assign_cluster_ids(points, components, threshold)
        clusters = [compute_cluster_stats(cid, points, idxs) for cid, idxs in cluster_members]
        sizes = sorted(c.size for c in clusters)
        radii = sorted(c.radius_ly for c in clusters)
        summary = {
            "threshold": int(threshold),
            "algo_version": algo_version,
            "moi_metric": metric,
            "n_points": len(points),
            "skipped_missing_coords": skipped,
            "num_clusters": len(clusters),
            "num_singletons": sum(1 for c in clusters if c.size == 1),
            "largest_cluster_size": max(sizes) if sizes else 0,
            "p50_cluster_size": _quantile_floor(sizes, 0.50),
            "p90_cluster_size": _quantile_floor(sizes, 0.90),
            "max_radius_ly": max(radii) if radii else 0.0,
            "median_radius_ly": median(radii) if radii else 0.0,
        }
        sweep_rows.append(summary)
        if not dry_run:
            write_threshold_outputs(out_dir, threshold, clusters, points, cluster_members, summary)

    if not dry_run:
        sweep_path = out_dir / "sweep_summary.csv"
        with sweep_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "threshold",
                    "algo_version",
                    "moi_metric",
                    "n_points",
                    "skipped_missing_coords",
                    "num_clusters",
                    "num_singletons",
                    "largest_cluster_size",
                    "p50_cluster_size",
                    "p90_cluster_size",
                    "max_radius_ly",
                    "median_radius_ly",
                ]
            )
            for row in sweep_rows:
                writer.writerow(
                    [
                        row["threshold"],
                        row["algo_version"],
                        row["moi_metric"],
                        row["n_points"],
                        row["skipped_missing_coords"],
                        row["num_clusters"],
                        row["num_singletons"],
                        row["largest_cluster_size"],
                        row["p50_cluster_size"],
                        row["p90_cluster_size"],
                        f"{float(row['max_radius_ly']):.6f}",
                        f"{float(row['median_radius_ly']):.6f}",
                    ]
                )
    return sweep_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build IcyCore connected-component clusters for distance thresholds.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME)
    parser.add_argument("--moi-metric")
    parser.add_argument("--thresholds", default=DEFAULT_THRESHOLDS)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--algo-version", default=DEFAULT_ALGO_VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sweep = run_clustering(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        moi_metric=args.moi_metric,
        thresholds=parse_thresholds(args.thresholds),
        out_dir=Path(args.out_dir),
        dry_run=args.dry_run,
        limit=args.limit,
        algo_version=args.algo_version,
    )
    for row in sweep:
        print(
            "D={threshold} clusters={num_clusters} singletons={num_singletons} "
            "largest={largest_cluster_size} p50={p50_cluster_size} p90={p90_cluster_size}".format(**row)
        )
    if args.dry_run:
        print("Dry run: no files written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
