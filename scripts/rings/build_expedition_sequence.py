from __future__ import annotations

"""
Build a deterministic expedition sequence over staged subregion clusters.

The planner selects one staging run per scope (latest by created_utc DESC, run_id ASC),
then sequences cluster medoids with a deterministic greedy policy that evaluates a
small lookahead window (k=3 nearest candidates by distance).

Step score for choosing next cluster B from current point A:
    d + (d > max_leg_ly ? 100000 : 0) - 250*log1p(size_n_B) + 50*p90_ly_B
"""

import argparse
import csv
import math
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_START_SYSTEM = "Phraa Blao LU-Q C21-34"
DEFAULT_MAX_LEG_LY = 8000.0
DEFAULT_OUTDIR = Path("data/ring_hunter_library/expedition_sequence")
LOOKAHEAD_K = 3
EPS = 1e-12


@dataclass(frozen=True)
class ClusterNode:
    score_version: str
    cohort_name: str
    subregion: str
    run_id: str
    mode: str
    k_final: int
    cluster_index: int
    cluster_id: str
    medoid_ring_id: str
    size_n: int
    p90_ly: float
    centroid_x: float
    centroid_y: float
    centroid_z: float
    centroid_rho: float
    medoid_x: float
    medoid_y: float
    medoid_z: float


@dataclass(frozen=True)
class StagingTables:
    runs: str
    clusters: str
    members: str


def _fmt_float(value: float, places: int = 6) -> str:
    return f"{float(value):.{places}f}"


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _quantile_floor(values_sorted: list[float], q: float) -> float:
    if not values_sorted:
        return 0.0
    idx = int(math.floor((len(values_sorted) - 1) * q))
    return values_sorted[idx]


def _step_score(distance_ly: float, size_n: int, p90_ly: float, max_leg_ly: float) -> float:
    penalty = 100000.0 if distance_ly > max_leg_ly else 0.0
    return distance_ly + penalty - (250.0 * math.log1p(float(size_n))) + (50.0 * float(p90_ly))


def _resolve_start_xyz(conn: sqlite3.Connection, start_system: str) -> tuple[float, float, float]:
    row = conn.execute(
        """
        SELECT AVG(x), AVG(y), AVG(z)
        FROM rings_raw
        WHERE system_name = ? COLLATE NOCASE
          AND x IS NOT NULL AND y IS NOT NULL AND z IS NOT NULL
        """,
        (start_system,),
    ).fetchone()
    if row is None or row[0] is None or row[1] is None or row[2] is None:
        raise ValueError(f"Could not resolve start system coordinates from rings_raw: {start_system}")
    return float(row[0]), float(row[1]), float(row[2])


def resolve_table(conn: sqlite3.Connection, preferred: str, fallback: str) -> str:
    table_names = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if preferred in table_names:
        return preferred
    if fallback in table_names:
        return fallback
    raise ValueError(f"Missing required table: preferred={preferred}, fallback={fallback}")


def _resolve_staging_tables(conn: sqlite3.Connection) -> StagingTables:
    return StagingTables(
        runs=resolve_table(conn, "subregion_staging_runs", "staged_cluster_runs"),
        clusters=resolve_table(conn, "subregion_staging_clusters", "staged_clusters"),
        members=resolve_table(conn, "subregion_staging_members", "staged_cluster_members"),
    )


def _resolve_run_id(
    conn: sqlite3.Connection,
    staging: StagingTables,
    score_version: str,
    cohort_name: str,
    subregion: str,
) -> tuple[str, str, int]:
    row = conn.execute(
        f"""
        SELECT run_id, mode, k_final
        FROM {staging.runs}
        WHERE score_version=? AND cohort_name=? AND subregion=?
        ORDER BY created_utc DESC, run_id ASC
        LIMIT 1
        """,
        (score_version, cohort_name, subregion),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"No staging run found for scope: score_version={score_version}, cohort_name={cohort_name}, subregion={subregion}"
        )
    return str(row[0]), str(row[1]), int(row[2])


def _resolve_subregions(
    conn: sqlite3.Connection,
    staging: StagingTables,
    *,
    score_version: str,
    cohort_name: str,
    subregions: list[str] | None,
    all_subregions: bool,
) -> list[str]:
    if all_subregions:
        rows = conn.execute(
            f"""
            SELECT DISTINCT subregion
            FROM {staging.runs}
            WHERE score_version=? AND cohort_name=?
            ORDER BY subregion ASC
            """,
            (score_version, cohort_name),
        ).fetchall()
        values = [str(row[0]) for row in rows]
    else:
        values = sorted(set(subregions or []))
    if not values:
        raise ValueError("No subregions selected. Provide --subregion values or use --all-subregions.")
    return values


def _load_cluster_nodes(
    conn: sqlite3.Connection,
    staging: StagingTables,
    *,
    score_version: str,
    cohort_name: str,
    subregions: list[str],
) -> list[ClusterNode]:
    out: list[ClusterNode] = []
    for subregion in sorted(subregions):
        run_id, mode, k_final = _resolve_run_id(conn, staging, score_version, cohort_name, subregion)

        member_rows = conn.execute(
            f"""
            SELECT cluster_id, dist_to_medoid_ly
            FROM {staging.members}
            WHERE score_version=? AND cohort_name=? AND subregion=? AND run_id=?
            ORDER BY cluster_id ASC, dist_to_medoid_ly ASC, ring_id ASC
            """,
            (score_version, cohort_name, subregion, run_id),
        ).fetchall()
        dists_by_cluster: dict[str, list[float]] = {}
        for cluster_id, dist_ly in member_rows:
            dists_by_cluster.setdefault(str(cluster_id), []).append(float(dist_ly))
        for vals in dists_by_cluster.values():
            vals.sort()

        rows = conn.execute(
            f"""
            SELECT
                c.cluster_index,
                c.cluster_id,
                c.medoid_ring_id,
                c.size_n,
                c.centroid_x,
                c.centroid_y,
                c.centroid_z,
                COALESCE(i.x, rr.x) AS medoid_x,
                COALESCE(i.y, rr.y) AS medoid_y,
                COALESCE(i.z, rr.z) AS medoid_z
            FROM {staging.clusters} c
            LEFT JOIN icy_subregions i
              ON i.score_version = c.score_version
             AND i.cohort_name = c.cohort_name
             AND i.ring_id = c.medoid_ring_id
            LEFT JOIN rings_raw rr
              ON rr.ring_id = c.medoid_ring_id
            WHERE c.score_version=? AND c.cohort_name=? AND c.subregion=? AND c.run_id=?
            ORDER BY c.cluster_index ASC, c.medoid_ring_id ASC
            """,
            (score_version, cohort_name, subregion, run_id),
        ).fetchall()
        if not rows:
            raise ValueError(
                f"No staged clusters for scope: score_version={score_version}, cohort_name={cohort_name}, subregion={subregion}, run_id={run_id}"
            )
        for row in rows:
            cluster_index = int(row[0])
            cluster_id = str(row[1])
            medoid_ring_id = str(row[2])
            size_n = int(row[3])
            centroid_x = float(row[4])
            centroid_y = float(row[5])
            centroid_z = float(row[6])
            if row[7] is None or row[8] is None or row[9] is None:
                raise ValueError(
                    f"Missing medoid coordinates for cluster_id={cluster_id}, medoid_ring_id={medoid_ring_id}"
                )
            medoid_x = float(row[7])
            medoid_y = float(row[8])
            medoid_z = float(row[9])
            centroid_rho = math.sqrt((centroid_x * centroid_x) + (centroid_y * centroid_y) + (centroid_z * centroid_z))
            p90_ly = _quantile_floor(dists_by_cluster.get(cluster_id, []), 0.90)
            out.append(
                ClusterNode(
                    score_version=score_version,
                    cohort_name=cohort_name,
                    subregion=subregion,
                    run_id=run_id,
                    mode=mode,
                    k_final=k_final,
                    cluster_index=cluster_index,
                    cluster_id=cluster_id,
                    medoid_ring_id=medoid_ring_id,
                    size_n=size_n,
                    p90_ly=p90_ly,
                    centroid_x=centroid_x,
                    centroid_y=centroid_y,
                    centroid_z=centroid_z,
                    centroid_rho=centroid_rho,
                    medoid_x=medoid_x,
                    medoid_y=medoid_y,
                    medoid_z=medoid_z,
                )
            )
    return out


def _select_next(
    current_xyz: tuple[float, float, float],
    remaining: list[ClusterNode],
    max_leg_ly: float,
) -> tuple[ClusterNode, float]:
    by_distance = sorted(
        remaining,
        key=lambda c: (
            _dist(current_xyz, (c.medoid_x, c.medoid_y, c.medoid_z)),
            c.cluster_index,
            c.medoid_ring_id,
            c.cluster_id,
        ),
    )
    lookahead = by_distance[:LOOKAHEAD_K]

    best = lookahead[0]
    best_d = _dist(current_xyz, (best.medoid_x, best.medoid_y, best.medoid_z))
    best_score = _step_score(best_d, best.size_n, best.p90_ly, max_leg_ly)
    for cand in lookahead[1:]:
        d = _dist(current_xyz, (cand.medoid_x, cand.medoid_y, cand.medoid_z))
        score = _step_score(d, cand.size_n, cand.p90_ly, max_leg_ly)
        if score < best_score - EPS:
            best = cand
            best_d = d
            best_score = score
            continue
        if abs(score - best_score) <= EPS:
            if d < best_d - EPS:
                best = cand
                best_d = d
                best_score = score
                continue
            if abs(d - best_d) <= EPS:
                tie_a = (cand.cluster_index, cand.medoid_ring_id, cand.cluster_id)
                tie_b = (best.cluster_index, best.medoid_ring_id, best.cluster_id)
                if tie_a < tie_b:
                    best = cand
                    best_d = d
                    best_score = score
    return best, best_d


def _sequence_clusters(
    nodes: list[ClusterNode],
    start_xyz: tuple[float, float, float],
    max_leg_ly: float,
) -> list[tuple[int, ClusterNode, float, bool]]:
    remaining = sorted(nodes, key=lambda c: (c.subregion, c.cluster_index, c.medoid_ring_id, c.cluster_id))
    current = start_xyz
    sequence: list[tuple[int, ClusterNode, float, bool]] = []
    step = 1
    while remaining:
        nxt, leg_distance = _select_next(current, remaining, max_leg_ly)
        remaining.remove(nxt)
        staging_required = leg_distance > max_leg_ly
        sequence.append((step, nxt, leg_distance, staging_required))
        step += 1
        current = (nxt.medoid_x, nxt.medoid_y, nxt.medoid_z)
    return sequence


def _load_cluster_members(
    conn: sqlite3.Connection,
    staging: StagingTables,
    score_version: str,
    cohort_name: str,
    cluster_ids: list[str],
) -> dict[str, list[sqlite3.Row]]:
    if not cluster_ids:
        return {}
    placeholders = ",".join("?" for _ in cluster_ids)
    query = f"""
        SELECT
            m.cluster_id,
            m.ring_id,
            m.medoid_ring_id,
            m.dist_to_medoid_ly,
            m.dist2_to_medoid,
            i.moi_metric,
            COALESCE(i.system_name, rr.system_name) AS system_name,
            COALESCE(i.body_name, rr.body_name) AS body_name,
            COALESCE(i.ring_name, rr.ring_name) AS ring_name
        FROM {staging.members} m
        LEFT JOIN icy_subregions i
          ON i.score_version = m.score_version
         AND i.cohort_name = m.cohort_name
         AND i.ring_id = m.ring_id
        LEFT JOIN rings_raw rr
          ON rr.ring_id = m.ring_id
        WHERE m.score_version=?
          AND m.cohort_name=?
          AND m.cluster_id IN ({placeholders})
        ORDER BY m.cluster_id ASC, m.dist_to_medoid_ly ASC, m.ring_id ASC
    """
    rows = conn.execute(query, [score_version, cohort_name, *cluster_ids]).fetchall()
    by_cluster: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_cluster.setdefault(str(row[0]), []).append(row)
    return by_cluster


def build_expedition_sequence(
    *,
    db_path: Path,
    score_version: str,
    cohort_name: str,
    subregions: list[str] | None,
    all_subregions: bool,
    start_system: str,
    max_leg_ly: float,
    outdir: Path,
) -> dict[str, int]:
    outdir.mkdir(parents=True, exist_ok=True)
    if db_path.stat().st_size < 50_000_000:
        print(
            "Warning: database file is smaller than 50 MB and may be schema-only/unpopulated.",
            file=sys.stderr,
        )

    with closing(sqlite3.connect(db_path)) as conn:
        staging = _resolve_staging_tables(conn)
        run_count = int(conn.execute(f"SELECT COUNT(*) FROM {staging.runs}").fetchone()[0])
        if run_count <= 0:
            raise ValueError(
                "No staged runs found. Run build_subregion_staging first (and ensure you are pointing at the populated rings_master_YYYY-MM-DD.sqlite)."
            )
        subregion_list = _resolve_subregions(
            conn,
            staging,
            score_version=score_version,
            cohort_name=cohort_name,
            subregions=subregions,
            all_subregions=all_subregions,
        )
        start_xyz = _resolve_start_xyz(conn, start_system)
        nodes = _load_cluster_nodes(
            conn,
            staging,
            score_version=score_version,
            cohort_name=cohort_name,
            subregions=subregion_list,
        )
        cluster_ids = [node.cluster_id for node in nodes]
        members_by_cluster = _load_cluster_members(conn, staging, score_version, cohort_name, cluster_ids)
    nodes_by_subregion: dict[str, list[ClusterNode]] = {}
    for node in nodes:
        nodes_by_subregion.setdefault(node.subregion, []).append(node)

    cluster_headers = [
        "seq",
        "score_version",
        "cohort_name",
        "subregion",
        "run_id",
        "cluster_index",
        "cluster_id",
        "medoid_ring_id",
        "leg_distance_ly",
        "staging_required",
        "size_n",
        "p90_ly",
        "centroid_rho",
    ]

    target_headers = [
        "seq",
        "target_order",
        "score_version",
        "cohort_name",
        "subregion",
        "run_id",
        "cluster_index",
        "cluster_id",
        "ring_id",
        "is_medoid",
        "dist_to_medoid_ly",
        "dist2_to_medoid",
        "moi_metric",
        "system_name",
        "body_name",
        "ring_name",
    ]
    staging_headers = ["seq", "from_cluster", "to_cluster", "distance_ly"]
    cluster_rows = 0
    target_rows = 0
    staging_rows = 0

    for subregion in sorted(nodes_by_subregion):
        sequence = _sequence_clusters(nodes_by_subregion[subregion], start_xyz, max_leg_ly)
        subdir = outdir / f"subregion_{subregion}"
        subdir.mkdir(parents=True, exist_ok=True)

        with (subdir / "sequence_clusters.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=cluster_headers)
            writer.writeheader()
            for seq, node, leg_distance, staging_required in sequence:
                writer.writerow(
                    {
                        "seq": seq,
                        "score_version": node.score_version,
                        "cohort_name": node.cohort_name,
                        "subregion": node.subregion,
                        "run_id": node.run_id,
                        "cluster_index": node.cluster_index,
                        "cluster_id": node.cluster_id,
                        "medoid_ring_id": node.medoid_ring_id,
                        "leg_distance_ly": _fmt_float(leg_distance),
                        "staging_required": "true" if staging_required else "false",
                        "size_n": node.size_n,
                        "p90_ly": _fmt_float(node.p90_ly),
                        "centroid_rho": _fmt_float(node.centroid_rho),
                    }
                )
                cluster_rows += 1

        with (subdir / "sequence_targets.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=target_headers)
            writer.writeheader()
            for seq, node, _, _ in sequence:
                members = members_by_cluster.get(node.cluster_id, [])
                ordered_members = sorted(
                    members,
                    key=lambda row: (
                        str(row[1]) != node.medoid_ring_id,
                        float(row[3]),
                        str(row[1]),
                    ),
                )
                for target_order, row in enumerate(ordered_members, start=1):
                    writer.writerow(
                        {
                            "seq": seq,
                            "target_order": target_order,
                            "score_version": node.score_version,
                            "cohort_name": node.cohort_name,
                            "subregion": node.subregion,
                            "run_id": node.run_id,
                            "cluster_index": node.cluster_index,
                            "cluster_id": node.cluster_id,
                            "ring_id": str(row[1]),
                            "is_medoid": "true" if str(row[1]) == node.medoid_ring_id else "false",
                            "dist_to_medoid_ly": _fmt_float(float(row[3])),
                            "dist2_to_medoid": _fmt_float(float(row[4])),
                            "moi_metric": "" if row[5] is None else _fmt_float(float(row[5])),
                            "system_name": "" if row[6] is None else str(row[6]),
                            "body_name": "" if row[7] is None else str(row[7]),
                            "ring_name": "" if row[8] is None else str(row[8]),
                        }
                    )
                    target_rows += 1

        with (subdir / "staging_recommendations.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=staging_headers)
            writer.writeheader()
            for idx, (seq, node, leg_distance, staging_required) in enumerate(sequence):
                if not staging_required:
                    continue
                from_cluster = "START" if idx == 0 else sequence[idx - 1][1].cluster_id
                writer.writerow(
                    {
                        "seq": seq,
                        "from_cluster": from_cluster,
                        "to_cluster": node.cluster_id,
                        "distance_ly": _fmt_float(leg_distance),
                    }
                )
                staging_rows += 1

    return {
        "cluster_rows": cluster_rows,
        "target_rows": target_rows,
        "staging_rows": staging_rows,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build deterministic expedition sequence CSVs from Phase 5 staging clusters."
    )
    parser.add_argument("--db", required=True, help="Path to rings SQLite DB.")
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--subregion", action="append", dest="subregions", help="Subregion to include (repeatable).")
    group.add_argument(
        "--all-subregions",
        action="store_true",
        dest="all_subregions",
        help="Include all subregions available for scope.",
    )
    parser.add_argument("--start-system", default=DEFAULT_START_SYSTEM, help="Expedition start system name.")
    parser.add_argument("--max-leg-ly", type=float, default=DEFAULT_MAX_LEG_LY, help="Max acceptable leg length before staging flag.")
    parser.add_argument(
        "--outdir",
        "--out-dir",
        dest="outdir",
        default=str(DEFAULT_OUTDIR),
        help="Output directory for sequence CSVs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_expedition_sequence(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        subregions=args.subregions,
        all_subregions=args.all_subregions,
        start_system=args.start_system,
        max_leg_ly=float(args.max_leg_ly),
        outdir=Path(args.outdir),
    )
    print(f"sequence_clusters.csv rows={result['cluster_rows']}")
    print(f"sequence_targets.csv rows={result['target_rows']}")
    print(f"staging_recommendations.csv rows={result['staging_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
