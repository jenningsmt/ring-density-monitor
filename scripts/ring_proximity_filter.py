#!/usr/bin/env python3
"""
ring_proximity_filter.py — Identify best Icy ring fueling candidates near galactic extreme points.
===================================================================================================
For each reference system (galactic cardinal extreme), queries all Icy rings within the search
radius and returns the best candidates ranked by Tritium hotspot strength then surface density.
This is a LOCAL query — no global rank cutoff — so even sparsely explored regions surface
whatever candidates exist in the Spansh dataset.

Global SSD rank is looked up afterward for context, but does not filter results.

Usage:
  python scripts/ring_proximity_filter.py

Output:
  out/ring_proximity_results.csv          — all candidates, grouped by reference system
  out/ring_proximity_summary.csv          — one best candidate per reference system
"""

from __future__ import annotations

import csv
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


# =============================================================================
# Configuration
# =============================================================================

DB_PATH     = Path("data/ring_hunter_library/rings_master_2026-02-13.sqlite")
OUT_FULL    = Path("out/ring_proximity_results.csv")
OUT_SUMMARY = Path("out/ring_proximity_summary.csv")

RADIUS_LY    = 5000.0
TOP_LOCAL_N  = 25          # candidates to return per reference system
SCORE_VERSION = "moi_v1"

# Galactic cardinal extremes — the six furthest reachable points in the Milky Way.
REFERENCE_SYSTEMS: list[dict] = [
    {"name": "HD 6428",             "label": "South",  "x":   -80.8124,   "y": -4849.40625,  "z":   -418.8125},
    {"name": "HIP 58832",           "label": "North",  "x":  -111.6875,   "y":  5319.21875,  "z":  -1115.375},
    {"name": "SPHIESI HX-L d7-0",  "label": "West",   "x": -42213.8125,  "y":   -19.21875,  "z": 35418.71875},
    {"name": "OOD FLEAU ZJ0I D9-0","label": "East",   "x":  40503.8125,  "y":    25.96875,  "z": 17678.0},
    {"name": "LYED YJ-I D9-0",     "label": "Bottom", "x":  11007.46875, "y":    44.84375,  "z": -16899.75},
    {"name": "OEVASY SG-Y D0",     "label": "Top",    "x":  -1502.15625, "y":    -2.625,    "z": 65630.15625},
]


# =============================================================================
# Distance
# =============================================================================

def dist(x1, y1, z1, x2, y2, z2) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)


# =============================================================================
# Per-system local query
# =============================================================================

def scan_all_icy_rings(
    conn: sqlite3.Connection,
    reference_systems: list[dict],
    radius: float,
    top_n: int,
) -> dict[str, list[dict]]:
    """
    Single streaming pass over every Icy ring.  For each row, checks distance
    to all reference systems simultaneously.  Returns {ref_name: [candidates]}
    sorted by Tritium strength then surface density, capped at top_n each.

    One table scan instead of one per reference system, and no COUNT queries.
    """
    per_ref: dict[str, list] = {ref["name"]: [] for ref in reference_systems}

    cursor = conn.execute(
        """
        SELECT
            ring_id,
            system_name,
            body_name,
            ring_name,
            reserve_level,
            surface_density,
            linear_density,
            arrival_distance_ls,
            parent_body_gravity,
            primary_star_class,
            moi_ssd_tritium,
            x, y, z
        FROM rings_raw
        WHERE ring_type = 'Icy'
          AND x IS NOT NULL
          AND y IS NOT NULL
          AND z IS NOT NULL
        """
    )

    rows_scanned = 0
    for row in cursor:
        rx, ry, rz = row["x"], row["y"], row["z"]
        rows_scanned += 1
        if rows_scanned % 5_000_000 == 0:
            print(f"    ... {rows_scanned:,} rows scanned", flush=True)

        for ref in reference_systems:
            d = dist(rx, ry, rz, ref["x"], ref["y"], ref["z"])
            if d <= radius:
                tritium_score = row["moi_ssd_tritium"]
                tritium_confirmed = bool(tritium_score is not None and tritium_score > 0)
                per_ref[ref["name"]].append({
                    "_dist":            d,
                    "_tritium_score":   tritium_score or 0.0,
                    "_surface_density": row["surface_density"] or 0.0,
                    "ring_id":          row["ring_id"],
                    "system_name":      row["system_name"],
                    "body_name":        row["body_name"],
                    "ring_name":        row["ring_name"],
                    "reserve_level":    row["reserve_level"] or "",
                    "tritium_confirmed": tritium_confirmed,
                    "moi_ssd_tritium":  round(tritium_score, 6) if tritium_confirmed else "",
                    "surface_density":  row["surface_density"],
                    "linear_density":   row["linear_density"],
                    "arrival_distance_ls": row["arrival_distance_ls"],
                    "parent_body_gravity": row["parent_body_gravity"],
                    "primary_star_class":  row["primary_star_class"] or "",
                    "x": rx, "y": ry, "z": rz,
                    "distance_ly": round(d, 2),
                })

    print(f"    Scan complete: {rows_scanned:,} Icy rings checked.", flush=True)

    # Sort and cap each reference system's list
    for ref_name in per_ref:
        per_ref[ref_name].sort(key=lambda c: (
            -c["_tritium_score"],
            -c["_surface_density"],
            c["_dist"],
        ))
        per_ref[ref_name] = per_ref[ref_name][:top_n]

    return per_ref


def lookup_ssd_scores(
    conn: sqlite3.Connection,
    ring_ids: list[str],
    score_version: str,
) -> dict[str, dict]:
    """Fetch ssd_score and moi_final for a list of ring_ids in one query."""
    if not ring_ids:
        return {}
    placeholders = ",".join("?" * len(ring_ids))
    rows = conn.execute(
        f"""
        SELECT ring_id, ssd_score, moi_final
        FROM rings_scored
        WHERE ring_id IN ({placeholders})
          AND score_version = ?
        """,
        ring_ids + [score_version],
    ).fetchall()
    return {
        row["ring_id"]: {
            "ssd_score": round(row["ssd_score"], 6) if row["ssd_score"] is not None else None,
            "moi_final": round(row["moi_final"], 6) if row["moi_final"] is not None else None,
        }
        for row in rows
    }


# =============================================================================
# Output writers
# =============================================================================

FULL_FIELDS = [
    "reference_system", "galactic_extreme", "local_rank",
    "global_rank_approx", "system_name", "body_name", "ring_name",
    "reserve_level", "tritium_confirmed", "moi_ssd_tritium",
    "ssd_score", "moi_final", "surface_density", "linear_density",
    "arrival_distance_ls", "parent_body_gravity", "primary_star_class",
    "x", "y", "z", "distance_ly",
]

SUMMARY_FIELDS = [
    "reference_system", "galactic_extreme", "candidates_found",
    "tritium_confirmed_count", "best_system_name", "best_ring_name",
    "best_reserve_level", "best_tritium_confirmed", "best_moi_ssd_tritium",
    "best_ssd_score", "best_global_rank_approx", "best_distance_ly",
    "note",
]


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found: {DB_PATH}")
        return

    print("Ring Proximity Filter — Galactic Extreme Base Camp Candidates")
    print(f"  DB       : {DB_PATH}")
    print(f"  Radius   : {RADIUS_LY:,.0f} ly per extreme point")
    print(f"  Top-N    : {TOP_LOCAL_N} candidates per point")
    print(f"  Mode     : local best (no global rank cutoff)")
    print()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA mmap_size=2147483648")

    all_rows: list[dict] = []
    summary_rows: list[dict] = []

    # Single pass over all Icy rings — all six reference systems checked simultaneously
    print("Scanning all Icy rings (single pass — this will take a few minutes)...")
    print()
    per_ref = scan_all_icy_rings(conn, REFERENCE_SYSTEMS, RADIUS_LY, TOP_LOCAL_N)

    # Collect all matched ring_ids and look up SSD scores in one query
    all_matched_ids = [
        c["ring_id"]
        for candidates in per_ref.values()
        for c in candidates
    ]
    ssd_lookup = lookup_ssd_scores(conn, all_matched_ids, SCORE_VERSION)
    conn.close()

    print()
    for ref in REFERENCE_SYSTEMS:
        candidates = per_ref[ref["name"]]
        print(f"--- {ref['label'].upper()} : {ref['name']} ---")
        print(f"    Coords: ({ref['x']}, {ref['y']}, {ref['z']})")
        print(f"    {len(candidates)} candidate(s) within {RADIUS_LY:,.0f} ly")

        if not candidates:
            summary_rows.append({
                "reference_system":        ref["name"],
                "galactic_extreme":        ref["label"],
                "candidates_found":        0,
                "tritium_confirmed_count": 0,
                "best_system_name":        "",
                "best_ring_name":          "",
                "best_reserve_level":      "",
                "best_tritium_confirmed":  False,
                "best_moi_ssd_tritium":    "",
                "best_ssd_score":          "",
                "best_global_rank_approx": "",
                "best_distance_ly":        "",
                "note": f"No Icy rings in Spansh dataset within {RADIUS_LY:,.0f} ly. Region likely unexplored.",
            })
            print(f"    NOTE: No Icy rings found within radius. Region likely unexplored.")
            print()
            continue

        tritium_count = sum(1 for c in candidates if c["tritium_confirmed"])
        print(f"    Tritium confirmed: {tritium_count}  |  Hotspot unknown: {len(candidates) - tritium_count}")

        print(f"    {'LRnk':<5} {'Trit':^5} {'System':<32} {'Ring':<36} {'Dist':>7}  {'SSD Score':>12}")
        print(f"    {'-'*5} {'-'*5} {'-'*32} {'-'*36} {'-'*7}  {'-'*12}")

        for local_rank, cand in enumerate(candidates, 1):
            gi = ssd_lookup.get(cand["ring_id"], {})
            ssd = gi.get("ssd_score")
            trit_flag = " YES " if cand["tritium_confirmed"] else "     "
            ssd_str = str(ssd) if ssd is not None else "n/a"
            print(
                f"    {local_rank:<5} {trit_flag} {cand['system_name']:<32} "
                f"{cand['ring_name']:<36} {cand['distance_ly']:>7,.1f}  {ssd_str:>12}"
            )
            row = {
                "reference_system":   ref["name"],
                "galactic_extreme":   ref["label"],
                "local_rank":         local_rank,
                "global_rank_approx": "",
                "ssd_score":          gi.get("ssd_score", ""),
                "moi_final":          gi.get("moi_final", ""),
            }
            row.update({k: v for k, v in cand.items() if not k.startswith("_")})
            all_rows.append(row)

        best = candidates[0]
        best_gi = ssd_lookup.get(best["ring_id"], {})
        summary_rows.append({
            "reference_system":        ref["name"],
            "galactic_extreme":        ref["label"],
            "candidates_found":        len(candidates),
            "tritium_confirmed_count": tritium_count,
            "best_system_name":        best["system_name"],
            "best_ring_name":          best["ring_name"],
            "best_reserve_level":      best["reserve_level"],
            "best_tritium_confirmed":  best["tritium_confirmed"],
            "best_moi_ssd_tritium":    best["moi_ssd_tritium"],
            "best_ssd_score":          best_gi.get("ssd_score", ""),
            "best_global_rank_approx": "",
            "best_distance_ly":        best["distance_ly"],
            "note":                    "",
        })
        print()

    conn.close()

    # Write outputs
    OUT_FULL.parent.mkdir(parents=True, exist_ok=True)

    with OUT_FULL.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    with OUT_SUMMARY.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    total = len(all_rows)
    tritium_total = sum(1 for r in all_rows if r.get("tritium_confirmed"))
    print("=" * 70)
    print(f"Complete — {datetime.now(timezone.utc).isoformat()}")
    print(f"  Total candidates : {total}")
    print(f"  Tritium confirmed: {tritium_total}  |  Hotspot unknown: {total - tritium_total}")
    print(f"  Full results     : {OUT_FULL}")
    print(f"  Summary          : {OUT_SUMMARY}")
    print()
    print("Base camp summary (best candidate per extreme point):")
    print(f"  {'Extreme':<8} {'System':<35} {'Trit':^5} {'G.Rank':>8}  {'Dist':>7}  Note")
    print(f"  {'-'*8} {'-'*35} {'-'*5} {'-'*8}  {'-'*7}  {'-'*30}")
    for s in summary_rows:
        if s["candidates_found"] == 0:
            print(f"  {s['galactic_extreme']:<8} {'— no candidates —':<35} {'':^5} {'':>8}  {'':>7}  {s['note'][:50]}")
        else:
            trit = " YES " if s["best_tritium_confirmed"] else "     "
            grank = f"{s['best_global_rank_approx']:,}" if s["best_global_rank_approx"] else "n/a"
            print(
                f"  {s['galactic_extreme']:<8} {s['best_system_name']:<35} {trit:^5} "
                f"{grank:>8}  {s['best_distance_ly']:>7,.1f}"
            )


if __name__ == "__main__":
    main()
