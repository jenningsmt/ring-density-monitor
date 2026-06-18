#!/usr/bin/env python3
"""
Rank Elite Dangerous pristine rings as mining targets using journal logs.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


RING_CLASS_ICY = "eRingClass_Icy"
RING_CLASS_METALLIC = "eRingClass_Metalic"
RING_CLASS_METALLIC_ALT = "eRingClass_Metallic"
PRISTINE = "PristineResources"


@dataclass
class RingRow:
    system_name: str
    parent_body_name: str
    ring_name: str
    ring_class: str
    reserve_level: str
    mass_mt: float
    inner_rad_m: float
    outer_rad_m: float
    surface_area_m2: float
    surface_density: float
    sigma_norm: float
    dss_completed: bool
    dss_required: bool
    tritium_hotspots: int
    platinum_hotspots: int
    rmoi_tritium: float
    rmoi_platinum: float
    rmoi_tritium_pct: str
    rmoi_platinum_pct: str


def iter_journal_files(input_dir: str) -> Iterable[str]:
    for name in os.listdir(input_dir):
        if name.startswith("Journal.") and name.endswith(".log"):
            yield os.path.join(input_dir, name)


def safe_float(value: Optional[float]) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def ring_surface_area(inner_rad_m: float, outer_rad_m: float) -> float:
    if inner_rad_m <= 0 or outer_rad_m <= 0 or outer_rad_m <= inner_rad_m:
        return 0.0
    return math.pi * (outer_rad_m ** 2 - inner_rad_m ** 2)


def ring_density(mass_mt: float, area_m2: float) -> float:
    if mass_mt <= 0 or area_m2 <= 0:
        return 0.0
    return mass_mt / area_m2


def normalize_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def make_body_key(system_address: Optional[int], system_name: Optional[str], body_name: Optional[str]) -> Tuple[str, str, str]:
    if system_address is not None:
        return ("addr", str(system_address), normalize_name(body_name))
    return ("name", normalize_name(system_name), normalize_name(body_name))


def parse_journals(
    input_dir: str,
) -> Tuple[List[dict], Dict[Tuple[str, str, str], bool], Dict[Tuple[str, str, str], Dict[str, int]], List[dict]]:
    ring_rows: List[dict] = []
    dss_completed: Dict[Tuple[str, str, str], bool] = {}
    hotspots: Dict[Tuple[str, str, str], Dict[str, int]] = defaultdict(lambda: {"Tritium": 0, "Platinum": 0})
    ring_keys: set[Tuple[str, str, str]] = set()
    dss_events: List[dict] = []

    for path in iter_journal_files(input_dir):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("event")
                    if event_type == "Scan":
                        system_name = event.get("StarSystem", "")
                        system_address = event.get("SystemAddress")
                        body_name = event.get("BodyName", "")
                        reserve_level = event.get("ReserveLevel", "")
                        key = make_body_key(system_address, system_name, body_name)
                        for ring in event.get("Rings", []) or []:
                            ring_rows.append(
                                {
                                    "system_name": system_name,
                                    "system_address": system_address,
                                    "parent_body_name": body_name,
                                    "ring_name": ring.get("Name", ""),
                                    "ring_class": ring.get("RingClass", ""),
                                    "reserve_level": reserve_level,
                                    "mass_mt": safe_float(ring.get("MassMT")),
                                    "inner_rad_m": safe_float(ring.get("InnerRad")),
                                    "outer_rad_m": safe_float(ring.get("OuterRad")),
                                }
                            )
                            ring_keys.add(key)
                    elif event_type == "SAAScanComplete":
                        body_name = event.get("BodyName")
                        if body_name:
                            system_name = event.get("StarSystem")
                            system_address = event.get("SystemAddress")
                            key = make_body_key(system_address, system_name, body_name)
                            dss_completed[key] = True
                            dss_events.append(
                                {
                                    "event": event_type,
                                    "timestamp": event.get("timestamp", ""),
                                    "system_name": system_name or "",
                                    "system_address": system_address,
                                    "body_name": body_name,
                                    "key": key,
                                }
                            )
                    elif event_type == "SAASignalsFound":
                        body_name = event.get("BodyName")
                        if not body_name:
                            continue
                        system_name = event.get("StarSystem")
                        system_address = event.get("SystemAddress")
                        key = make_body_key(system_address, system_name, body_name)
                        for signal in event.get("Signals", []) or []:
                            if signal.get("Type") == "Tritium":
                                hotspots[key]["Tritium"] += 1
                            elif signal.get("Type") == "Platinum":
                                hotspots[key]["Platinum"] += 1
                        dss_events.append(
                            {
                                "event": event_type,
                                "timestamp": event.get("timestamp", ""),
                                "system_name": system_name or "",
                                "system_address": system_address,
                                "body_name": body_name,
                                "key": key,
                            }
                        )
        except OSError:
            continue

    unmatched_dss_events = [event for event in dss_events if event["key"] not in ring_keys]
    return ring_rows, dss_completed, hotspots, unmatched_dss_events


def filter_rings(rows: List[dict]) -> List[dict]:
    filtered: List[dict] = []
    for row in rows:
        if row.get("reserve_level") != PRISTINE:
            continue
        ring_class = row.get("ring_class")
        if ring_class not in {RING_CLASS_ICY, RING_CLASS_METALLIC, RING_CLASS_METALLIC_ALT}:
            continue
        filtered.append(row)
    return filtered


def compute_sigma_norm(rows: List[dict]) -> List[dict]:
    densities = [row["surface_density"] for row in rows if row["surface_density"] > 0]
    if not densities:
        median_density = 0.0
    else:
        densities.sort()
        mid = len(densities) // 2
        if len(densities) % 2:
            median_density = densities[mid]
        else:
            median_density = (densities[mid - 1] + densities[mid]) / 2
    for row in rows:
        if median_density > 0 and row["surface_density"] > 0:
            row["sigma_norm"] = row["surface_density"] / median_density
        else:
            row["sigma_norm"] = 0.0
    return rows


def score_rows(
    rows: List[dict],
    dss_completed_map: Dict[Tuple[str, str, str], bool],
    hotspot_map: Dict[Tuple[str, str, str], Dict[str, int]],
) -> List[RingRow]:
    scored: List[RingRow] = []
    for row in rows:
        key = make_body_key(row.get("system_address"), row.get("system_name"), row.get("parent_body_name"))
        dss_completed = bool(dss_completed_map.get(key))
        tritium_count = int(hotspot_map.get(key, {}).get("Tritium", 0))
        platinum_count = int(hotspot_map.get(key, {}).get("Platinum", 0))
        dss_required = not dss_completed

        ring_class = row["ring_class"]
        icy_match = ring_class == RING_CLASS_ICY
        metallic_match = ring_class in {RING_CLASS_METALLIC, RING_CLASS_METALLIC_ALT}

        tritium_h = 1.0 + math.log(1 + tritium_count) if dss_completed else 1.0
        platinum_h = 1.0 + math.log(1 + platinum_count) if dss_completed else 1.0

        rmoi_tritium = (1.0 if icy_match else 0.0) * row["sigma_norm"] * tritium_h
        rmoi_platinum = (1.0 if metallic_match else 0.0) * row["sigma_norm"] * platinum_h

        rmoi_tritium_pct = format_rmoi_percent(rmoi_tritium)
        rmoi_platinum_pct = format_rmoi_percent(rmoi_platinum)

        scored.append(
            RingRow(
                system_name=row["system_name"],
                parent_body_name=row["parent_body_name"],
                ring_name=row["ring_name"],
                ring_class=ring_class,
                reserve_level=row["reserve_level"],
                mass_mt=row["mass_mt"],
                inner_rad_m=row["inner_rad_m"],
                outer_rad_m=row["outer_rad_m"],
                surface_area_m2=row["surface_area"],
                surface_density=row["surface_density"],
                sigma_norm=row["sigma_norm"],
                dss_completed=dss_completed,
                dss_required=dss_required,
                tritium_hotspots=tritium_count,
                platinum_hotspots=platinum_count,
                rmoi_tritium=rmoi_tritium,
                rmoi_platinum=rmoi_platinum,
                rmoi_tritium_pct=rmoi_tritium_pct,
                rmoi_platinum_pct=rmoi_platinum_pct,
            )
        )
    return scored


def format_rmoi_percent(rmoi: float) -> str:
    pct = (rmoi - 1.0) * 100.0
    if pct < 0:
        return f"({abs(pct):.2f}%)"
    return f"{pct:.2f}%"


def write_csv(path: str, rows: List[RingRow]) -> None:
    fieldnames = [
        "system_name",
        "parent_body_name",
        "ring_name",
        "ring_class",
        "reserve_level",
        "mass_mt",
        "inner_rad_m",
        "outer_rad_m",
        "surface_area_m2",
        "surface_density",
        "sigma_norm",
        "dss_completed",
        "dss_required",
        "tritium_hotspots",
        "platinum_hotspots",
        "rmoi_tritium",
        "rmoi_platinum",
        "rmoi_tritium_pct",
        "rmoi_platinum_pct",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def write_unmatched_dss(path: str, events: List[dict]) -> None:
    fieldnames = [
        "event",
        "timestamp",
        "system_name",
        "system_address",
        "body_name",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            writer.writerow({name: event.get(name, "") for name in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank pristine rings from Elite Dangerous journals.")
    parser.add_argument("--input_dir", required=True, help="Directory containing Journal.*.log files")
    parser.add_argument("--output_dir", required=True, help="Directory to write CSV outputs")
    parser.add_argument(
        "--dss_sigma_min",
        type=float,
        default=None,
        help="Optional sigma_norm minimum for DSS tasking list",
    )
    args = parser.parse_args()

    ring_rows, dss_completed_map, hotspot_map, unmatched_dss_events = parse_journals(args.input_dir)
    filtered = filter_rings(ring_rows)

    for row in filtered:
        area = ring_surface_area(row["inner_rad_m"], row["outer_rad_m"])
        density = ring_density(row["mass_mt"], area)
        row["surface_area"] = area
        row["surface_density"] = density

    compute_sigma_norm(filtered)
    scored = score_rows(filtered, dss_completed_map, hotspot_map)

    tritium_ranked = sorted(
        [r for r in scored if r.ring_class == RING_CLASS_ICY],
        key=lambda r: r.rmoi_tritium,
        reverse=True,
    )[:200]
    platinum_ranked = sorted(
        [r for r in scored if r.ring_class in {RING_CLASS_METALLIC, RING_CLASS_METALLIC_ALT}],
        key=lambda r: r.rmoi_platinum,
        reverse=True,
    )[:200]
    dss_candidates = [r for r in scored if not r.dss_completed]
    if args.dss_sigma_min is not None:
        dss_candidates = [r for r in dss_candidates if r.sigma_norm >= args.dss_sigma_min]
    for ring in dss_candidates:
        ring.dss_required = True
    dss_tasking_icy = sorted(
        [r for r in dss_candidates if r.ring_class == RING_CLASS_ICY],
        key=lambda r: (r.rmoi_tritium, r.mass_mt),
        reverse=True,
    )
    dss_tasking_metallic = sorted(
        [r for r in dss_candidates if r.ring_class in {RING_CLASS_METALLIC, RING_CLASS_METALLIC_ALT}],
        key=lambda r: (r.rmoi_platinum, r.mass_mt),
        reverse=True,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    write_csv(os.path.join(args.output_dir, "rings_ranked_tritium.csv"), tritium_ranked)
    write_csv(os.path.join(args.output_dir, "rings_ranked_platinum.csv"), platinum_ranked)
    write_csv(os.path.join(args.output_dir, "dss_tasking_icy.csv"), dss_tasking_icy)
    write_csv(os.path.join(args.output_dir, "dss_tasking_metallic.csv"), dss_tasking_metallic)
    write_unmatched_dss(os.path.join(args.output_dir, "dss_events_unmatched.csv"), unmatched_dss_events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
