from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_TOP = 100
DEFAULT_SCORE_VERSION = "moi_v1"


@dataclass(frozen=True)
class RingRow:
    ring_id: str
    system_name: str
    body_name: str
    ring_name: str
    x: Optional[float]
    y: Optional[float]
    z: Optional[float]
    score: float
    arrival_distance_ls: Optional[float]
    surface_density: Optional[float]
    linear_density: Optional[float]


def _distance_ly(
    ax: Optional[float],
    ay: Optional[float],
    az: Optional[float],
    bx: Optional[float],
    by: Optional[float],
    bz: Optional[float],
) -> float:
    if None in (ax, ay, az, bx, by, bz):
        return 0.0
    dx = float(ax) - float(bx)
    dy = float(ay) - float(by)
    dz = float(az) - float(bz)
    return math.sqrt((dx * dx) + (dy * dy) + (dz * dz))


def _query_rows(
    conn: sqlite3.Connection,
    command: str,
    top_n: int,
    score_version: str,
) -> list[RingRow]:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(rings_raw)").fetchall()}
    x_col = "system_x" if "system_x" in columns else "x"
    y_col = "system_y" if "system_y" in columns else "y"
    z_col = "system_z" if "system_z" in columns else "z"

    if command == "export-top-metallic":
        rows = conn.execute(
            f"""
            SELECT
              r.ring_id,
              r.system_name,
              r.body_name,
              r.ring_name,
              r.{x_col} AS x,
              r.{y_col} AS y,
              r.{z_col} AS z,
              s.moi_final AS score,
              r.arrival_distance_ls,
              r.surface_density,
              r.linear_density
            FROM rings_scored s
            JOIN rings_raw r ON r.ring_id = s.ring_id
            WHERE s.score_version = :score_version
              AND r.ring_type = 'Metallic'
              AND s.moi_final IS NOT NULL
            ORDER BY s.moi_final DESC, (r.arrival_distance_ls IS NULL) ASC, r.arrival_distance_ls ASC
            LIMIT :top
            """,
            {"score_version": score_version, "top": top_n},
        ).fetchall()
    elif command == "export-top-icy-ssd":
        has_ssd = conn.execute(
            """
            SELECT 1
            FROM rings_scored s
            JOIN rings_raw r ON r.ring_id = s.ring_id
            WHERE s.score_version = :score_version
              AND r.ring_type = 'Icy'
              AND s.ssd_score IS NOT NULL
            LIMIT 1
            """,
            {"score_version": score_version},
        ).fetchone()
        if has_ssd is None:
            raise RuntimeError(
                f"SSD scores not found for score_version={score_version}. Run recompute_ssd first."
            )
        rows = conn.execute(
            f"""
            SELECT
              r.ring_id,
              r.system_name,
              r.body_name,
              r.ring_name,
              r.{x_col} AS x,
              r.{y_col} AS y,
              r.{z_col} AS z,
              s.ssd_score AS score,
              r.arrival_distance_ls,
              r.surface_density,
              r.linear_density
            FROM rings_scored s
            JOIN rings_raw r ON r.ring_id = s.ring_id
            WHERE s.score_version = :score_version
              AND r.ring_type = 'Icy'
              AND s.ssd_score IS NOT NULL
            ORDER BY s.ssd_score DESC, (r.arrival_distance_ls IS NULL) ASC, r.arrival_distance_ls ASC
            LIMIT :top
            """,
            {"score_version": score_version, "top": top_n},
        ).fetchall()
    else:
        raise ValueError(f"Unsupported command: {command}")

    return [
        RingRow(
            ring_id=row["ring_id"],
            system_name=row["system_name"],
            body_name=row["body_name"],
            ring_name=row["ring_name"],
            x=row["x"],
            y=row["y"],
            z=row["z"],
            score=row["score"],
            arrival_distance_ls=row["arrival_distance_ls"],
            surface_density=row["surface_density"],
            linear_density=row["linear_density"],
        )
        for row in rows
    ]


def _export_ranked_csv(rows: Iterable[RingRow], out_path: Path) -> None:
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
                "score",
                "arrival_distance_ls",
                "surface_density",
                "linear_density",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.ring_id,
                    row.system_name,
                    row.body_name,
                    row.ring_name,
                    row.x,
                    row.y,
                    row.z,
                    row.score,
                    row.arrival_distance_ls,
                    row.surface_density,
                    row.linear_density,
                ]
            )


def _nearest_neighbor_sequence(
    rows: list[RingRow],
    anchor_x: float,
    anchor_y: float,
    anchor_z: float,
) -> list[tuple[int, RingRow, float, float]]:
    remaining = list(rows)
    sequence: list[tuple[int, RingRow, float, float]] = []

    prev_x: Optional[float] = anchor_x
    prev_y: Optional[float] = anchor_y
    prev_z: Optional[float] = anchor_z
    cumulative = 0.0
    stop_order = 1

    while remaining:
        best_idx = 0
        best_dist = _distance_ly(prev_x, prev_y, prev_z, remaining[0].x, remaining[0].y, remaining[0].z)
        for idx in range(1, len(remaining)):
            dist = _distance_ly(prev_x, prev_y, prev_z, remaining[idx].x, remaining[idx].y, remaining[idx].z)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx

        chosen = remaining.pop(best_idx)
        cumulative += best_dist
        sequence.append((stop_order, chosen, best_dist, cumulative))
        stop_order += 1
        prev_x, prev_y, prev_z = chosen.x, chosen.y, chosen.z

    return sequence


def _export_sequence_csv(
    rows: list[RingRow],
    out_path: Path,
    anchor_x: float,
    anchor_y: float,
    anchor_z: float,
) -> None:
    ordered = _nearest_neighbor_sequence(rows, anchor_x, anchor_y, anchor_z)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "stop_order",
                "ring_id",
                "system_name",
                "body_name",
                "ring_name",
                "x",
                "y",
                "z",
                "score",
                "arrival_distance_ls",
                "surface_density",
                "linear_density",
                "distance_from_prev_ly",
                "cumulative_distance_ly",
            ]
        )
        for stop_order, row, dist, cumulative in ordered:
            writer.writerow(
                [
                    stop_order,
                    row.ring_id,
                    row.system_name,
                    row.body_name,
                    row.ring_name,
                    row.x,
                    row.y,
                    row.z,
                    row.score,
                    row.arrival_distance_ls,
                    row.surface_density,
                    row.linear_density,
                    dist,
                    cumulative,
                ]
            )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Ring Hunter report CSVs from rings_master.sqlite.",
    )
    parser.add_argument("--db", required=True, help="Path to rings_master SQLite DB.")
    parser.add_argument("--out", required=True, help="Output directory for CSV files.")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="Top N rows to export.")
    parser.add_argument(
        "--score-version",
        default=DEFAULT_SCORE_VERSION,
        help="Score version to export from rings_scored.",
    )
    parser.add_argument("--sequence", action="store_true", help="Also emit nearest-neighbor sequence CSV.")
    parser.add_argument("--anchor-x", type=float, default=0.0, help="Sequence anchor X (ly).")
    parser.add_argument("--anchor-y", type=float, default=0.0, help="Sequence anchor Y (ly).")
    parser.add_argument("--anchor-z", type=float, default=0.0, help="Sequence anchor Z (ly).")
    parser.add_argument("--quiet", action="store_true", help="Suppress status output.")
    parser.add_argument(
        "command",
        choices=["export-top-metallic", "export-top-icy-ssd"],
        help="Report command.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.top <= 0:
        print("--top must be >= 1.")
        return 2

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _query_rows(conn, args.command, args.top, args.score_version)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    finally:
        conn.close()

    base_name = "top_metallic" if args.command == "export-top-metallic" else "top_icy_ssd"
    ranked_path = out_dir / f"{base_name}.csv"
    _export_ranked_csv(rows, ranked_path)
    if not args.quiet:
        print(f"Wrote {len(rows)} rows: {ranked_path}")

    if args.sequence:
        seq_path = out_dir / f"{base_name}_sequence.csv"
        _export_sequence_csv(rows, seq_path, args.anchor_x, args.anchor_y, args.anchor_z)
        if not args.quiet:
            print(f"Wrote sequence: {seq_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
