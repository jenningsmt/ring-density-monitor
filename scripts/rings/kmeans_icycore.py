from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from scripts.rings.metric_resolver import resolve_moi_metric
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_K_LIST = "12,13,14"
DEFAULT_MAX_ITER = 50
DEFAULT_TOL = 1e-6
DEFAULT_OUT_DIR = Path("data/ring_hunter_library/icycore_kmeans")
EPS = 1e-12


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
    moi: float | None


@dataclass(frozen=True)
class ClusterSummary:
    cluster_id: str
    idx: int
    size: int
    centroid_x: float
    centroid_y: float
    centroid_z: float
    radius_ly: float
    p90_radius_ly: float
    moi_max: float | None
    moi_median: float | None
    min_ring_id: str
    member_indices: list[int]
    member_distances: list[float]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _cohort_rank_column(conn: sqlite3.Connection) -> str:
    cols = _table_columns(conn, "cohort_members")
    if "rank" in cols:
        return "rank"
    if "rank_in_cohort" in cols:
        return "rank_in_cohort"
    raise RuntimeError("cohort_members must include rank or rank_in_cohort.")


def parse_k_list(raw: str) -> list[int]:
    out: list[int] = []
    for part in raw.split(","):
        k = int(part.strip())
        if k <= 0:
            raise ValueError("k values must be positive integers.")
        out.append(k)
    if not out:
        raise ValueError("k-list must include at least one value.")
    return out


def _quantile_floor(values: list[float], q: float) -> float:
    if not values:
        return 0.0
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
    rows = conn.execute(
        f"""
        SELECT
            cm.ring_id,
            cm.{rank_col},
            rr.x, rr.y, rr.z,
            rr.system_name, rr.body_name, rr.ring_name,
            rs.{moi_metric}
        FROM cohort_members cm
        JOIN rings_raw rr ON rr.ring_id = cm.ring_id
        LEFT JOIN rings_scored rs
            ON rs.ring_id = cm.ring_id AND rs.score_version = cm.score_version
        WHERE cm.score_version=? AND cm.cohort_name=?
        ORDER BY cm.{rank_col} ASC, cm.ring_id ASC
        {limit_clause}
        """,
        (score_version, cohort_name, *( [limit] if limit is not None else [] )),
    ).fetchall()
    points: list[Point] = []
    skipped = 0
    for row in rows:
        if row[2] is None or row[3] is None or row[4] is None:
            skipped += 1
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
                moi=None if row[8] is None else float(row[8]),
            )
        )
    return points, skipped


def initialize_centroids(points: list[Point], k: int) -> list[tuple[float, float, float]]:
    n = len(points)
    if k > n:
        raise RuntimeError(f"k={k} cannot exceed number of points n={n}.")
    if k == 1:
        p = points[0]
        return [(p.x, p.y, p.z)]

    used: set[int] = set()
    centroids: list[tuple[float, float, float]] = []
    for i in range(k):
        idx = int(math.floor(i * (n - 1) / (k - 1)))
        while idx in used:
            idx = (idx + 1) % n
        used.add(idx)
        p = points[idx]
        centroids.append((p.x, p.y, p.z))
    return centroids


def assign_points(points: list[Point], centroids: list[tuple[float, float, float]]) -> tuple[list[int], list[float]]:
    assignments: list[int] = [0] * len(points)
    dist2s: list[float] = [0.0] * len(points)
    for i, p in enumerate(points):
        best_c = 0
        cx, cy, cz = centroids[0]
        best_d2 = (p.x - cx) ** 2 + (p.y - cy) ** 2 + (p.z - cz) ** 2
        for c_idx in range(1, len(centroids)):
            cx, cy, cz = centroids[c_idx]
            d2 = (p.x - cx) ** 2 + (p.y - cy) ** 2 + (p.z - cz) ** 2
            if d2 < best_d2 - EPS:
                best_d2 = d2
                best_c = c_idx
            elif abs(d2 - best_d2) <= EPS and c_idx < best_c:
                best_c = c_idx
        assignments[i] = best_c
        dist2s[i] = best_d2
    return assignments, dist2s


def recompute_centroids(
    points: list[Point],
    centroids: list[tuple[float, float, float]],
    assignments: list[int],
    dist2s: list[float],
) -> list[tuple[float, float, float]]:
    k = len(centroids)
    sums = [[0.0, 0.0, 0.0, 0] for _ in range(k)]
    for idx, p in enumerate(points):
        c = assignments[idx]
        sums[c][0] += p.x
        sums[c][1] += p.y
        sums[c][2] += p.z
        sums[c][3] += 1

    new_centroids: list[tuple[float, float, float]] = list(centroids)
    for c in range(k):
        if sums[c][3] > 0:
            new_centroids[c] = (
                sums[c][0] / sums[c][3],
                sums[c][1] / sums[c][3],
                sums[c][2] / sums[c][3],
            )

    empty_clusters = [c for c in range(k) if sums[c][3] == 0]
    if empty_clusters:
        worst_points = sorted(
            range(len(points)),
            key=lambda i: (-dist2s[i], points[i].ring_id),
        )
        used_points: set[int] = set()
        for c in empty_clusters:
            for idx in worst_points:
                if idx in used_points:
                    continue
                used_points.add(idx)
                p = points[idx]
                new_centroids[c] = (p.x, p.y, p.z)
                break
    return new_centroids


def centroid_shift(old: list[tuple[float, float, float]], new: list[tuple[float, float, float]]) -> float:
    max_shift = 0.0
    for (ox, oy, oz), (nx, ny, nz) in zip(old, new):
        s = math.sqrt((ox - nx) ** 2 + (oy - ny) ** 2 + (oz - nz) ** 2)
        if s > max_shift:
            max_shift = s
    return max_shift


def has_empty_clusters(assignments: list[int], k: int) -> bool:
    counts = [0] * k
    for a in assignments:
        counts[a] += 1
    return any(c == 0 for c in counts)


def run_kmeans(points: list[Point], k: int, max_iter: int, tol: float) -> tuple[list[int], list[tuple[float, float, float]], int]:
    centroids = initialize_centroids(points, k)
    iterations = 0
    for _ in range(max_iter):
        iterations += 1
        assignments, dist2s = assign_points(points, centroids)
        new_centroids = recompute_centroids(points, centroids, assignments, dist2s)
        shift = centroid_shift(centroids, new_centroids)
        centroids = new_centroids
        if shift < tol and not has_empty_clusters(assignments, k):
            break

    assignments, _ = assign_points(points, centroids)
    if has_empty_clusters(assignments, k):
        raise RuntimeError("K-means ended with empty clusters; check data or k.")
    return assignments, centroids, iterations


def build_cluster_summaries(
    points: list[Point],
    assignments: list[int],
    centroids: list[tuple[float, float, float]],
    k: int,
) -> list[ClusterSummary]:
    members_by_cluster: dict[int, list[int]] = {i: [] for i in range(k)}
    for idx, c in enumerate(assignments):
        members_by_cluster[c].append(idx)

    cluster_rows: list[ClusterSummary] = []
    for c in range(k):
        member_idxs = sorted(members_by_cluster[c], key=lambda i: (points[i].rank, points[i].ring_id))
        cx, cy, cz = centroids[c]
        dists = sorted(
            math.sqrt((points[i].x - cx) ** 2 + (points[i].y - cy) ** 2 + (points[i].z - cz) ** 2)
            for i in member_idxs
        )
        radius = dists[-1] if dists else 0.0
        p90 = _quantile_floor(dists, 0.90) if dists else 0.0
        moi_values = sorted(points[i].moi for i in member_idxs if points[i].moi is not None)
        moi_max = moi_values[-1] if moi_values else None
        moi_med = median(moi_values) if moi_values else None
        min_ring_id = min(points[i].ring_id for i in member_idxs)
        cluster_rows.append(
            ClusterSummary(
                cluster_id="",
                idx=c,
                size=len(member_idxs),
                centroid_x=cx,
                centroid_y=cy,
                centroid_z=cz,
                radius_ly=radius,
                p90_radius_ly=p90,
                moi_max=moi_max,
                moi_median=moi_med,
                min_ring_id=min_ring_id,
                member_indices=member_idxs,
                member_distances=dists,
            )
        )

    ordered = sorted(
        cluster_rows,
        key=lambda c: (
            -c.size,
            -(c.moi_max if c.moi_max is not None else float("-inf")),
            c.min_ring_id,
        ),
    )
    out: list[ClusterSummary] = []
    for idx, c in enumerate(ordered, start=1):
        out.append(
            ClusterSummary(
                cluster_id=f"K{k}_C{idx:02d}",
                idx=c.idx,
                size=c.size,
                centroid_x=c.centroid_x,
                centroid_y=c.centroid_y,
                centroid_z=c.centroid_z,
                radius_ly=c.radius_ly,
                p90_radius_ly=c.p90_radius_ly,
                moi_max=c.moi_max,
                moi_median=c.moi_median,
                min_ring_id=c.min_ring_id,
                member_indices=c.member_indices,
                member_distances=c.member_distances,
            )
        )
    return out


def _fmt_opt(v: float | None) -> str:
    return "" if v is None else f"{v:.6f}"


def write_outputs_for_k(
    out_dir: Path,
    k: int,
    points: list[Point],
    clusters: list[ClusterSummary],
    summary: dict[str, object],
    anchor_xyz: tuple[float, float, float] | None,
) -> None:
    clusters_csv = out_dir / f"clusters_k{k}.csv"
    members_csv = out_dir / f"members_k{k}.csv"
    summary_md = out_dir / f"summary_k{k}.md"
    centroids_json = out_dir / f"centroids_k{k}.json"

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
                "p90_radius_ly",
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
                    f"{c.p90_radius_ly:.6f}",
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
                "rank",
                "moi",
                "system_name",
                "body_name",
                "ring_name",
                "x",
                "y",
                "z",
                "dist_to_centroid_ly",
            ]
        )
        for c in clusters:
            cx, cy, cz = c.centroid_x, c.centroid_y, c.centroid_z
            for idx in c.member_indices:
                p = points[idx]
                d = math.sqrt((p.x - cx) ** 2 + (p.y - cy) ** 2 + (p.z - cz) ** 2)
                writer.writerow(
                    [
                        c.cluster_id,
                        p.ring_id,
                        p.rank,
                        _fmt_opt(p.moi),
                        p.system_name or "",
                        p.body_name or "",
                        p.ring_name or "",
                        f"{p.x:.6f}",
                        f"{p.y:.6f}",
                        f"{p.z:.6f}",
                        f"{d:.6f}",
                    ]
                )

    lines = [
        f"# IcyCore K-Means Summary k={k}",
        "",
        f"- n_points: {summary['n_points']}",
        f"- skipped_missing_coords: {summary['skipped']}",
        f"- num_clusters: {summary['num_clusters']}",
        f"- size_min: {summary['size_min']}",
        f"- size_median: {summary['size_median']}",
        f"- size_max: {summary['size_max']}",
        f"- radius_max: {summary['radius_max']:.6f}",
        f"- radius_median: {summary['radius_median']:.6f}",
        f"- p90_radius: {summary['radius_p90']:.6f}",
        f"- total_within_2000ly_of_centroid: {summary['within_2000']}",
    ]
    if anchor_xyz is not None:
        lines.append(f"- anchor_xyz: ({anchor_xyz[0]:.6f}, {anchor_xyz[1]:.6f}, {anchor_xyz[2]:.6f})")
    lines.append(f"- best_k_suggestion: {summary['best_k_suggestion']}")
    lines.append("")
    summary_md.write_text("\n".join(lines), encoding="utf-8")

    centroids_payload = [
        {
            "cluster_id": c.cluster_id,
            "centroid_x": c.centroid_x,
            "centroid_y": c.centroid_y,
            "centroid_z": c.centroid_z,
            "size": c.size,
        }
        for c in clusters
    ]
    centroids_json.write_text(json.dumps(centroids_payload, indent=2), encoding="utf-8")


def run_kmeans_sweep(
    db_path: Path,
    score_version: str = DEFAULT_SCORE_VERSION,
    cohort_name: str = DEFAULT_COHORT_NAME,
    moi_metric: str | None = None,
    k_list: list[int] | None = None,
    max_iter: int = DEFAULT_MAX_ITER,
    tol: float = DEFAULT_TOL,
    out_dir: Path = DEFAULT_OUT_DIR,
    anchor_xyz: tuple[float, float, float] | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> list[dict[str, object]]:
    k_list = k_list or parse_k_list(DEFAULT_K_LIST)
    with closing(sqlite3.connect(db_path)) as conn:
        resolved_metric = resolve_moi_metric(conn, preferred=moi_metric)
        points, skipped = load_points(conn, score_version, cohort_name, resolved_metric, limit)

    if not points:
        raise RuntimeError("No points available for k-means clustering.")
    if max(k_list) > len(points):
        raise RuntimeError(f"max(k)={max(k_list)} exceeds point count n={len(points)}")

    out_dir.mkdir(parents=True, exist_ok=True) if not dry_run else None
    rows: list[dict[str, object]] = []
    per_k_payload: dict[int, tuple[list[ClusterSummary], dict[str, object]]] = {}
    for k in k_list:
        assignments, centroids, _iterations = run_kmeans(points, k, max_iter=max_iter, tol=tol)
        clusters = build_cluster_summaries(points, assignments, centroids, k)
        sizes = sorted(c.size for c in clusters)
        radii = sorted(c.radius_ly for c in clusters)
        within_2000 = sum(
            1
            for c in clusters
            for d in c.member_distances
            if d <= 2000.0 + EPS
        )
        size_min = sizes[0]
        size_median = _quantile_floor([float(s) for s in sizes], 0.50)
        size_max = sizes[-1]
        size_p90 = _quantile_floor([float(s) for s in sizes], 0.90)
        radius_median = _quantile_floor(radii, 0.50)
        radius_p90 = _quantile_floor(radii, 0.90)
        radius_max = radii[-1]
        ratio = float(size_max) / float(size_min) if size_min > 0 else float("inf")
        row = {
            "k": k,
            "n_points": len(points),
            "skipped": skipped,
            "size_min": size_min,
            "size_median": int(size_median),
            "size_max": size_max,
            "size_p90": int(size_p90),
            "radius_median": radius_median,
            "radius_p90": radius_p90,
            "radius_max": radius_max,
            "max_min_size_ratio": ratio,
            "num_clusters": len(clusters),
            "within_2000": within_2000,
            "moi_metric": resolved_metric,
        }
        rows.append(row)
        per_k_payload[k] = (clusters, row)

    best = min(
        rows,
        key=lambda r: (r["radius_max"], r["max_min_size_ratio"], r["radius_p90"], r["k"]),
    )
    best_k_suggestion = f"k={best['k']} (lowest radius_max, then best balance ratio)"
    for row in rows:
        row["best_k_suggestion"] = best_k_suggestion

    if not dry_run:
        for k in k_list:
            clusters, row = per_k_payload[k]
            row["best_k_suggestion"] = best_k_suggestion
            write_outputs_for_k(
                out_dir=out_dir,
                k=k,
                points=points,
                clusters=clusters,
                summary=row,
                anchor_xyz=anchor_xyz,
            )

        sweep_path = out_dir / "sweep_summary.csv"
        with sweep_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "k",
                    "n_points",
                    "skipped",
                    "size_min",
                    "size_median",
                    "size_max",
                    "size_p90",
                    "radius_median",
                    "radius_p90",
                    "radius_max",
                    "max_min_size_ratio",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row["k"],
                        row["n_points"],
                        row["skipped"],
                        row["size_min"],
                        row["size_median"],
                        row["size_max"],
                        row["size_p90"],
                        f"{float(row['radius_median']):.6f}",
                        f"{float(row['radius_p90']):.6f}",
                        f"{float(row['radius_max']):.6f}",
                        f"{float(row['max_min_size_ratio']):.6f}",
                    ]
                )
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic k-means partitioning for IcyCore.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME)
    parser.add_argument("--moi-metric")
    parser.add_argument("--k-list", default=DEFAULT_K_LIST)
    parser.add_argument("--max-iter", type=int, default=DEFAULT_MAX_ITER)
    parser.add_argument("--tol", type=float, default=DEFAULT_TOL)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--anchor-x", type=float)
    parser.add_argument("--anchor-y", type=float)
    parser.add_argument("--anchor-z", type=float)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    anchor_xyz = None
    if args.anchor_x is not None or args.anchor_y is not None or args.anchor_z is not None:
        if args.anchor_x is None or args.anchor_y is None or args.anchor_z is None:
            raise SystemExit("Provide all of --anchor-x --anchor-y --anchor-z or none.")
        anchor_xyz = (args.anchor_x, args.anchor_y, args.anchor_z)

    rows = run_kmeans_sweep(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        moi_metric=args.moi_metric,
        k_list=parse_k_list(args.k_list),
        max_iter=args.max_iter,
        tol=args.tol,
        out_dir=Path(args.out_dir),
        anchor_xyz=anchor_xyz,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    for row in rows:
        print(
            f"k={row['k']} size_min={row['size_min']} size_max={row['size_max']} "
            f"radius_max={float(row['radius_max']):.6f} ratio={float(row['max_min_size_ratio']):.6f}"
        )
    if args.dry_run:
        print("Dry run: no files written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
