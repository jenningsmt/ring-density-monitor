from __future__ import annotations

"""
Export deterministic candidate CSVs for a staged subregion clustering run.

Outputs for a single scope (score_version, cohort_name, subregion) and run:
- clusters.csv: one row per cluster with deterministic priority ranking metadata
- members.csv: one row per member ring assignment

Priority score (MVP):
    sqrt(size_n) / (p90_ly + 1.0)

Default run selection:
    Latest run by created_utc for the given scope, tie-broken by run_id ASC.
"""

import argparse
import csv
import math
import sqlite3
from contextlib import closing
from pathlib import Path


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_OUTDIR = Path("data/ring_hunter_library/subregion_candidates")

CLUSTER_HEADERS = [
    "score_version",
    "cohort_name",
    "subregion",
    "run_id",
    "mode",
    "k_final",
    "cluster_index",
    "cluster_id",
    "medoid_ring_id",
    "size_n",
    "radius_max_ly",
    "p90_ly",
    "centroid_x",
    "centroid_y",
    "centroid_z",
    "centroid_rho",
    "priority_score",
    "priority_reason",
]

MEMBER_HEADERS = [
    "score_version",
    "cohort_name",
    "subregion",
    "run_id",
    "cluster_index",
    "cluster_id",
    "medoid_ring_id",
    "ring_id",
    "dist_to_medoid_ly",
    "dist2_to_medoid",
]


def _fmt_float(value: float | int | None, places: int = 6) -> str:
    if value is None:
        return ""
    return f"{float(value):.{places}f}"


def _quantile_floor(values_sorted: list[float], q: float) -> float:
    if not values_sorted:
        return 0.0
    idx = int(math.floor((len(values_sorted) - 1) * q))
    return values_sorted[idx]


def _resolve_run(
    conn: sqlite3.Connection,
    *,
    score_version: str,
    cohort_name: str,
    subregion: str,
    run_id: str | None,
) -> tuple[str, str, int]:
    if run_id:
        row = conn.execute(
            """
            SELECT run_id, mode, k_final
            FROM subregion_staging_runs
            WHERE score_version=? AND cohort_name=? AND subregion=? AND run_id=?
            """,
            (score_version, cohort_name, subregion, run_id),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"Run not found for scope: score_version={score_version}, "
                f"cohort_name={cohort_name}, subregion={subregion}, run_id={run_id}"
            )
        return str(row[0]), str(row[1]), int(row[2])

    row = conn.execute(
        """
        SELECT run_id, mode, k_final
        FROM subregion_staging_runs
        WHERE score_version=? AND cohort_name=? AND subregion=?
        ORDER BY created_utc DESC, run_id ASC
        LIMIT 1
        """,
        (score_version, cohort_name, subregion),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"No staging runs found for scope: score_version={score_version}, "
            f"cohort_name={cohort_name}, subregion={subregion}"
        )
    return str(row[0]), str(row[1]), int(row[2])


def _priority_score(size_n: int, p90_ly: float) -> float:
    return math.sqrt(float(size_n)) / (float(p90_ly) + 1.0)


def run_export(
    *,
    db_path: Path,
    score_version: str,
    cohort_name: str,
    subregion: str,
    outdir: Path,
    run_id: str | None = None,
) -> tuple[int, int]:
    outdir.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(db_path)) as conn:
        resolved_run_id, mode, k_final = _resolve_run(
            conn,
            score_version=score_version,
            cohort_name=cohort_name,
            subregion=subregion,
            run_id=run_id,
        )

        cluster_rows = conn.execute(
            """
            SELECT
                c.cluster_index,
                c.cluster_id,
                c.medoid_ring_id,
                c.size_n,
                c.radius_max_ly,
                c.radius_p90_ly,
                c.centroid_x,
                c.centroid_y,
                c.centroid_z
            FROM subregion_staging_clusters c
            WHERE c.score_version=? AND c.cohort_name=? AND c.subregion=? AND c.run_id=?
            ORDER BY c.cluster_index ASC
            """,
            (score_version, cohort_name, subregion, resolved_run_id),
        ).fetchall()
        if not cluster_rows:
            raise ValueError(
                f"No staging clusters found for scope: score_version={score_version}, "
                f"cohort_name={cohort_name}, subregion={subregion}, run_id={resolved_run_id}"
            )

        member_rows = conn.execute(
            """
            SELECT
                c.cluster_index,
                m.cluster_id,
                m.medoid_ring_id,
                m.ring_id,
                m.dist_to_medoid_ly,
                m.dist2_to_medoid
            FROM subregion_staging_members m
            JOIN subregion_staging_clusters c
              ON c.cluster_id = m.cluster_id
             AND c.run_id = m.run_id
             AND c.score_version = m.score_version
             AND c.cohort_name = m.cohort_name
             AND c.subregion = m.subregion
            WHERE m.score_version=? AND m.cohort_name=? AND m.subregion=? AND m.run_id=?
            ORDER BY c.cluster_index ASC, m.dist_to_medoid_ly ASC, m.ring_id ASC
            """,
            (score_version, cohort_name, subregion, resolved_run_id),
        ).fetchall()

    distances_by_cluster: dict[str, list[float]] = {}
    for row in member_rows:
        cluster_id = str(row[1])
        distances_by_cluster.setdefault(cluster_id, []).append(float(row[4]))
    for values in distances_by_cluster.values():
        values.sort()

    cluster_export_rows: list[dict[str, object]] = []
    for row in cluster_rows:
        cluster_index = int(row[0])
        cluster_id = str(row[1])
        medoid_ring_id = str(row[2])
        size_n = int(row[3])
        radius_max_ly = float(row[4])
        p90_from_cluster = None if row[5] is None else float(row[5])
        centroid_x = float(row[6])
        centroid_y = float(row[7])
        centroid_z = float(row[8])
        centroid_rho = math.sqrt((centroid_x * centroid_x) + (centroid_y * centroid_y) + (centroid_z * centroid_z))

        member_dists = distances_by_cluster.get(cluster_id, [])
        if member_dists:
            p90_ly = _quantile_floor(member_dists, 0.90)
        else:
            p90_ly = p90_from_cluster if p90_from_cluster is not None else 0.0
        priority = _priority_score(size_n, p90_ly)

        cluster_export_rows.append(
            {
                "score_version": score_version,
                "cohort_name": cohort_name,
                "subregion": subregion,
                "run_id": resolved_run_id,
                "mode": mode,
                "k_final": k_final,
                "cluster_index": cluster_index,
                "cluster_id": cluster_id,
                "medoid_ring_id": medoid_ring_id,
                "size_n": size_n,
                "radius_max_ly": radius_max_ly,
                "p90_ly": p90_ly,
                "centroid_x": centroid_x,
                "centroid_y": centroid_y,
                "centroid_z": centroid_z,
                "centroid_rho": centroid_rho,
                "priority_score": priority,
                "priority_reason": (
                    f"sqrt(size_n)/(p90_ly+1);n={size_n};p90={_fmt_float(p90_ly, places=3)}"
                ),
            }
        )

    cluster_export_rows.sort(
        key=lambda r: (
            -float(r["priority_score"]),
            float(r["p90_ly"]),
            -int(r["size_n"]),
            int(r["cluster_index"]),
        )
    )

    members_out_rows: list[dict[str, object]] = []
    for row in member_rows:
        members_out_rows.append(
            {
                "score_version": score_version,
                "cohort_name": cohort_name,
                "subregion": subregion,
                "run_id": resolved_run_id,
                "cluster_index": int(row[0]),
                "cluster_id": str(row[1]),
                "medoid_ring_id": str(row[2]),
                "ring_id": str(row[3]),
                "dist_to_medoid_ly": float(row[4]),
                "dist2_to_medoid": float(row[5]),
            }
        )

    cluster_index_to_rank = {int(row["cluster_index"]): idx for idx, row in enumerate(cluster_export_rows, start=1)}
    members_out_rows.sort(
        key=lambda r: (
            cluster_index_to_rank[int(r["cluster_index"])],
            int(r["cluster_index"]),
            float(r["dist_to_medoid_ly"]),
            str(r["ring_id"]),
        )
    )

    clusters_csv = outdir / "clusters.csv"
    with clusters_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CLUSTER_HEADERS)
        writer.writeheader()
        for row in cluster_export_rows:
            out = dict(row)
            out["radius_max_ly"] = _fmt_float(float(out["radius_max_ly"]))
            out["p90_ly"] = _fmt_float(float(out["p90_ly"]))
            out["centroid_x"] = _fmt_float(float(out["centroid_x"]))
            out["centroid_y"] = _fmt_float(float(out["centroid_y"]))
            out["centroid_z"] = _fmt_float(float(out["centroid_z"]))
            out["centroid_rho"] = _fmt_float(float(out["centroid_rho"]))
            out["priority_score"] = _fmt_float(float(out["priority_score"]), places=9)
            writer.writerow(out)

    members_csv = outdir / "members.csv"
    with members_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MEMBER_HEADERS)
        writer.writeheader()
        for row in members_out_rows:
            out = dict(row)
            out["dist_to_medoid_ly"] = _fmt_float(float(out["dist_to_medoid_ly"]))
            out["dist2_to_medoid"] = _fmt_float(float(out["dist2_to_medoid"]))
            writer.writerow(out)

    return len(cluster_export_rows), len(members_out_rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export deterministic cluster/member candidate CSVs from Phase 5 staging "
            "for a specific (score_version, cohort_name, subregion) scope."
        )
    )
    parser.add_argument("--db", required=True, help="Path to rings SQLite database.")
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION, help="Score version scope filter.")
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME, help="Cohort scope filter.")
    parser.add_argument("--subregion", required=True, help="Subregion scope filter (e.g., E-inner).")
    parser.add_argument(
        "--outdir",
        "--out-dir",
        dest="outdir",
        default=str(DEFAULT_OUTDIR),
        help="Output directory for clusters.csv and members.csv.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Optional run_id to export. If omitted, exporter selects latest run in scope "
            "by created_utc DESC, run_id ASC."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cluster_count, member_count = run_export(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        subregion=args.subregion,
        outdir=Path(args.outdir),
        run_id=args.run_id,
    )
    print(f"clusters.csv rows={cluster_count}")
    print(f"members.csv rows={member_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
