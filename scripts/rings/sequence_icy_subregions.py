from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_START_X = 11272.3125
DEFAULT_START_Y = -164.9375
DEFAULT_START_Z = 34440.625
DEFAULT_OUT = Path("docs/ring_hunter/icycore_subregion_itinerary.md")
EPS = 1e-12


@dataclass(frozen=True)
class SubregionSummary:
    subregion: str
    n: int
    centroid_x: float
    centroid_y: float
    centroid_z: float
    radius_max_ly: float


@dataclass(frozen=True)
class EntryRing:
    ring_id: str
    system_name: str | None
    body_name: str | None
    ring_name: str | None
    moi_metric: float | None
    x: float
    y: float
    z: float
    distance_from_previous: float


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def load_summaries(conn: sqlite3.Connection, score_version: str, cohort_name: str) -> list[SubregionSummary]:
    rows = conn.execute(
        """
        SELECT subregion, n, centroid_x, centroid_y, centroid_z, radius_max_ly
        FROM subregion_summaries
        WHERE score_version=? AND cohort_name=?
        ORDER BY subregion ASC
        """,
        (score_version, cohort_name),
    ).fetchall()
    return [
        SubregionSummary(
            subregion=row[0],
            n=int(row[1]),
            centroid_x=float(row[2]),
            centroid_y=float(row[3]),
            centroid_z=float(row[4]),
            radius_max_ly=float(row[5]),
        )
        for row in rows
    ]


def sequence_subregions(summaries: list[SubregionSummary], start_xyz: tuple[float, float, float]) -> list[SubregionSummary]:
    remaining = list(summaries)
    ordered: list[SubregionSummary] = []
    current = start_xyz
    while remaining:
        best = remaining[0]
        best_d = _dist(current, (best.centroid_x, best.centroid_y, best.centroid_z))
        for cand in remaining[1:]:
            d = _dist(current, (cand.centroid_x, cand.centroid_y, cand.centroid_z))
            if d < best_d - EPS:
                best = cand
                best_d = d
            elif abs(d - best_d) <= EPS:
                if cand.radius_max_ly < best.radius_max_ly - EPS:
                    best = cand
                    best_d = d
                elif abs(cand.radius_max_ly - best.radius_max_ly) <= EPS and cand.subregion < best.subregion:
                    best = cand
                    best_d = d
        ordered.append(best)
        remaining.remove(best)
        current = (best.centroid_x, best.centroid_y, best.centroid_z)
    return ordered


def choose_entry_ring(
    conn: sqlite3.Connection,
    score_version: str,
    cohort_name: str,
    subregion: str,
    from_xyz: tuple[float, float, float],
) -> EntryRing:
    rows = conn.execute(
        """
        SELECT ring_id, system_name, body_name, ring_name, moi_metric, x, y, z
        FROM icy_subregions
        WHERE score_version=? AND cohort_name=? AND subregion=?
        ORDER BY ring_id ASC
        """,
        (score_version, cohort_name, subregion),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"No rings in subregion {subregion}.")

    best = rows[0]
    best_d = _dist(from_xyz, (float(best[5]), float(best[6]), float(best[7])))
    for row in rows[1:]:
        d = _dist(from_xyz, (float(row[5]), float(row[6]), float(row[7])))
        if d < best_d - EPS:
            best = row
            best_d = d
        elif abs(d - best_d) <= EPS:
            best_moi = float(best[4]) if best[4] is not None else float("-inf")
            cand_moi = float(row[4]) if row[4] is not None else float("-inf")
            if cand_moi > best_moi + EPS:
                best = row
                best_d = d
            elif abs(cand_moi - best_moi) <= EPS and str(row[0]) < str(best[0]):
                best = row
                best_d = d

    return EntryRing(
        ring_id=str(best[0]),
        system_name=best[1],
        body_name=best[2],
        ring_name=best[3],
        moi_metric=None if best[4] is None else float(best[4]),
        x=float(best[5]),
        y=float(best[6]),
        z=float(best[7]),
        distance_from_previous=best_d,
    )


def write_itinerary(
    db_path: Path,
    score_version: str = DEFAULT_SCORE_VERSION,
    cohort_name: str = DEFAULT_COHORT_NAME,
    start_xyz: tuple[float, float, float] = (DEFAULT_START_X, DEFAULT_START_Y, DEFAULT_START_Z),
    out_path: Path = DEFAULT_OUT,
) -> dict[str, object]:
    with closing(sqlite3.connect(db_path)) as conn:
        summaries = load_summaries(conn, score_version, cohort_name)
        if not summaries:
            raise RuntimeError("No subregion_summaries rows found. Run assign_icy_subregions first.")
        ordered = sequence_subregions(summaries, start_xyz)

        rows: list[dict[str, object]] = []
        centroid_current = start_xyz
        entry_current = start_xyz
        for idx, s in enumerate(ordered, start=1):
            centroid_xyz = (s.centroid_x, s.centroid_y, s.centroid_z)
            jump = _dist(centroid_current, centroid_xyz)
            entry = choose_entry_ring(conn, score_version, cohort_name, s.subregion, entry_current)
            rows.append(
                {
                    "step": idx,
                    "subregion": s.subregion,
                    "n": s.n,
                    "centroid_x": s.centroid_x,
                    "centroid_y": s.centroid_y,
                    "centroid_z": s.centroid_z,
                    "radius_max_ly": s.radius_max_ly,
                    "jump_distance_ly": jump,
                    "entry_ring_id": entry.ring_id,
                    "entry_system_name": entry.system_name or "",
                    "entry_body_name": entry.body_name or "",
                    "entry_ring_name": entry.ring_name or "",
                    "entry_moi_metric": "" if entry.moi_metric is None else f"{entry.moi_metric:.6f}",
                    "entry_distance_from_previous": entry.distance_from_previous,
                }
            )
            centroid_current = centroid_xyz
            entry_current = (entry.x, entry.y, entry.z)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# IcyCore Subregion Itinerary",
        "",
        f"- DB: `{db_path}`",
        f"- score_version: `{score_version}`",
        f"- cohort_name: `{cohort_name}`",
        f"- start_xyz: ({start_xyz[0]:.6f}, {start_xyz[1]:.6f}, {start_xyz[2]:.6f})",
        "",
        "| step | subregion | n | centroid_x | centroid_y | centroid_z | radius_max_ly | jump_distance_ly | entry_ring_id | entry_system_name | entry_body_name | entry_ring_name | entry_moi_metric | distance_from_previous |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {step} | {subregion} | {n} | {centroid_x:.6f} | {centroid_y:.6f} | {centroid_z:.6f} | "
            "{radius_max_ly:.6f} | {jump_distance_ly:.6f} | {entry_ring_id} | {entry_system_name} | {entry_body_name} | "
            "{entry_ring_name} | {entry_moi_metric} | {entry_distance_from_previous:.6f} |".format(**row)
        )
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return {"order": [row["subregion"] for row in rows], "rows": rows}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sequence IcyCore subregions into deterministic itinerary.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME)
    parser.add_argument("--start-x", type=float, default=DEFAULT_START_X)
    parser.add_argument("--start-y", type=float, default=DEFAULT_START_Y)
    parser.add_argument("--start-z", type=float, default=DEFAULT_START_Z)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = write_itinerary(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        start_xyz=(args.start_x, args.start_y, args.start_z),
        out_path=Path(args.out),
    )
    print(f"subregion_order={','.join(result['order'])}")
    print(f"Wrote itinerary: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
