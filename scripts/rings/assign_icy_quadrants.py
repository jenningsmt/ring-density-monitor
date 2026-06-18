from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from scripts.rings.metric_resolver import resolve_moi_metric
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_SAGA_X = 25.21875
DEFAULT_SAGA_Y = -20.90625
DEFAULT_SAGA_Z = 25899.96875


@dataclass(frozen=True)
class QuadrantRow:
    score_version: str
    cohort_name: str
    ring_id: str
    quadrant: str
    theta_deg: float
    dx: float
    dz: float
    x: float
    y: float
    z: float
    system_name: str | None
    body_name: str | None
    ring_name: str | None
    moi_metric: float | None
    rank: int


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _cohort_rank_column(conn: sqlite3.Connection) -> str:
    cols = _table_columns(conn, "cohort_members")
    if "rank" in cols:
        return "rank"
    if "rank_in_cohort" in cols:
        return "rank_in_cohort"
    raise RuntimeError("cohort_members must include rank or rank_in_cohort.")


def apply_schema(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema_phase5_quadrants.sql")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()


def compute_theta_and_quadrant(x: float, z: float, saga_x: float, saga_z: float) -> tuple[float, float, float, str]:
    dx = x - saga_x
    dz = z - saga_z
    theta = math.degrees(math.atan2(dz, dx))
    if -45.0 <= theta < 45.0:
        quadrant = "E"
    elif 45.0 <= theta < 135.0:
        quadrant = "N"
    elif theta >= 135.0 or theta < -135.0:
        quadrant = "W"
    else:
        quadrant = "S"
    return dx, dz, theta, quadrant


def load_quadrant_rows(
    conn: sqlite3.Connection,
    score_version: str,
    cohort_name: str,
    moi_metric: str,
    saga_x: float,
    saga_z: float,
) -> tuple[list[QuadrantRow], int]:
    rank_col = _cohort_rank_column(conn)
    rows = conn.execute(
        f"""
        SELECT
            cm.ring_id,
            cm.{rank_col},
            rr.system_name,
            rr.body_name,
            rr.ring_name,
            rr.x, rr.y, rr.z,
            rs.{moi_metric}
        FROM cohort_members cm
        JOIN rings_raw rr ON rr.ring_id = cm.ring_id
        LEFT JOIN rings_scored rs ON rs.ring_id = cm.ring_id AND rs.score_version = cm.score_version
        WHERE cm.score_version=? AND cm.cohort_name=?
        ORDER BY cm.{rank_col} ASC, cm.ring_id ASC
        """,
        (score_version, cohort_name),
    ).fetchall()

    out: list[QuadrantRow] = []
    skipped = 0
    for row in rows:
        if row[5] is None or row[6] is None or row[7] is None:
            skipped += 1
            continue
        x = float(row[5])
        y = float(row[6])
        z = float(row[7])
        dx, dz, theta, quadrant = compute_theta_and_quadrant(x, z, saga_x, saga_z)
        out.append(
            QuadrantRow(
                score_version=score_version,
                cohort_name=cohort_name,
                ring_id=row[0],
                quadrant=quadrant,
                theta_deg=theta,
                dx=dx,
                dz=dz,
                x=x,
                y=y,
                z=z,
                system_name=row[2],
                body_name=row[3],
                ring_name=row[4],
                moi_metric=None if row[8] is None else float(row[8]),
                rank=int(row[1]),
            )
        )
    return out, skipped


def _radius_from_centroid(rows: list[QuadrantRow], cx: float, cy: float, cz: float) -> float:
    radius = 0.0
    for row in rows:
        d = math.sqrt((row.x - cx) ** 2 + (row.y - cy) ** 2 + (row.z - cz) ** 2)
        if d > radius:
            radius = d
    return radius


def _compute_summaries(rows: list[QuadrantRow]) -> dict[str, dict[str, object]]:
    by_quadrant: dict[str, list[QuadrantRow]] = {}
    for row in rows:
        by_quadrant.setdefault(row.quadrant, []).append(row)

    summaries: dict[str, dict[str, object]] = {}
    for quadrant, group in by_quadrant.items():
        n = len(group)
        cx = sum(r.x for r in group) / n
        cy = sum(r.y for r in group) / n
        cz = sum(r.z for r in group) / n
        radius = _radius_from_centroid(group, cx, cy, cz)
        moi_values = sorted(r.moi_metric for r in group if r.moi_metric is not None)
        moi_max = moi_values[-1] if moi_values else None
        moi_med = median(moi_values) if moi_values else None
        min_ring = min(r.ring_id for r in group)
        summaries[quadrant] = {
            "n": n,
            "centroid_x": cx,
            "centroid_y": cy,
            "centroid_z": cz,
            "radius_max_ly": radius,
            "moi_max": moi_max,
            "moi_median": moi_med,
            "min_ring_id": min_ring,
        }
    return summaries


def persist_rows_and_summaries(conn: sqlite3.Connection, rows: list[QuadrantRow], summaries: dict[str, dict[str, object]]) -> None:
    if not rows:
        return
    score_version = rows[0].score_version
    cohort_name = rows[0].cohort_name

    conn.execute("BEGIN")
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO icy_quadrants (
                score_version, cohort_name, ring_id, quadrant, theta_deg, dx, dz,
                x, y, z, system_name, body_name, ring_name, moi_metric, rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.score_version,
                    r.cohort_name,
                    r.ring_id,
                    r.quadrant,
                    r.theta_deg,
                    r.dx,
                    r.dz,
                    r.x,
                    r.y,
                    r.z,
                    r.system_name,
                    r.body_name,
                    r.ring_name,
                    r.moi_metric,
                    r.rank,
                )
                for r in rows
            ],
        )

        conn.execute(
            "DELETE FROM quadrant_summaries WHERE score_version=? AND cohort_name=?",
            (score_version, cohort_name),
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO quadrant_summaries (
                score_version, cohort_name, quadrant, n,
                centroid_x, centroid_y, centroid_z, radius_max_ly,
                moi_max, moi_median, min_ring_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    score_version,
                    cohort_name,
                    quadrant,
                    int(s["n"]),
                    float(s["centroid_x"]),
                    float(s["centroid_y"]),
                    float(s["centroid_z"]),
                    float(s["radius_max_ly"]),
                    s["moi_max"],
                    s["moi_median"],
                    str(s["min_ring_id"]),
                )
                for quadrant, s in sorted(summaries.items())
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def assign_quadrants(
    db_path: Path,
    score_version: str = DEFAULT_SCORE_VERSION,
    cohort_name: str = DEFAULT_COHORT_NAME,
    moi_metric: str | None = None,
    saga_x: float = DEFAULT_SAGA_X,
    saga_y: float = DEFAULT_SAGA_Y,
    saga_z: float = DEFAULT_SAGA_Z,
    dry_run: bool = False,
) -> dict[str, object]:
    del saga_y  # Y not used for XZ-plane quadrant calculation.
    with closing(sqlite3.connect(db_path)) as conn:
        apply_schema(conn)
        resolved_metric = resolve_moi_metric(conn, preferred=moi_metric)
        rows, skipped = load_quadrant_rows(conn, score_version, cohort_name, resolved_metric, saga_x, saga_z)
        summaries = _compute_summaries(rows)
        if not dry_run:
            persist_rows_and_summaries(conn, rows, summaries)

    counts = {q: int(summaries[q]["n"]) for q in sorted(summaries)}
    return {
        "score_version": score_version,
        "cohort_name": cohort_name,
        "moi_metric": resolved_metric,
        "total": len(rows),
        "skipped_missing_coords": skipped,
        "counts": counts,
        "summaries": summaries,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assign IcyCore rings to directional quadrants around Sag A*.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME)
    parser.add_argument("--moi-metric")
    parser.add_argument("--saga-x", type=float, default=DEFAULT_SAGA_X)
    parser.add_argument("--saga-y", type=float, default=DEFAULT_SAGA_Y)
    parser.add_argument("--saga-z", type=float, default=DEFAULT_SAGA_Z)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = assign_quadrants(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        moi_metric=args.moi_metric,
        saga_x=args.saga_x,
        saga_y=args.saga_y,
        saga_z=args.saga_z,
        dry_run=args.dry_run,
    )
    print(
        f"assigned={summary['total']} skipped_missing_coords={summary['skipped_missing_coords']} "
        f"moi_metric={summary['moi_metric']}"
    )
    for quadrant in ("E", "N", "W", "S"):
        if quadrant in summary["summaries"]:
            s = summary["summaries"][quadrant]
            print(
                f"{quadrant}: n={s['n']} centroid=({s['centroid_x']:.3f},{s['centroid_y']:.3f},{s['centroid_z']:.3f}) "
                f"radius_max_ly={s['radius_max_ly']:.3f}"
            )
    if args.dry_run:
        print("Dry run: no DB writes performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
