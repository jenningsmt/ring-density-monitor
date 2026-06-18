from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from contextlib import closing


DEFAULT_SCORE_VERSION = "moi_v1"
DEFAULT_COHORT_NAME = "IcyCore"
DEFAULT_SAGA_X = 25.21875
DEFAULT_SAGA_Y = -20.90625
DEFAULT_SAGA_Z = 25899.96875
Q33 = 0.3333333333
Q66 = 0.6666666667


@dataclass(frozen=True)
class InputRow:
    score_version: str
    cohort_name: str
    ring_id: str
    quadrant: str
    x: float
    y: float
    z: float
    system_name: str | None
    body_name: str | None
    ring_name: str | None
    moi_metric: float | None
    rank: int | None
    rho_ly: float


@dataclass(frozen=True)
class SubregionRow:
    base: InputRow
    band: str
    subregion: str


def apply_schema(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema_phase5_subregions.sql")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _rho(x: float, y: float, z: float, saga_xyz: tuple[float, float, float]) -> float:
    sx, sy, sz = saga_xyz
    return math.sqrt((x - sx) ** 2 + (y - sy) ** 2 + (z - sz) ** 2)


def _quantile_value(sorted_rhos: list[tuple[float, str]], q: float) -> float:
    idx = int(math.floor((len(sorted_rhos) - 1) * q))
    return sorted_rhos[idx][0]


def load_rows_from_icy_quadrants(
    conn: sqlite3.Connection,
    score_version: str,
    cohort_name: str,
    saga_xyz: tuple[float, float, float],
) -> list[InputRow]:
    if not _table_exists(conn, "icy_quadrants"):
        raise RuntimeError("Missing icy_quadrants table. Run assign_icy_quadrants first.")
    if not _table_exists(conn, "quadrant_summaries"):
        raise RuntimeError("Missing quadrant_summaries table. Run assign_icy_quadrants first.")

    quadrants = [
        row[0]
        for row in conn.execute(
            """
            SELECT quadrant
            FROM quadrant_summaries
            WHERE score_version=? AND cohort_name=?
            ORDER BY quadrant ASC
            """,
            (score_version, cohort_name),
        ).fetchall()
    ]
    if not quadrants:
        raise RuntimeError("No quadrant_summaries rows found for the requested cohort.")

    out: list[InputRow] = []
    for quadrant in quadrants:
        rows = conn.execute(
            """
            SELECT ring_id, x, y, z, system_name, body_name, ring_name, moi_metric, rank
            FROM icy_quadrants
            WHERE score_version=? AND cohort_name=? AND quadrant=?
            ORDER BY rank ASC, ring_id ASC
            """,
            (score_version, cohort_name, quadrant),
        ).fetchall()
        for row in rows:
            x = float(row[1])
            y = float(row[2])
            z = float(row[3])
            out.append(
                InputRow(
                    score_version=score_version,
                    cohort_name=cohort_name,
                    ring_id=row[0],
                    quadrant=quadrant,
                    x=x,
                    y=y,
                    z=z,
                    system_name=row[4],
                    body_name=row[5],
                    ring_name=row[6],
                    moi_metric=None if row[7] is None else float(row[7]),
                    rank=None if row[8] is None else int(row[8]),
                    rho_ly=_rho(x, y, z, saga_xyz),
                )
            )
    return out


def assign_bands(rows: list[InputRow]) -> list[SubregionRow]:
    by_quadrant: dict[str, list[InputRow]] = {}
    for row in rows:
        by_quadrant.setdefault(row.quadrant, []).append(row)

    out: list[SubregionRow] = []
    for quadrant in sorted(by_quadrant):
        group = by_quadrant[quadrant]
        if not group:
            continue
        sorted_rho = sorted((r.rho_ly, r.ring_id) for r in group)
        p33 = _quantile_value(sorted_rho, Q33)
        p66 = _quantile_value(sorted_rho, Q66)
        for row in sorted(group, key=lambda r: ((r.rank if r.rank is not None else 10**9), r.ring_id)):
            if row.rho_ly <= p33:
                band = "inner"
            elif row.rho_ly <= p66:
                band = "mid"
            else:
                band = "outer"
            out.append(SubregionRow(base=row, band=band, subregion=f"{quadrant}-{band}"))
    return out


def _radius(rows: list[SubregionRow], cx: float, cy: float, cz: float) -> float:
    rmax = 0.0
    for row in rows:
        d = math.sqrt((row.base.x - cx) ** 2 + (row.base.y - cy) ** 2 + (row.base.z - cz) ** 2)
        if d > rmax:
            rmax = d
    return rmax


def build_summaries(rows: list[SubregionRow]) -> dict[str, dict[str, object]]:
    by_subregion: dict[str, list[SubregionRow]] = {}
    for row in rows:
        by_subregion.setdefault(row.subregion, []).append(row)

    out: dict[str, dict[str, object]] = {}
    for subregion, group in sorted(by_subregion.items()):
        n = len(group)
        cx = sum(r.base.x for r in group) / n
        cy = sum(r.base.y for r in group) / n
        cz = sum(r.base.z for r in group) / n
        rho_vals = sorted(r.base.rho_ly for r in group)
        moi_vals = sorted(r.base.moi_metric for r in group if r.base.moi_metric is not None)
        out[subregion] = {
            "quadrant": group[0].base.quadrant,
            "band": group[0].band,
            "n": n,
            "centroid_x": cx,
            "centroid_y": cy,
            "centroid_z": cz,
            "radius_max_ly": _radius(group, cx, cy, cz),
            "rho_min": rho_vals[0],
            "rho_median": float(median(rho_vals)),
            "rho_max": rho_vals[-1],
            "moi_max": moi_vals[-1] if moi_vals else None,
            "moi_median": float(median(moi_vals)) if moi_vals else None,
            "min_ring_id": min(r.base.ring_id for r in group),
        }
    return out


def persist(
    conn: sqlite3.Connection,
    score_version: str,
    cohort_name: str,
    rows: list[SubregionRow],
    summaries: dict[str, dict[str, object]],
) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute(
            "DELETE FROM icy_subregions WHERE score_version=? AND cohort_name=?",
            (score_version, cohort_name),
        )
        conn.execute(
            "DELETE FROM subregion_summaries WHERE score_version=? AND cohort_name=?",
            (score_version, cohort_name),
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO icy_subregions (
                score_version, cohort_name, ring_id, quadrant, band, subregion, rho_ly,
                x, y, z, system_name, body_name, ring_name, moi_metric, rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    score_version,
                    cohort_name,
                    r.base.ring_id,
                    r.base.quadrant,
                    r.band,
                    r.subregion,
                    r.base.rho_ly,
                    r.base.x,
                    r.base.y,
                    r.base.z,
                    r.base.system_name,
                    r.base.body_name,
                    r.base.ring_name,
                    r.base.moi_metric,
                    r.base.rank,
                )
                for r in rows
            ],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO subregion_summaries (
                score_version, cohort_name, subregion, quadrant, band, n,
                centroid_x, centroid_y, centroid_z, radius_max_ly,
                rho_min, rho_median, rho_max,
                moi_max, moi_median, min_ring_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    score_version,
                    cohort_name,
                    subregion,
                    str(s["quadrant"]),
                    str(s["band"]),
                    int(s["n"]),
                    float(s["centroid_x"]),
                    float(s["centroid_y"]),
                    float(s["centroid_z"]),
                    float(s["radius_max_ly"]),
                    float(s["rho_min"]),
                    float(s["rho_median"]),
                    float(s["rho_max"]),
                    s["moi_max"],
                    s["moi_median"],
                    str(s["min_ring_id"]),
                )
                for subregion, s in sorted(summaries.items())
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def assign_subregions(
    db_path: Path,
    score_version: str = DEFAULT_SCORE_VERSION,
    cohort_name: str = DEFAULT_COHORT_NAME,
    saga_x: float = DEFAULT_SAGA_X,
    saga_y: float = DEFAULT_SAGA_Y,
    saga_z: float = DEFAULT_SAGA_Z,
    dry_run: bool = False,
) -> dict[str, object]:
    with closing(sqlite3.connect(db_path)) as conn:
        apply_schema(conn)
        rows_in = load_rows_from_icy_quadrants(
            conn=conn,
            score_version=score_version,
            cohort_name=cohort_name,
            saga_xyz=(saga_x, saga_y, saga_z),
        )
        rows = assign_bands(rows_in)
        summaries = build_summaries(rows)
        if not dry_run:
            persist(conn, score_version, cohort_name, rows, summaries)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.subregion] = counts.get(row.subregion, 0) + 1
    return {
        "total": len(rows),
        "counts": dict(sorted(counts.items())),
        "summaries": summaries,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persist IcyCore SagA radial subregions (quadrant x inner/mid/outer).")
    parser.add_argument("--db", required=True)
    parser.add_argument("--score-version", default=DEFAULT_SCORE_VERSION)
    parser.add_argument("--cohort-name", default=DEFAULT_COHORT_NAME)
    parser.add_argument("--saga-x", type=float, default=DEFAULT_SAGA_X)
    parser.add_argument("--saga-y", type=float, default=DEFAULT_SAGA_Y)
    parser.add_argument("--saga-z", type=float, default=DEFAULT_SAGA_Z)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = assign_subregions(
        db_path=Path(args.db),
        score_version=args.score_version,
        cohort_name=args.cohort_name,
        saga_x=args.saga_x,
        saga_y=args.saga_y,
        saga_z=args.saga_z,
        dry_run=args.dry_run,
    )
    print(f"assigned={summary['total']}")
    for subregion in sorted(summary["counts"]):
        s = summary["summaries"][subregion]
        print(
            f"{subregion}: n={summary['counts'][subregion]} radius_max_ly={s['radius_max_ly']:.3f} "
            f"rho_min/med/max={s['rho_min']:.3f}/{s['rho_median']:.3f}/{s['rho_max']:.3f}"
        )
    if args.dry_run:
        print("Dry run: no DB writes performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
