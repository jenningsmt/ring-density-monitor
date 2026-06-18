from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.rings.metric_resolver import resolve_moi_metric
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_ALGO_VERSION = "route_nn_v1"
EPS = 1e-12


@dataclass(frozen=True)
class RingPoint:
    ring_id: str
    system_name: str | None
    body_name: str | None
    ring_name: str | None
    x: float
    y: float
    z: float
    moi_value: float | None


@dataclass(frozen=True)
class Waypoint:
    seq: int
    ring_id: str
    system_name: str | None
    body_name: str | None
    ring_name: str | None
    x: float
    y: float
    z: float
    step_distance_ly: float
    cumulative_distance_ly: float


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_phase5_schema(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema_phase5_routes.sql")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()


def _cohort_rank_column(conn: sqlite3.Connection) -> str:
    cols = _table_columns(conn, "cohort_members")
    if "rank" in cols:
        return "rank"
    if "rank_in_cohort" in cols:
        return "rank_in_cohort"
    raise RuntimeError("cohort_members must include rank or rank_in_cohort.")


def load_cohort_points(
    conn: sqlite3.Connection,
    score_version: str,
    cohort_name: str,
    moi_metric: str,
    limit: int | None,
) -> list[RingPoint]:
    rank_col = _cohort_rank_column(conn)
    limit_clause = " LIMIT ?" if limit is not None else ""
    params: list[object] = [score_version, cohort_name, score_version]
    if limit is not None:
        params.append(limit)
    rows = conn.execute(
        f"""
        SELECT
            cm.ring_id,
            r.system_name,
            r.body_name,
            r.ring_name,
            r.x, r.y, r.z,
            s.{moi_metric}
        FROM cohort_members cm
        JOIN rings_raw r ON r.ring_id = cm.ring_id
        LEFT JOIN rings_scored s ON s.ring_id = cm.ring_id AND s.score_version = ?
        WHERE cm.score_version=? AND cm.cohort_name=?
          AND r.x IS NOT NULL AND r.y IS NOT NULL AND r.z IS NOT NULL
        ORDER BY cm.{rank_col} ASC, cm.ring_id ASC
        {limit_clause}
        """,
        (score_version, score_version, cohort_name, *( [limit] if limit is not None else [] )),
    ).fetchall()
    return [
        RingPoint(
            ring_id=row[0],
            system_name=row[1],
            body_name=row[2],
            ring_name=row[3],
            x=float(row[4]),
            y=float(row[5]),
            z=float(row[6]),
            moi_value=None if row[7] is None else float(row[7]),
        )
        for row in rows
    ]


def fetch_ring_anchor(conn: sqlite3.Connection, ring_id: str) -> tuple[float, float, float]:
    row = conn.execute(
        """
        SELECT x, y, z
        FROM rings_raw
        WHERE ring_id=? AND x IS NOT NULL AND y IS NOT NULL AND z IS NOT NULL
        LIMIT 1
        """,
        (ring_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Anchor ring_id '{ring_id}' has no coordinates in rings_raw.")
    return float(row[0]), float(row[1]), float(row[2])


def choose_best_moi_anchor(points: list[RingPoint]) -> str:
    if not points:
        raise RuntimeError("No cohort points available to choose best_moi anchor.")
    ranked = sorted(
        points,
        key=lambda p: (
            0 if p.moi_value is not None else 1,
            -(p.moi_value if p.moi_value is not None else -1e30),
            p.ring_id,
        ),
    )
    return ranked[0].ring_id


def _dist2(px: float, py: float, pz: float, qx: float, qy: float, qz: float) -> float:
    dx = px - qx
    dy = py - qy
    dz = pz - qz
    return dx * dx + dy * dy + dz * dz


def _candidate_better(dist2_new: float, cand_new: RingPoint, dist2_old: float, cand_old: RingPoint) -> bool:
    if dist2_new < dist2_old - EPS:
        return True
    if abs(dist2_new - dist2_old) > EPS:
        return False

    moi_new = cand_new.moi_value if cand_new.moi_value is not None else -1e30
    moi_old = cand_old.moi_value if cand_old.moi_value is not None else -1e30
    if moi_new > moi_old + EPS:
        return True
    if abs(moi_new - moi_old) > EPS:
        return False

    return cand_new.ring_id < cand_old.ring_id


def build_greedy_waypoints(points: list[RingPoint], anchor_x: float, anchor_y: float, anchor_z: float) -> list[Waypoint]:
    remaining = list(points)
    current_x = anchor_x
    current_y = anchor_y
    current_z = anchor_z
    cumulative = 0.0
    out: list[Waypoint] = []
    seq = 1

    while remaining:
        best_idx = 0
        best = remaining[0]
        best_d2 = _dist2(current_x, current_y, current_z, best.x, best.y, best.z)
        for idx in range(1, len(remaining)):
            candidate = remaining[idx]
            cand_d2 = _dist2(current_x, current_y, current_z, candidate.x, candidate.y, candidate.z)
            if _candidate_better(cand_d2, candidate, best_d2, best):
                best_idx = idx
                best = candidate
                best_d2 = cand_d2

        step = math.sqrt(best_d2)
        cumulative += step
        out.append(
            Waypoint(
                seq=seq,
                ring_id=best.ring_id,
                system_name=best.system_name,
                body_name=best.body_name,
                ring_name=best.ring_name,
                x=best.x,
                y=best.y,
                z=best.z,
                step_distance_ly=step,
                cumulative_distance_ly=cumulative,
            )
        )
        seq += 1
        current_x, current_y, current_z = best.x, best.y, best.z
        del remaining[best_idx]

    return out


def make_route_id(
    score_version: str,
    cohort_name: str,
    algo_version: str,
    anchor_mode: str,
    anchor_ring_id: str | None,
    anchor_xyz: tuple[float, float, float],
    count: int,
) -> str:
    if anchor_ring_id:
        anchor_part = anchor_ring_id
    else:
        anchor_part = f"{anchor_xyz[0]:.8f},{anchor_xyz[1]:.8f},{anchor_xyz[2]:.8f}"
    raw = f"{score_version}|{cohort_name}|{algo_version}|{anchor_mode}|{anchor_part}|{count}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def persist_route(
    conn: sqlite3.Connection,
    route_id: str,
    score_version: str,
    cohort_name: str,
    algo_version: str,
    moi_metric: str,
    anchor_mode: str,
    anchor_xyz: tuple[float, float, float],
    anchor_ring_id: str | None,
    waypoints: list[Waypoint],
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    total_distance = waypoints[-1].cumulative_distance_ly if waypoints else 0.0
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM expedition_waypoints WHERE route_id=?", (route_id,))
        conn.execute("DELETE FROM expedition_routes WHERE route_id=?", (route_id,))
        conn.execute(
            """
            INSERT INTO expedition_routes (
                route_id, score_version, cohort_name, algo_version, moi_metric,
                created_at, anchor_mode, anchor_x, anchor_y, anchor_z,
                anchor_ring_id, waypoint_count, total_distance_ly
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                route_id,
                score_version,
                cohort_name,
                algo_version,
                moi_metric,
                created_at,
                anchor_mode,
                anchor_xyz[0],
                anchor_xyz[1],
                anchor_xyz[2],
                anchor_ring_id,
                len(waypoints),
                total_distance,
            ),
        )
        conn.executemany(
            """
            INSERT INTO expedition_waypoints (
                route_id, seq, ring_id, system_name, body_name, ring_name,
                x, y, z, step_distance_ly, cumulative_distance_ly
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    route_id,
                    wp.seq,
                    wp.ring_id,
                    wp.system_name,
                    wp.body_name,
                    wp.ring_name,
                    wp.x,
                    wp.y,
                    wp.z,
                    wp.step_distance_ly,
                    wp.cumulative_distance_ly,
                )
                for wp in waypoints
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def write_waypoints_csv(out_path: Path, waypoints: list[Waypoint]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "seq",
                "ring_id",
                "system_name",
                "body_name",
                "ring_name",
                "x",
                "y",
                "z",
                "step_distance_ly",
                "cumulative_distance_ly",
            ]
        )
        for wp in waypoints:
            writer.writerow(
                [
                    wp.seq,
                    wp.ring_id,
                    wp.system_name or "",
                    wp.body_name or "",
                    wp.ring_name or "",
                    f"{wp.x:.8f}",
                    f"{wp.y:.8f}",
                    f"{wp.z:.8f}",
                    f"{wp.step_distance_ly:.8f}",
                    f"{wp.cumulative_distance_ly:.8f}",
                ]
            )


def build_route(
    db_path: Path,
    score_version: str = DEFAULT_SCORE_VERSION,
    cohort_name: str = DEFAULT_COHORT_NAME,
    moi_metric: str | None = None,
    algo_version: str = DEFAULT_ALGO_VERSION,
    anchor_mode: str = "best_moi",
    anchor_ring_id: str | None = None,
    anchor_xyz: tuple[float, float, float] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    out_csv: Path | None = None,
) -> dict[str, object]:
    with closing(sqlite3.connect(db_path)) as conn:
        for table in ("rings_raw", "rings_scored", "cohort_members"):
            if not _table_exists(conn, table):
                raise RuntimeError(f"Missing required table: {table}")
        ensure_phase5_schema(conn)
        resolved_metric = resolve_moi_metric(conn, preferred=moi_metric)
        points = load_cohort_points(conn, score_version, cohort_name, resolved_metric, limit)
        if not points:
            raise RuntimeError("No cohort points with coordinates found for route construction.")

        effective_anchor_mode = anchor_mode
        chosen_anchor_ring = anchor_ring_id
        if chosen_anchor_ring:
            ax, ay, az = fetch_ring_anchor(conn, chosen_anchor_ring)
            effective_anchor_mode = "best_moi"
        elif anchor_mode == "explicit_xyz":
            if anchor_xyz is None:
                raise RuntimeError("anchor_x/anchor_y/anchor_z are required for explicit_xyz mode.")
            ax, ay, az = anchor_xyz
        else:
            chosen_anchor_ring = choose_best_moi_anchor(points)
            ax, ay, az = fetch_ring_anchor(conn, chosen_anchor_ring)

        waypoints = build_greedy_waypoints(points, ax, ay, az)
        route_id = make_route_id(
            score_version=score_version,
            cohort_name=cohort_name,
            algo_version=algo_version,
            anchor_mode=effective_anchor_mode,
            anchor_ring_id=chosen_anchor_ring,
            anchor_xyz=(ax, ay, az),
            count=len(waypoints),
        )

        if not dry_run:
            persist_route(
                conn=conn,
                route_id=route_id,
                score_version=score_version,
                cohort_name=cohort_name,
                algo_version=algo_version,
                moi_metric=resolved_metric,
                anchor_mode=effective_anchor_mode,
                anchor_xyz=(ax, ay, az),
                anchor_ring_id=chosen_anchor_ring,
                waypoints=waypoints,
            )

    if out_csv is not None:
        write_waypoints_csv(out_csv, waypoints)

    total_distance = waypoints[-1].cumulative_distance_ly if waypoints else 0.0
    summary = {
        "route_id": route_id,
        "waypoint_count": len(waypoints),
        "total_distance_ly": total_distance,
        "anchor_mode": effective_anchor_mode,
        "anchor_ring_id": chosen_anchor_ring,
        "anchor_xyz": (ax, ay, az),
        "moi_metric": resolved_metric,
    }
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build deterministic greedy nearest-neighbor route for Icy cohort.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME)
    parser.add_argument("--moi-metric")
    parser.add_argument("--algo-version", default=DEFAULT_ALGO_VERSION)
    parser.add_argument("--anchor-mode", choices=("best_moi", "explicit_xyz"), default="best_moi")
    parser.add_argument("--anchor-ring-id")
    parser.add_argument("--anchor-x", type=float)
    parser.add_argument("--anchor-y", type=float)
    parser.add_argument("--anchor-z", type=float)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    anchor_xyz = None
    if args.anchor_mode == "explicit_xyz":
        if args.anchor_x is None or args.anchor_y is None or args.anchor_z is None:
            raise SystemExit("anchor-x, anchor-y, anchor-z are required when --anchor-mode=explicit_xyz")
        anchor_xyz = (args.anchor_x, args.anchor_y, args.anchor_z)
    out_csv = Path(args.out) if args.out else None

    summary = build_route(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        moi_metric=args.moi_metric,
        algo_version=args.algo_version,
        anchor_mode=args.anchor_mode,
        anchor_ring_id=args.anchor_ring_id,
        anchor_xyz=anchor_xyz,
        limit=args.limit,
        dry_run=args.dry_run,
        out_csv=out_csv,
    )
    print(
        "route_id={route_id} waypoint_count={waypoint_count} total_distance_ly={total_distance_ly:.6f} "
        "anchor_mode={anchor_mode} anchor_ring_id={anchor_ring_id} anchor_xyz={anchor_xyz}".format(**summary)
    )
    if args.dry_run:
        print("Dry run: no DB writes performed.")
    if out_csv is not None:
        print(f"Wrote waypoint CSV: {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
