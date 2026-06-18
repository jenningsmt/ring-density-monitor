from __future__ import annotations

import argparse
import csv
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from scripts.rings.build_icy_route import ensure_phase5_schema
from scripts.rings.metric_resolver import resolve_moi_metric
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_RING_TYPE = "Metallic"
DEFAULT_RADIUS = 500.0
DEFAULT_COHORT_NAME = "MetTail"
EPS = 1e-12


@dataclass(frozen=True)
class Waypoint:
    seq: int
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Candidate:
    ring_id: str
    system_name: str | None
    body_name: str | None
    ring_name: str | None
    x: float
    y: float
    z: float
    moi_metric: float
    distance_to_route_ly: float
    source_waypoint_seq: int


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def load_waypoints(conn: sqlite3.Connection, route_id: str) -> list[Waypoint]:
    rows = conn.execute(
        """
        SELECT seq, x, y, z
        FROM expedition_waypoints
        WHERE route_id=?
        ORDER BY seq ASC
        """,
        (route_id,),
    ).fetchall()
    return [Waypoint(seq=int(row[0]), x=float(row[1]), y=float(row[2]), z=float(row[3])) for row in rows]


def _dist2(ax: float, ay: float, az: float, bx: float, by: float, bz: float) -> float:
    dx = ax - bx
    dy = ay - by
    dz = az - bz
    return dx * dx + dy * dy + dz * dz


def min_distance_to_route(x: float, y: float, z: float, waypoints: list[Waypoint]) -> tuple[float, int]:
    best_seq = -1
    best_d2 = float("inf")
    for wp in waypoints:
        d2 = _dist2(x, y, z, wp.x, wp.y, wp.z)
        if d2 < best_d2 - EPS:
            best_d2 = d2
            best_seq = wp.seq
        elif abs(d2 - best_d2) <= EPS and wp.seq < best_seq:
            best_seq = wp.seq
    return math.sqrt(best_d2), best_seq


def resolve_theta(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    use_theta: bool,
    theta_override: float | None,
) -> float:
    if theta_override is not None:
        return theta_override
    if not use_theta:
        raise RuntimeError("Theta mode requested without --use-theta and no --theta provided.")
    if not _table_exists(conn, "cohort_cutoffs"):
        raise RuntimeError("Missing cohort_cutoffs table required for --use-theta.")
    row = conn.execute(
        """
        SELECT theta_value
        FROM cohort_cutoffs
        WHERE score_version=? AND cohort_name=? AND ring_type=?
        LIMIT 1
        """,
        (score_version, DEFAULT_COHORT_NAME, ring_type),
    ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("No theta cutoff found for MetTail/Metallic.")
    return float(row[0])


def load_theta_candidates(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    moi_metric: str,
    theta: float,
) -> list[tuple[object, ...]]:
    rows = conn.execute(
        f"""
        SELECT
            r.ring_id, r.system_name, r.body_name, r.ring_name,
            r.x, r.y, r.z, s.{moi_metric}
        FROM rings_scored s
        JOIN rings_raw r ON r.ring_id = s.ring_id
        WHERE s.score_version=? AND s.ring_type=?
          AND s.{moi_metric} IS NOT NULL
          AND s.{moi_metric} >= ?
          AND r.x IS NOT NULL AND r.y IS NOT NULL AND r.z IS NOT NULL
        ORDER BY s.{moi_metric} DESC, r.ring_id ASC
        """,
        (score_version, ring_type, theta),
    ).fetchall()
    return rows


def choose_better_duplicate(new: Candidate, old: Candidate) -> Candidate:
    if new.moi_metric > old.moi_metric + EPS:
        return new
    if old.moi_metric > new.moi_metric + EPS:
        return old
    if new.ring_id < old.ring_id:
        return new
    if old.ring_id < new.ring_id:
        return old
    if new.distance_to_route_ly < old.distance_to_route_ly - EPS:
        return new
    if old.distance_to_route_ly < new.distance_to_route_ly - EPS:
        return old
    if new.source_waypoint_seq < old.source_waypoint_seq:
        return new
    return old


def select_with_theta(
    conn: sqlite3.Connection,
    route_id: str,
    waypoints: list[Waypoint],
    score_version: str,
    ring_type: str,
    moi_metric: str,
    radius_ly: float,
    theta: float,
) -> list[Candidate]:
    selected: dict[str, Candidate] = {}
    for row in load_theta_candidates(conn, score_version, ring_type, moi_metric, theta):
        ring_id = str(row[0])
        x = float(row[4])
        y = float(row[5])
        z = float(row[6])
        distance, seq = min_distance_to_route(x, y, z, waypoints)
        if distance > radius_ly + EPS:
            continue
        candidate = Candidate(
            ring_id=ring_id,
            system_name=row[1],
            body_name=row[2],
            ring_name=row[3],
            x=x,
            y=y,
            z=z,
            moi_metric=float(row[7]),
            distance_to_route_ly=distance,
            source_waypoint_seq=seq,
        )
        current = selected.get(ring_id)
        selected[ring_id] = candidate if current is None else choose_better_duplicate(candidate, current)
    return sorted(selected.values(), key=lambda c: (-c.moi_metric, c.ring_id))


def _rows_within_cube(
    conn: sqlite3.Connection,
    score_version: str,
    ring_type: str,
    moi_metric: str,
    x: float,
    y: float,
    z: float,
    radius: float,
) -> list[tuple[object, ...]]:
    return conn.execute(
        f"""
        SELECT
            r.ring_id, r.system_name, r.body_name, r.ring_name,
            r.x, r.y, r.z, s.{moi_metric}
        FROM rings_scored s
        JOIN rings_raw r ON r.ring_id = s.ring_id
        WHERE s.score_version=? AND s.ring_type=?
          AND s.{moi_metric} IS NOT NULL
          AND r.x BETWEEN ? AND ?
          AND r.y BETWEEN ? AND ?
          AND r.z BETWEEN ? AND ?
        ORDER BY s.{moi_metric} DESC, r.ring_id ASC
        """,
        (
            score_version,
            ring_type,
            x - radius,
            x + radius,
            y - radius,
            y + radius,
            z - radius,
            z + radius,
        ),
    ).fetchall()


def select_top_per_waypoint(
    conn: sqlite3.Connection,
    route_id: str,
    waypoints: list[Waypoint],
    score_version: str,
    ring_type: str,
    moi_metric: str,
    radius_ly: float,
    top_per_waypoint: int,
) -> list[Candidate]:
    selected: dict[str, Candidate] = {}
    for wp in waypoints:
        per_wp: list[Candidate] = []
        rows = _rows_within_cube(conn, score_version, ring_type, moi_metric, wp.x, wp.y, wp.z, radius_ly)
        for row in rows:
            x = float(row[4])
            y = float(row[5])
            z = float(row[6])
            d2 = _dist2(x, y, z, wp.x, wp.y, wp.z)
            if d2 > (radius_ly * radius_ly) + EPS:
                continue
            dist = math.sqrt(d2)
            per_wp.append(
                Candidate(
                    ring_id=str(row[0]),
                    system_name=row[1],
                    body_name=row[2],
                    ring_name=row[3],
                    x=x,
                    y=y,
                    z=z,
                    moi_metric=float(row[7]),
                    distance_to_route_ly=dist,
                    source_waypoint_seq=wp.seq,
                )
            )
            if len(per_wp) >= top_per_waypoint:
                break
        for cand in per_wp:
            global_dist, global_seq = min_distance_to_route(cand.x, cand.y, cand.z, waypoints)
            global_cand = Candidate(
                ring_id=cand.ring_id,
                system_name=cand.system_name,
                body_name=cand.body_name,
                ring_name=cand.ring_name,
                x=cand.x,
                y=cand.y,
                z=cand.z,
                moi_metric=cand.moi_metric,
                distance_to_route_ly=global_dist,
                source_waypoint_seq=global_seq,
            )
            existing = selected.get(global_cand.ring_id)
            selected[global_cand.ring_id] = (
                global_cand if existing is None else choose_better_duplicate(global_cand, existing)
            )

    return sorted(selected.values(), key=lambda c: (-c.moi_metric, c.ring_id))


def persist_metprox(conn: sqlite3.Connection, route_id: str, rows: list[Candidate]) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM metprox_members WHERE route_id=?", (route_id,))
        conn.executemany(
            """
            INSERT INTO metprox_members (
                route_id, ring_id, system_name, body_name, ring_name,
                x, y, z, moi_metric, distance_to_route_ly, source_waypoint_seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    route_id,
                    row.ring_id,
                    row.system_name,
                    row.body_name,
                    row.ring_name,
                    row.x,
                    row.y,
                    row.z,
                    row.moi_metric,
                    row.distance_to_route_ly,
                    row.source_waypoint_seq,
                )
                for row in rows
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def write_csv(out_path: Path, rows: list[Candidate]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "ring_id",
                "system_name",
                "body_name",
                "ring_name",
                "x",
                "y",
                "z",
                "moi_metric",
                "distance_to_route_ly",
                "source_waypoint_seq",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.ring_id,
                    row.system_name or "",
                    row.body_name or "",
                    row.ring_name or "",
                    f"{row.x:.8f}",
                    f"{row.y:.8f}",
                    f"{row.z:.8f}",
                    f"{row.moi_metric:.8f}",
                    f"{row.distance_to_route_ly:.8f}",
                    row.source_waypoint_seq,
                ]
            )


def build_metprox(
    db_path: Path,
    route_id: str,
    score_version: str = DEFAULT_SCORE_VERSION,
    moi_metric: str | None = None,
    ring_type: str = DEFAULT_RING_TYPE,
    radius_ly: float = DEFAULT_RADIUS,
    use_theta: bool = True,
    theta: float | None = None,
    top_per_waypoint: int | None = None,
    dry_run: bool = False,
    out: Path | None = None,
) -> dict[str, object]:
    with closing(sqlite3.connect(db_path)) as conn:
        for table in ("expedition_waypoints", "rings_raw", "rings_scored"):
            if not _table_exists(conn, table):
                raise RuntimeError(f"Missing required table: {table}")
        ensure_phase5_schema(conn)
        waypoints = load_waypoints(conn, route_id)
        if not waypoints:
            raise RuntimeError(f"No waypoints found for route_id={route_id}")
        metric = resolve_moi_metric(conn, preferred=moi_metric)

        if top_per_waypoint is not None:
            selected = select_top_per_waypoint(
                conn=conn,
                route_id=route_id,
                waypoints=waypoints,
                score_version=score_version,
                ring_type=ring_type,
                moi_metric=metric,
                radius_ly=radius_ly,
                top_per_waypoint=top_per_waypoint,
            )
            selection_mode = f"top_per_waypoint={top_per_waypoint}"
            theta_used = None
        else:
            theta_used = resolve_theta(conn, score_version, ring_type, use_theta=use_theta, theta_override=theta)
            selected = select_with_theta(
                conn=conn,
                route_id=route_id,
                waypoints=waypoints,
                score_version=score_version,
                ring_type=ring_type,
                moi_metric=metric,
                radius_ly=radius_ly,
                theta=theta_used,
            )
            selection_mode = f"theta={theta_used:.6f}"

        if not dry_run:
            persist_metprox(conn, route_id, selected)

    if out is not None:
        write_csv(out, selected)

    distances = [row.distance_to_route_ly for row in selected]
    stats = {
        "min": min(distances) if distances else None,
        "median": median(distances) if distances else None,
        "max": max(distances) if distances else None,
    }
    return {
        "route_id": route_id,
        "count": len(selected),
        "selection_mode": selection_mode,
        "moi_metric": metric,
        "radius_ly": radius_ly,
        "stats": stats,
        "theta_used": theta_used,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build MetProx members for a route.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--route-id", required=True)
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--moi-metric")
    parser.add_argument("--ring-type", default=DEFAULT_RING_TYPE)
    parser.add_argument("--radius-ly", type=float, default=DEFAULT_RADIUS)
    parser.add_argument("--use-theta", action="store_true", default=True)
    parser.add_argument("--theta", type=float)
    parser.add_argument("--top-per-waypoint", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Path(args.out) if args.out else None
    summary = build_metprox(
        db_path=Path(args.db),
        route_id=args.route_id,
        score_version=args.score_version,
        moi_metric=args.moi_metric,
        ring_type=args.ring_type,
        radius_ly=args.radius_ly,
        use_theta=args.use_theta,
        theta=args.theta,
        top_per_waypoint=args.top_per_waypoint,
        dry_run=args.dry_run,
        out=out,
    )
    stats = summary["stats"]
    print(
        f"route_id={summary['route_id']} selected={summary['count']} "
        f"mode={summary['selection_mode']} radius_ly={summary['radius_ly']}"
    )
    print(
        "distance_stats min={min} median={median} max={max}".format(
            min="None" if stats["min"] is None else f"{stats['min']:.6f}",
            median="None" if stats["median"] is None else f"{stats['median']:.6f}",
            max="None" if stats["max"] is None else f"{stats['max']:.6f}",
        )
    )
    if args.dry_run:
        print("Dry run: no DB writes performed.")
    if out is not None:
        print(f"Wrote CSV: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
