from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from contextlib import closing


ALGO_VERSION = "phase5_0_8_kmedoids_v1"
DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
EPS = 1e-12


@dataclass(frozen=True)
class Point:
    ring_id: str
    x: float
    y: float
    z: float
    moi_metric: float | None
    system_name: str | None
    body_name: str | None
    ring_name: str | None


@dataclass(frozen=True)
class Cluster:
    cluster_index: int
    cluster_id: str
    medoid_ring_id: str
    k: int
    size_n: int
    radius_max_ly: float
    radius_p90_ly: float
    centroid_x: float
    centroid_y: float
    centroid_z: float
    cost_sum: float


@dataclass(frozen=True)
class MemberAssignment:
    ring_id: str
    cluster_index: int
    cluster_id: str
    medoid_ring_id: str
    dist_to_medoid_ly: float
    dist2_to_medoid: float
    assign_rank: int


def _dist2(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return dx * dx + dy * dy + dz * dz


def _quantile_floor(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    idx = int(math.floor((len(values) - 1) * q))
    return values[idx]


def apply_schema(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema_phase5_staging.sql")
    conn.executescript(schema_path.read_text(encoding="utf-8"))

    cols = conn.execute("PRAGMA table_info('subregion_staging_members')").fetchall()
    col_names = {str(row[1]) for row in cols}
    if "dist2_to_medoid" not in col_names:
        conn.execute("ALTER TABLE subregion_staging_members ADD COLUMN dist2_to_medoid REAL")


def parse_k_map(raw: str | None) -> dict[str, int]:
    if not raw:
        return {}
    out: dict[str, int] = {}
    for piece in raw.split(","):
        item = piece.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Invalid --k-map entry: '{item}'")
        subregion, val = item.split("=", 1)
        k = int(val.strip())
        if k <= 0:
            raise ValueError(f"Invalid k in --k-map entry: '{item}'")
        out[subregion.strip()] = k
    return out


def get_subregions(conn: sqlite3.Connection, score_version: str, cohort_name: str, subregion: str | None) -> list[str]:
    params: list[object] = [score_version, cohort_name]
    where = ""
    if subregion is not None:
        where = " AND subregion=?"
        params.append(subregion)
    rows = conn.execute(
        f"""
        SELECT subregion
        FROM subregion_summaries
        WHERE score_version=? AND cohort_name=? {where}
        ORDER BY subregion ASC
        """,
        tuple(params),
    ).fetchall()
    return [row[0] for row in rows]


def get_subregion_meta(conn: sqlite3.Connection, score_version: str, cohort_name: str, subregion: str) -> tuple[str, float, tuple[float, float, float]]:
    row = conn.execute(
        """
        SELECT band, radius_max_ly, centroid_x, centroid_y, centroid_z
        FROM subregion_summaries
        WHERE score_version=? AND cohort_name=? AND subregion=?
        LIMIT 1
        """,
        (score_version, cohort_name, subregion),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Missing subregion_summaries row for {subregion}")
    band = row[0]
    if band is None or str(band).strip() == "":
        # Fallback from label suffix.
        label = subregion.lower()
        if label.endswith("-inner"):
            band = "inner"
        elif label.endswith("-mid"):
            band = "mid"
        elif label.endswith("-outer"):
            band = "outer"
        else:
            raise RuntimeError(f"Unable to determine band for subregion '{subregion}'")
    return str(band), float(row[1]), (float(row[2]), float(row[3]), float(row[4]))


def load_points(conn: sqlite3.Connection, score_version: str, cohort_name: str, subregion: str) -> list[Point]:
    rows = conn.execute(
        """
        SELECT ring_id, x, y, z, moi_metric, system_name, body_name, ring_name
        FROM icy_subregions
        WHERE score_version=? AND cohort_name=? AND subregion=?
        ORDER BY (moi_metric IS NULL) ASC, moi_metric DESC, ring_id ASC
        """,
        (score_version, cohort_name, subregion),
    ).fetchall()
    return [
        Point(
            ring_id=row[0],
            x=float(row[1]),
            y=float(row[2]),
            z=float(row[3]),
            moi_metric=None if row[4] is None else float(row[4]),
            system_name=row[5],
            body_name=row[6],
            ring_name=row[7],
        )
        for row in rows
    ]


def choose_k(band: str, radius_max_ly: float, explicit_k: int | None, auto_k: bool, k_map: dict[str, int], subregion: str) -> int:
    if subregion in k_map:
        return k_map[subregion]
    if explicit_k is not None:
        return explicit_k
    if not auto_k:
        # deterministic default fallback
        return 1
    band_l = band.lower()
    if band_l == "inner":
        t = 8000.0
        kmax = 3
    elif band_l == "mid":
        t = 10000.0
        kmax = 6
    else:
        t = 12000.0
        kmax = 10
    # Start at 1 and escalate by caller loop based on achieved max radius.
    # Here return sentinel 1; caller handles escalation with thresholds.
    _ = radius_max_ly
    _ = t
    _ = kmax
    return 1


def band_policy(band: str) -> tuple[float, int]:
    band_l = band.lower()
    if band_l == "inner":
        return 8000.0, 3
    if band_l == "mid":
        return 10000.0, 6
    return 12000.0, 10


def _moi_sort_val(p: Point) -> float:
    return p.moi_metric if p.moi_metric is not None else float("-inf")


def first_medoid(points: list[Point], centroid: tuple[float, float, float]) -> str:
    best = points[0]
    best_d2 = _dist2((best.x, best.y, best.z), centroid)
    for p in points[1:]:
        d2 = _dist2((p.x, p.y, p.z), centroid)
        if d2 < best_d2 - EPS:
            best = p
            best_d2 = d2
        elif abs(d2 - best_d2) <= EPS:
            if _moi_sort_val(p) > _moi_sort_val(best) + EPS:
                best = p
            elif abs(_moi_sort_val(p) - _moi_sort_val(best)) <= EPS and p.ring_id < best.ring_id:
                best = p
    return best.ring_id


def nearest_medoid_d2(point: Point, medoid_ids: list[str], pidx: dict[str, Point]) -> float:
    m0 = pidx[medoid_ids[0]]
    best = _dist2((point.x, point.y, point.z), (m0.x, m0.y, m0.z))
    for mid in medoid_ids[1:]:
        m = pidx[mid]
        d2 = _dist2((point.x, point.y, point.z), (m.x, m.y, m.z))
        if d2 < best:
            best = d2
    return best


def init_farthest_first(points: list[Point], centroid: tuple[float, float, float], k: int) -> list[str]:
    pidx = {p.ring_id: p for p in points}
    chosen = [first_medoid(points, centroid)]
    chosen_set = set(chosen)
    while len(chosen) < min(k, len(points)):
        candidates = [p for p in points if p.ring_id not in chosen_set]
        best = candidates[0]
        best_nn = nearest_medoid_d2(best, chosen, pidx)
        for p in candidates[1:]:
            nn = nearest_medoid_d2(p, chosen, pidx)
            if nn > best_nn + EPS:
                best = p
                best_nn = nn
            elif abs(nn - best_nn) <= EPS:
                if _moi_sort_val(p) > _moi_sort_val(best) + EPS:
                    best = p
                elif abs(_moi_sort_val(p) - _moi_sort_val(best)) <= EPS and p.ring_id < best.ring_id:
                    best = p
        chosen.append(best.ring_id)
        chosen_set.add(best.ring_id)
    return chosen


def assign_points(points: list[Point], medoid_ids: list[str], pidx: dict[str, Point]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in points:
        best_mid = medoid_ids[0]
        m0 = pidx[best_mid]
        best_d2 = _dist2((p.x, p.y, p.z), (m0.x, m0.y, m0.z))
        for mid in medoid_ids[1:]:
            m = pidx[mid]
            d2 = _dist2((p.x, p.y, p.z), (m.x, m.y, m.z))
            if d2 < best_d2 - EPS:
                best_mid = mid
                best_d2 = d2
            elif abs(d2 - best_d2) <= EPS and mid < best_mid:
                best_mid = mid
                best_d2 = d2
        out[p.ring_id] = best_mid
    return out


def update_medoids(points: list[Point], assignments: dict[str, str], medoid_ids: list[str], pidx: dict[str, Point]) -> list[str]:
    clusters: dict[str, list[Point]] = {mid: [] for mid in medoid_ids}
    for p in points:
        clusters[assignments[p.ring_id]].append(p)

    new_ids: list[str] = []
    for old_mid in medoid_ids:
        members = clusters[old_mid]
        best = members[0]
        best_cost = None
        for cand in members:
            cost = 0.0
            cxyz = (cand.x, cand.y, cand.z)
            for m in members:
                cost += math.sqrt(_dist2(cxyz, (m.x, m.y, m.z)))
            if best_cost is None or cost < best_cost - EPS:
                best = cand
                best_cost = cost
            elif abs(cost - best_cost) <= EPS:
                if _moi_sort_val(cand) > _moi_sort_val(best) + EPS:
                    best = cand
                elif abs(_moi_sort_val(cand) - _moi_sort_val(best)) <= EPS and cand.ring_id < best.ring_id:
                    best = cand
        new_ids.append(best.ring_id)
    # Keep medoid order deterministic by ring id after each update.
    return sorted(new_ids)


def summarize_assignments(
    points: list[Point], medoid_ids: list[str], assignments: dict[str, str]
) -> tuple[dict[str, list[str]], dict[str, dict[str, float | tuple[float, float, float]]]]:
    pidx = {p.ring_id: p for p in points}
    clusters_by_mid: dict[str, list[str]] = {mid: [] for mid in medoid_ids}
    for p in points:
        clusters_by_mid[assignments[p.ring_id]].append(p.ring_id)

    stats_by_mid: dict[str, dict[str, float | tuple[float, float, float]]] = {}
    for mid in medoid_ids:
        members = clusters_by_mid[mid]
        m = pidx[mid]
        d2_values: list[float] = []
        cx = 0.0
        cy = 0.0
        cz = 0.0
        for rid in members:
            p = pidx[rid]
            d2 = _dist2((p.x, p.y, p.z), (m.x, m.y, m.z))
            d2_values.append(d2)
            cx += p.x
            cy += p.y
            cz += p.z
        d2_values.sort()
        size_n = len(members)
        radius_max_ly = math.sqrt(d2_values[-1]) if d2_values else 0.0
        radius_p90_ly = math.sqrt(_quantile_floor(d2_values, 0.90))
        cost_sum = sum(math.sqrt(d2) for d2 in d2_values)
        stats_by_mid[mid] = {
            "radius_max_ly": radius_max_ly,
            "radius_p90_ly": radius_p90_ly,
            "centroid": (
                (cx / size_n) if size_n else 0.0,
                (cy / size_n) if size_n else 0.0,
                (cz / size_n) if size_n else 0.0,
            ),
            "cost_sum": cost_sum,
        }
    return clusters_by_mid, stats_by_mid


def run_kmedoids_detail(
    points: list[Point], centroid: tuple[float, float, float], k: int, max_iter: int = 10
) -> tuple[list[str], dict[str, str], dict[str, list[str]], dict[str, dict[str, float | tuple[float, float, float]]]]:
    pidx = {p.ring_id: p for p in points}
    medoid_ids = sorted(init_farthest_first(points, centroid, k))
    for _ in range(max_iter):
        medoid_ids = sorted(medoid_ids)
        assignments = assign_points(points, medoid_ids, pidx)
        updated = sorted(update_medoids(points, assignments, medoid_ids, pidx))
        if updated == medoid_ids:
            clusters_by_mid, stats_by_mid = summarize_assignments(points, medoid_ids, assignments)
            return medoid_ids, assignments, clusters_by_mid, stats_by_mid
        medoid_ids = updated

    medoid_ids = sorted(medoid_ids)
    assignments = assign_points(points, medoid_ids, pidx)
    clusters_by_mid, stats_by_mid = summarize_assignments(points, medoid_ids, assignments)
    return medoid_ids, assignments, clusters_by_mid, stats_by_mid


def run_kmedoids(points: list[Point], centroid: tuple[float, float, float], k: int, max_iter: int = 10) -> tuple[list[str], dict[str, str]]:
    medoids, assignments, _, _ = run_kmedoids_detail(points, centroid, k, max_iter=max_iter)
    return medoids, assignments


def max_radius_from_assignments(points: list[Point], assignments: dict[str, str], pidx: dict[str, Point]) -> float:
    max_d = 0.0
    for p in points:
        m = pidx[assignments[p.ring_id]]
        d = math.sqrt(_dist2((p.x, p.y, p.z), (m.x, m.y, m.z)))
        if d > max_d:
            max_d = d
    return max_d


def auto_kmedoids(points: list[Point], band: str, centroid: tuple[float, float, float]) -> tuple[int, list[str], dict[str, str]]:
    threshold, kmax = band_policy(band)
    pidx = {p.ring_id: p for p in points}
    best_k, best_m, best_a = 1, [], {}
    for k in range(1, min(kmax, len(points)) + 1):
        medoids, assignments = run_kmedoids(points, centroid, k, max_iter=10)
        rmax = max_radius_from_assignments(points, assignments, pidx)
        best_k, best_m, best_a = k, medoids, assignments
        if rmax <= threshold + EPS:
            break
    return best_k, best_m, best_a


def auto_kmedoids_detail(
    points: list[Point], band: str, centroid: tuple[float, float, float]
) -> tuple[
    int,
    list[str],
    dict[str, str],
    dict[str, list[str]],
    dict[str, dict[str, float | tuple[float, float, float]]],
]:
    threshold, kmax = band_policy(band)
    pidx = {p.ring_id: p for p in points}
    best_k = 1
    best_m: list[str] = []
    best_a: dict[str, str] = {}
    best_clusters: dict[str, list[str]] = {}
    best_stats: dict[str, dict[str, float | tuple[float, float, float]]] = {}
    for k in range(1, min(kmax, len(points)) + 1):
        medoids, assignments, clusters_by_mid, stats_by_mid = run_kmedoids_detail(points, centroid, k, max_iter=10)
        rmax = max_radius_from_assignments(points, assignments, pidx)
        best_k, best_m, best_a = k, medoids, assignments
        best_clusters, best_stats = clusters_by_mid, stats_by_mid
        if rmax <= threshold + EPS:
            break
    return best_k, best_m, best_a, best_clusters, best_stats


def build_cluster_rows(
    points: list[Point],
    medoid_ids: list[str],
    assignments: dict[str, str],
    clusters_by_mid: dict[str, list[str]],
    stats_by_mid: dict[str, dict[str, float | tuple[float, float, float]]],
    run_id: str,
    score_version: str,
    cohort_name: str,
    subregion: str,
    k: int,
) -> tuple[list[Cluster], list[MemberAssignment], dict[str, Point]]:
    pidx = {p.ring_id: p for p in points}
    # Stable cluster_index ordering.
    medoid_ids_sorted = sorted(medoid_ids)
    cluster_index_by_mid = {mid: idx + 1 for idx, mid in enumerate(medoid_ids_sorted)}

    cluster_rows: list[Cluster] = []
    assignment_rows: list[MemberAssignment] = []
    for mid in medoid_ids_sorted:
        members = clusters_by_mid[mid]
        size_n = len(members)
        centroid = stats_by_mid[mid]["centroid"]
        assert isinstance(centroid, tuple)
        radius_max = float(stats_by_mid[mid]["radius_max_ly"])
        radius_p90 = float(stats_by_mid[mid]["radius_p90_ly"])
        cost_sum = float(stats_by_mid[mid]["cost_sum"])
        cluster_index = cluster_index_by_mid[mid]
        cluster_id_raw = f"{run_id}|{cluster_index}|{mid}"
        cluster_id = hashlib.sha1(cluster_id_raw.encode("utf-8")).hexdigest()
        cluster_rows.append(
            Cluster(
                cluster_index=cluster_index,
                cluster_id=cluster_id,
                medoid_ring_id=mid,
                k=k,
                size_n=size_n,
                radius_max_ly=radius_max,
                radius_p90_ly=radius_p90,
                centroid_x=float(centroid[0]),
                centroid_y=float(centroid[1]),
                centroid_z=float(centroid[2]),
                cost_sum=cost_sum,
            )
        )
        m = pidx[mid]
        ordered_members = sorted(
            (pidx[rid] for rid in members),
            key=lambda p: (_dist2((p.x, p.y, p.z), (m.x, m.y, m.z)), -_moi_sort_val(p), p.ring_id),
        )
        for assign_rank, p in enumerate(ordered_members, start=1):
            dist2 = _dist2((p.x, p.y, p.z), (m.x, m.y, m.z))
            assignment_rows.append(
                MemberAssignment(
                    ring_id=p.ring_id,
                    cluster_index=cluster_index,
                    cluster_id=cluster_id,
                    medoid_ring_id=mid,
                    dist_to_medoid_ly=math.sqrt(dist2),
                    dist2_to_medoid=dist2,
                    assign_rank=assign_rank,
                )
            )
    assignment_rows.sort(key=lambda a: a.ring_id)
    return cluster_rows, assignment_rows, pidx


def run_id_for(
    score_version: str,
    cohort_name: str,
    subregion: str,
    k: int,
    auto_k: bool,
    k_map: dict[str, int],
) -> str:
    mode = "auto-k" if auto_k else "fixed-k"
    key = f"phase5.0.8|{ALGO_VERSION}|{score_version}|{cohort_name}|{subregion}|{k}|{mode}|{sorted(k_map.items())}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def persist_for_subregion(
    conn: sqlite3.Connection,
    run_id: str,
    score_version: str,
    cohort_name: str,
    subregion: str,
    mode: str,
    k_final: int,
    policy_json: str,
    clusters: list[Cluster],
    assignments: list[MemberAssignment],
) -> None:
    created_utc = datetime.now(timezone.utc).isoformat()
    # Canonical staging tables are scoped snapshots per score/cohort/subregion.
    conn.execute(
        """
        DELETE FROM subregion_staging_members
        WHERE score_version=? AND cohort_name=? AND subregion=?
        """,
        (score_version, cohort_name, subregion),
    )
    conn.execute(
        """
        DELETE FROM subregion_staging_clusters
        WHERE score_version=? AND cohort_name=? AND subregion=?
        """,
        (score_version, cohort_name, subregion),
    )
    conn.execute(
        """
        DELETE FROM subregion_staging_runs
        WHERE score_version=? AND cohort_name=? AND subregion=?
        """,
        (score_version, cohort_name, subregion),
    )
    conn.executemany(
        """
        INSERT INTO subregion_staging_clusters (
            run_id, score_version, cohort_name, subregion, k, cluster_index, cluster_id,
            medoid_ring_id, size_n, radius_max_ly, radius_p90_ly, centroid_x, centroid_y, centroid_z,
            cost_sum, created_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                score_version,
                cohort_name,
                subregion,
                c.k,
                c.cluster_index,
                c.cluster_id,
                c.medoid_ring_id,
                c.size_n,
                c.radius_max_ly,
                c.radius_p90_ly,
                c.centroid_x,
                c.centroid_y,
                c.centroid_z,
                c.cost_sum,
                created_utc,
            )
            for c in clusters
        ],
    )
    conn.executemany(
        """
        INSERT INTO subregion_staging_members (
            run_id, cluster_id, score_version, cohort_name, subregion, ring_id,
            medoid_ring_id, dist_to_medoid_ly, dist2_to_medoid, assign_rank
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                a.cluster_id,
                score_version,
                cohort_name,
                subregion,
                a.ring_id,
                a.medoid_ring_id,
                a.dist_to_medoid_ly,
                a.dist2_to_medoid,
                a.assign_rank,
            )
            for a in assignments
        ],
    )
    conn.execute(
        """
        INSERT INTO subregion_staging_runs (
            run_id, score_version, cohort_name, subregion, mode, k_final, policy_json, created_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, score_version, cohort_name, subregion, mode, k_final, policy_json, created_utc),
    )


def normalize_k_map(k_map: dict[str, int]) -> str:
    if not k_map:
        return ""
    return ",".join(f"{name}={value}" for name, value in sorted(k_map.items()))


def policy_string_for(
    *,
    band: str,
    mode: str,
    chosen_k: int,
    explicit_k: int | None,
    k_map: dict[str, int],
) -> str:
    parts = [
        f"algorithm_version={ALGO_VERSION}",
        f"mode={mode}",
        f"k_final={chosen_k}",
        "cluster_order=medoid_ring_id_asc",
        f"band={band.lower()}",
        f"k_map={normalize_k_map(k_map)}",
    ]
    if mode == "auto-k":
        threshold, kmax = band_policy(band)
        parts.extend([f"threshold_ly={threshold:.1f}", f"kmax={kmax}"])
    else:
        if explicit_k is not None:
            parts.append(f"fixed_k={explicit_k}")
    return "|".join(parts)


def maybe_write_exports(
    out_dir: Path | None,
    staging_rows: list[list[object]],
    coverage_rows: list[list[object]],
    sample_rows: list[list[object]],
) -> None:
    if out_dir is None:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "staging_points.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["subregion", "staging_idx", "medoid_ring_id", "system_name", "body_name", "ring_name", "x", "y", "z", "moi_metric"])
        w.writerows(staging_rows)
    with (out_dir / "staging_coverage.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["subregion", "staging_idx", "count_assigned", "max_dist", "p90_dist", "median_dist"])
        w.writerows(coverage_rows)
    with (out_dir / "assignments_sample.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["subregion", "ring_id", "staging_idx", "staging_ring_id", "distance_to_staging_ly"])
        w.writerows(sample_rows)


def build_staging(
    db_path: Path,
    score_version: str,
    cohort_name: str,
    subregion: str | None,
    k: int | None,
    auto_k: bool,
    dry_run: bool,
    out_dir: Path | None = None,
    k_map: str | None = None,
) -> list[dict[str, object]]:
    if k is not None and k <= 0:
        raise ValueError("--k must be positive")
    k_map_dict = parse_k_map(k_map)
    summary_rows: list[dict[str, object]] = []
    export_staging: list[list[object]] = []
    export_coverage: list[list[object]] = []
    export_sample: list[list[object]] = []

    with closing(sqlite3.connect(db_path)) as conn:
        if not dry_run:
            apply_schema(conn)
        targets = get_subregions(conn, score_version, cohort_name, subregion)
        conn.execute("BEGIN")
        try:
            for sub in targets:
                band, radius_from_summary, centroid = get_subregion_meta(conn, score_version, cohort_name, sub)
                points = load_points(conn, score_version, cohort_name, sub)
                if not points:
                    continue

                chosen_k = choose_k(band, radius_from_summary, k, auto_k, k_map_dict, sub)
                if auto_k and k is None and sub not in k_map_dict:
                    chosen_k, medoid_ids, assignments, clusters_by_mid, stats_by_mid = auto_kmedoids_detail(points, band, centroid)
                else:
                    chosen_k = min(chosen_k, len(points))
                    medoid_ids, assignments, clusters_by_mid, stats_by_mid = run_kmedoids_detail(
                        points, centroid, chosen_k, max_iter=10
                    )

                rid = run_id_for(score_version, cohort_name, sub, chosen_k, auto_k and k is None, k_map_dict)
                mode = "auto-k" if (auto_k and k is None) else "fixed-k"
                policy_json = policy_string_for(
                    band=band,
                    mode=mode,
                    chosen_k=chosen_k,
                    explicit_k=k,
                    k_map=k_map_dict,
                )
                clusters, member_rows, pidx = build_cluster_rows(
                    points=points,
                    medoid_ids=medoid_ids,
                    assignments=assignments,
                    clusters_by_mid=clusters_by_mid,
                    stats_by_mid=stats_by_mid,
                    run_id=rid,
                    score_version=score_version,
                    cohort_name=cohort_name,
                    subregion=sub,
                    k=chosen_k,
                )
                if not dry_run:
                    persist_for_subregion(
                        conn=conn,
                        run_id=rid,
                        score_version=score_version,
                        cohort_name=cohort_name,
                        subregion=sub,
                        mode=mode,
                        k_final=chosen_k,
                        policy_json=policy_json,
                        clusters=clusters,
                        assignments=member_rows,
                    )

                dists = sorted(a.dist_to_medoid_ly for a in member_rows)
                max_overall = dists[-1] if dists else 0.0
                p90_overall = _quantile_floor(dists, 0.90)
                medoid_list = [c.medoid_ring_id for c in sorted(clusters, key=lambda x: x.cluster_index)]
                print(
                    f"{sub} K={chosen_k} total_n={len(points)} max_dist_overall={max_overall:.3f} "
                    f"p90_overall={p90_overall:.3f} medoids={','.join(medoid_list)}"
                )
                for c in sorted(clusters, key=lambda x: x.cluster_index):
                    print(
                        f"  cluster_index={c.cluster_index} size_n={c.size_n} radius_max_ly={c.radius_max_ly:.3f}"
                    )

                summary_rows.append(
                    {
                        "subregion": sub,
                        "k": chosen_k,
                        "total_n": len(points),
                        "max_dist_overall": max_overall,
                        "p90_overall": p90_overall,
                        "run_id": rid,
                        "medoids": medoid_list,
                    }
                )

                if out_dir is not None:
                    by_cluster_idx: dict[int, list[float]] = {c.cluster_index: [] for c in clusters}
                    for a in member_rows:
                        by_cluster_idx[a.cluster_index].append(a.dist_to_medoid_ly)
                    for c in sorted(clusters, key=lambda x: x.cluster_index):
                        mp = pidx[c.medoid_ring_id]
                        export_staging.append(
                            [
                                sub,
                                c.cluster_index,
                                c.medoid_ring_id,
                                mp.system_name or "",
                                mp.body_name or "",
                                mp.ring_name or "",
                                f"{mp.x:.6f}",
                                f"{mp.y:.6f}",
                                f"{mp.z:.6f}",
                                "" if mp.moi_metric is None else f"{mp.moi_metric:.6f}",
                            ]
                        )
                        dlist = sorted(by_cluster_idx[c.cluster_index])
                        export_coverage.append(
                            [
                                sub,
                                c.cluster_index,
                                len(dlist),
                                f"{(dlist[-1] if dlist else 0.0):.6f}",
                                f"{_quantile_floor(dlist, 0.90):.6f}",
                                f"{(median(dlist) if dlist else 0.0):.6f}",
                            ]
                        )
                    for a in member_rows[: min(10, len(member_rows))]:
                        export_sample.append(
                            [sub, a.ring_id, a.cluster_index, a.medoid_ring_id, f"{a.dist_to_medoid_ly:.6f}"]
                        )

            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception:
            conn.rollback()
            raise

    maybe_write_exports(out_dir, export_staging, export_coverage, export_sample)
    return summary_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic Phase 5.0.8 true k-medoids staging clusters.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--score-version", required=True)
    parser.add_argument("--cohort-name", required=True)
    parser.add_argument("--subregion")
    parser.add_argument("--k", type=int)
    parser.add_argument("--auto-k", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-dir")
    parser.add_argument("--k-map")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else None
    build_staging(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        subregion=args.subregion,
        k=args.k,
        auto_k=args.auto_k,
        dry_run=args.dry_run,
        out_dir=out_dir,
        k_map=args.k_map,
    )
    if args.dry_run:
        print("Dry run: no DB writes performed.")
    if out_dir is not None:
        print(f"Wrote exports: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
