#!/usr/bin/env python3
"""
Build top ring rankings for a sector, merging sector DB data with journal discoveries.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from contextlib import closing


CATEGORY_ICY = "ICY"
CATEGORY_METALLIC = "METALLIC"
CATEGORY_METAL_RICH = "METAL_RICH"
CATEGORY_ROCKY = "ROCKY"
CATEGORIES = [CATEGORY_ROCKY, CATEGORY_METAL_RICH, CATEGORY_METALLIC, CATEGORY_ICY]

DEFAULT_SINCE = "2026-01-01"
DEFAULT_LIMIT = 10
ANCHOR_SYSTEM_NAME = "Eotchorts FG-X d1-318"


@dataclass
class ColumnInfo:
    name: str
    norm: str


@dataclass
class TableInfo:
    name: str
    columns: List[ColumnInfo]

    def column_names(self) -> List[str]:
        return [col.name for col in self.columns]

    def column_norms(self) -> List[str]:
        return [col.norm for col in self.columns]


@dataclass
class RingEntry:
    category: str
    system_name: str
    body_name: str
    ring_name: str
    surface_density: float
    distance_to_anchor_ly: float
    mapped_journal: bool
    mapped_db: bool
    mapped_final: bool
    source: str


def sanitize_sector(value: str) -> str:
    trimmed = value.strip()
    replaced = trimmed.replace(" ", "_")
    safe = "".join(ch for ch in replaced if ch.isalnum() or ch in {"_", "-"})
    return safe or "sector"


def normalize_identifier(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def normalize_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def quote_ident(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_since(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.fromisoformat(text + "T00:00:00")
        except ValueError:
            raise ValueError(f"Invalid date format: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def ring_surface_area(inner_rad_m: float, outer_rad_m: float) -> float:
    if inner_rad_m <= 0 or outer_rad_m <= 0 or outer_rad_m <= inner_rad_m:
        return 0.0
    return math.pi * (outer_rad_m ** 2 - inner_rad_m ** 2)


def ring_density(mass_mt: float, area_m2: float) -> float:
    if mass_mt <= 0 or area_m2 <= 0:
        return 0.0
    return mass_mt / area_m2


def classify_ring(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lowered = value.strip().lower()
    normalized = lowered.replace("_", " ").replace("-", " ")
    normalized = " ".join(normalized.split())
    if "icy" in normalized:
        return CATEGORY_ICY
    if "metal rich" in normalized or "metalrich" in normalized:
        return CATEGORY_METAL_RICH
    if "metallic" in normalized or "metalic" in normalized:
        return CATEGORY_METALLIC
    if "rocky" in normalized:
        return CATEGORY_ROCKY
    return None


def table_infos(conn: sqlite3.Connection) -> List[TableInfo]:
    tables = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (name,) in rows:
        columns = []
        for col in conn.execute(f"PRAGMA table_info({quote_ident(name)})").fetchall():
            col_name = col[1]
            columns.append(ColumnInfo(name=col_name, norm=normalize_identifier(col_name)))
        tables.append(TableInfo(name=name, columns=columns))
    return tables


def column_by_patterns(columns: Sequence[ColumnInfo], patterns: Sequence[Sequence[str]]) -> Optional[str]:
    for pattern in patterns:
        for col in columns:
            if all(token in col.norm for token in pattern):
                return col.name
    return None


def table_has_column(table: TableInfo, name: str) -> bool:
    return any(col.name == name for col in table.columns)


def find_system_name_column(table: TableInfo) -> Optional[str]:
    col = column_by_patterns(
        table.columns,
        [
            ["systemname"],
            ["system", "name"],
        ],
    )
    if col:
        return col
    if "system" in table.name.lower():
        if table_has_column(table, "name"):
            return "name"
    return None


def find_body_name_column(table: TableInfo) -> Optional[str]:
    col = column_by_patterns(
        table.columns,
        [
            ["bodyname"],
            ["body", "name"],
        ],
    )
    if col:
        return col
    if "body" in table.name.lower():
        if table_has_column(table, "name"):
            return "name"
    return None


def find_ring_name_column(table: TableInfo) -> Optional[str]:
    col = column_by_patterns(
        table.columns,
        [
            ["ringname"],
            ["ring", "name"],
        ],
    )
    if col:
        return col
    if "ring" in table.name.lower():
        if table_has_column(table, "name"):
            return "name"
    return None


def find_ring_class_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["ringclass"],
            ["ring", "class"],
            ["ringtype"],
            ["ring", "type"],
            ["class", "ring"],
            ["type", "ring"],
        ],
    )


def find_density_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["surfacedensity"],
            ["surface", "density"],
            ["density"],
        ],
    )


def find_mass_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["massmt"],
            ["mass", "mt"],
            ["mass"],
        ],
    )


def find_inner_radius_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["innerrad"],
            ["inner", "rad"],
            ["innerradius"],
            ["inner", "radius"],
        ],
    )


def find_outer_radius_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["outerrad"],
            ["outer", "rad"],
            ["outerradius"],
            ["outer", "radius"],
        ],
    )


def find_raw_json_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["rawjson"],
            ["raw", "json"],
            ["json"],
        ],
    )


def find_system_key_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["systemkey"],
            ["system", "key"],
            ["systemid"],
            ["system", "id"],
        ],
    )


def find_coord_column(table: TableInfo, axis: str) -> Optional[str]:
    axis_norm = axis.lower()
    for col in table.columns:
        if col.norm == axis_norm:
            return col.name
    return column_by_patterns(
        table.columns,
        [
            ["coord", axis_norm],
            [axis_norm, "coord"],
            ["pos", axis_norm],
            [axis_norm, "pos"],
        ],
    )


def find_body_key_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["bodykey"],
            ["body", "key"],
            ["bodyid"],
            ["body", "id"],
        ],
    )


def find_mapping_column(table: TableInfo) -> Optional[str]:
    return column_by_patterns(
        table.columns,
        [
            ["ismapped"],
            ["wasmapped"],
            ["mapped"],
            ["dss"],
            ["saas"],
            ["mappingstate"],
            ["mapping", "state"],
        ],
    )


def score_ring_table(table: TableInfo) -> int:
    score = 0
    name = table.name.lower()
    if "ring" in name:
        score += 3
    if find_ring_name_column(table):
        score += 6
    if find_ring_class_column(table):
        score += 5
    if find_mass_column(table):
        score += 2
    if find_inner_radius_column(table):
        score += 2
    if find_outer_radius_column(table):
        score += 2
    if find_system_name_column(table):
        score += 2
    if find_body_name_column(table):
        score += 2
    if find_raw_json_column(table):
        score += 1
    return score


def score_system_table(table: TableInfo) -> int:
    score = 0
    name = table.name.lower()
    if "system" in name:
        score += 3
    if find_system_name_column(table):
        score += 6
    if find_system_key_column(table):
        score += 2
    return score


def score_body_table(table: TableInfo) -> int:
    score = 0
    name = table.name.lower()
    if "bod" in name:
        score += 3
    if find_body_name_column(table):
        score += 6
    if find_body_key_column(table):
        score += 2
    return score


def pick_best_table(tables: List[TableInfo], scorer) -> Optional[TableInfo]:
    best = None
    best_score = 0
    for table in tables:
        score = scorer(table)
        if score > best_score:
            best_score = score
            best = table
    return best


def safe_float(value: Optional[object]) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ring_json(raw_json: Optional[str]) -> Dict[str, Optional[object]]:
    if not raw_json:
        return {}
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def ring_field_from_json(data: Dict[str, object], keys: Sequence[str]) -> Optional[object]:
    for key in keys:
        if key in data:
            value = data.get(key)
            if value is not None:
                return value
    return None


def mapping_value_to_bool(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "y", "yes", "true", "mapped", "complete", "completed"}:
            return True
        if lowered in {"0", "n", "no", "false", "unmapped", "none"}:
            return False
    return False


def build_mapped_db_set(
    conn: sqlite3.Connection,
    body_table: Optional[TableInfo],
    ring_table: Optional[TableInfo],
) -> Tuple[set[Tuple[str, str]], Optional[str]]:
    tables_to_check = []
    if body_table:
        tables_to_check.append(body_table)
    if ring_table:
        tables_to_check.append(ring_table)

    for table in tables_to_check:
        mapping_col = find_mapping_column(table)
        system_name_col = find_system_name_column(table)
        body_name_col = find_body_name_column(table)
        if not mapping_col or not system_name_col or not body_name_col:
            continue
        query = (
            f"SELECT {quote_ident(system_name_col)}, {quote_ident(body_name_col)}, "
            f"{quote_ident(mapping_col)} FROM {quote_ident(table.name)}"
        )
        mapped_set: set[Tuple[str, str]] = set()
        for system_name, body_name, mapped_val in conn.execute(query).fetchall():
            if not isinstance(system_name, str) or not isinstance(body_name, str):
                continue
            if mapping_value_to_bool(mapped_val):
                mapped_set.add((normalize_name(system_name), normalize_name(body_name)))
        return mapped_set, mapping_col
    return set(), None


def db_mapped(
    system_name: str,
    body_name: str,
    mapped_db_set: set[Tuple[str, str]],
) -> bool:
    return (normalize_name(system_name), normalize_name(body_name)) in mapped_db_set


def ring_sort_key(entry: RingEntry) -> Tuple[float, float, str, str, str]:
    return (
        -entry.surface_density,
        entry.distance_to_anchor_ly,
        entry.system_name.lower(),
        entry.body_name.lower(),
        entry.ring_name.lower(),
    )


def build_system_maps(
    conn: sqlite3.Connection,
    system_table: Optional[TableInfo],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    if not system_table:
        return {}, {}
    system_name_col = find_system_name_column(system_table)
    system_key_col = find_system_key_column(system_table)
    if not system_name_col:
        return {}, {}
    if not system_key_col:
        return {}, {}
    system_key_to_name: Dict[str, str] = {}
    system_name_to_key: Dict[str, str] = {}
    query = (
        f"SELECT {quote_ident(system_key_col)}, {quote_ident(system_name_col)} "
        f"FROM {quote_ident(system_table.name)}"
    )
    for row in conn.execute(query).fetchall():
        system_key = row[0]
        name = row[1]
        if system_key is not None and isinstance(name, str):
            key_str = str(system_key)
            system_key_to_name[key_str] = name
            system_name_to_key[name] = key_str
    return system_key_to_name, system_name_to_key


def build_system_coords_map(
    conn: sqlite3.Connection,
    system_table: Optional[TableInfo],
    anchor_system_name: str = ANCHOR_SYSTEM_NAME,
) -> Tuple[Dict[str, Tuple[float, float, float]], Optional[Tuple[float, float, float]]]:
    if not system_table:
        return {}, None
    system_name_col = find_system_name_column(system_table)
    x_col = find_coord_column(system_table, "x")
    y_col = find_coord_column(system_table, "y")
    z_col = find_coord_column(system_table, "z")
    if not system_name_col or not x_col or not y_col or not z_col:
        return {}, None
    query = (
        f"SELECT {quote_ident(system_name_col)}, {quote_ident(x_col)}, "
        f"{quote_ident(y_col)}, {quote_ident(z_col)} FROM {quote_ident(system_table.name)}"
    )
    coords: Dict[str, Tuple[float, float, float]] = {}
    anchor_coords: Optional[Tuple[float, float, float]] = None
    anchor_norm = normalize_name(anchor_system_name)
    for system_name, x_val, y_val, z_val in conn.execute(query).fetchall():
        if not isinstance(system_name, str):
            continue
        x = safe_float(x_val)
        y = safe_float(y_val)
        z = safe_float(z_val)
        if x is None or y is None or z is None:
            continue
        norm = normalize_name(system_name)
        coords[norm] = (x, y, z)
        if norm == anchor_norm:
            anchor_coords = (x, y, z)
    return coords, anchor_coords


def distance_between(
    a: Tuple[float, float, float], b: Tuple[float, float, float]
) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def build_body_map(
    conn: sqlite3.Connection,
    body_table: Optional[TableInfo],
) -> Dict[str, str]:
    if not body_table:
        return {}
    body_name_col = find_body_name_column(body_table)
    body_key_col = find_body_key_column(body_table)
    if not body_name_col or not body_key_col:
        return {}
    body_key_to_name: Dict[str, str] = {}
    query = (
        f"SELECT {quote_ident(body_key_col)}, {quote_ident(body_name_col)} "
        f"FROM {quote_ident(body_table.name)}"
    )
    for row in conn.execute(query).fetchall():
        body_key = row[0]
        name = row[1]
        if body_key is not None and isinstance(name, str):
            body_key_to_name[str(body_key)] = name
    return body_key_to_name


def build_system_name_set(
    conn: sqlite3.Connection,
    system_table: Optional[TableInfo],
    ring_table: TableInfo,
) -> Tuple[Dict[str, str], List[str]]:
    system_names: Dict[str, str] = {}
    names: List[str] = []
    if system_table:
        system_name_col = find_system_name_column(system_table)
        if system_name_col:
            query = (
                f"SELECT {quote_ident(system_name_col)} "
                f"FROM {quote_ident(system_table.name)}"
            )
            for (name,) in conn.execute(query).fetchall():
                if not isinstance(name, str):
                    continue
                norm = normalize_name(name)
                if norm not in system_names:
                    system_names[norm] = name
                    names.append(name)
            return system_names, names
    ring_system_col = find_system_name_column(ring_table)
    if ring_system_col:
        query = (
            f"SELECT DISTINCT {quote_ident(ring_system_col)} "
            f"FROM {quote_ident(ring_table.name)}"
        )
        for (name,) in conn.execute(query).fetchall():
            if not isinstance(name, str):
                continue
            norm = normalize_name(name)
            if norm not in system_names:
                system_names[norm] = name
                names.append(name)
    return system_names, names


def journal_file_date(name: str) -> Optional[str]:
    """Extract date prefix from journal filename for pre-filtering.

    Journal filenames follow 'Journal.YYYY-MM-DDThh...log' or 'Journal.YYMMDDhh...log'.
    Returns the date portion or None if unparseable.
    """
    stem = name.removeprefix("Journal.").removesuffix(".log")
    if not stem:
        return None
    # Standard format: 2026-01-02T123456.01
    if len(stem) >= 10 and stem[4] == "-":
        return stem[:10]
    # Legacy format: 220207184508.01 (YYMMDD...)
    if len(stem) >= 6 and stem[:6].isdigit():
        yy, mm, dd = stem[:2], stem[2:4], stem[4:6]
        return f"20{yy}-{mm}-{dd}"
    return None


def iter_journal_files(input_dir: Path, since: Optional[datetime] = None) -> Iterable[Path]:
    if not input_dir.exists():
        return []
    # Pre-compute since date string for filename filtering
    since_date_str = None
    if since is not None:
        since_date_str = since.strftime("%Y-%m-%d")
    for entry in sorted(input_dir.iterdir()):
        if entry.is_file() and entry.name.startswith("Journal.") and entry.name.endswith(".log"):
            if since_date_str:
                file_date = journal_file_date(entry.name)
                if file_date and file_date < since_date_str:
                    continue
            yield entry


def parse_journals(
    input_dir: Path,
    since: datetime,
    sector_systems: Dict[str, str],
    counters: Dict[str, int],
) -> Tuple[Dict[Tuple[str, str, str], RingEntry], Dict[Tuple[str, str], bool]]:
    ring_entries: Dict[Tuple[str, str, str], RingEntry] = {}
    mapped_bodies: Dict[Tuple[str, str], bool] = {}

    for path in iter_journal_files(input_dir, since):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    counters["journal_lines"] = counters.get("journal_lines", 0) + 1
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    timestamp = parse_timestamp(event.get("timestamp"))
                    if not timestamp:
                        continue
                    if timestamp < since:
                        continue
                    event_type = event.get("event")
                    if event_type == "SAAScanComplete":
                        system_name = event.get("StarSystem") or event.get("SystemName") or ""
                        body_name = event.get("BodyName") or ""
                        if not system_name or not body_name:
                            continue
                        system_norm = normalize_name(system_name)
                        if system_norm not in sector_systems:
                            continue
                        mapped_bodies[(system_norm, normalize_name(body_name))] = True
                    elif event_type == "Scan":
                        counters["journal_scan_events"] = (
                            counters.get("journal_scan_events", 0) + 1
                        )
                        system_name = event.get("StarSystem") or event.get("SystemName") or ""
                        body_name = event.get("BodyName") or ""
                        if not system_name or not body_name:
                            continue
                        system_norm = normalize_name(system_name)
                        if system_norm not in sector_systems:
                            continue
                        rings = event.get("Rings") or []
                        if not isinstance(rings, list):
                            continue
                        for ring in rings:
                            if not isinstance(ring, dict):
                                continue
                            ring_name = ring.get("Name") or ""
                            if not ring_name:
                                continue
                            ring_class = ring.get("RingClass") or ""
                            category = classify_ring(ring_class)
                            if not category:
                                counters["journal_unknown_category"] = (
                                    counters.get("journal_unknown_category", 0) + 1
                                )
                                continue
                            mass_mt = safe_float(ring.get("MassMT"))
                            inner_rad = safe_float(ring.get("InnerRad"))
                            outer_rad = safe_float(ring.get("OuterRad"))
                            if mass_mt is None or inner_rad is None or outer_rad is None:
                                counters["journal_missing_mass_radii"] = (
                                    counters.get("journal_missing_mass_radii", 0) + 1
                                )
                                continue
                            area = ring_surface_area(inner_rad, outer_rad)
                            if area <= 0:
                                counters["journal_invalid_radii"] = (
                                    counters.get("journal_invalid_radii", 0) + 1
                                )
                                continue
                            density = ring_density(mass_mt, area)
                            if density <= 0:
                                counters["journal_invalid_density"] = (
                                    counters.get("journal_invalid_density", 0) + 1
                                )
                                continue
                            counters["journal_rings_matched"] = (
                                counters.get("journal_rings_matched", 0) + 1
                            )
                            key = (
                                normalize_name(system_name),
                                normalize_name(body_name),
                                normalize_name(ring_name),
                            )
                            mapped = mapped_bodies.get(
                                (normalize_name(system_name), normalize_name(body_name)), False
                            )
                            entry = RingEntry(
                                category=category,
                                system_name=system_name,
                                body_name=body_name,
                                ring_name=ring_name,
                                surface_density=density,
                                distance_to_anchor_ly=0.0,
                                mapped_journal=mapped,
                                mapped_db=False,
                                mapped_final=mapped,
                                source="journal",
                            )
                            existing = ring_entries.get(key)
                            if existing is None or entry.surface_density > existing.surface_density:
                                if existing is not None and existing.mapped_journal:
                                    entry.mapped_journal = True
                                    entry.mapped_final = True
                                ring_entries[key] = entry
                            elif entry.mapped_journal and existing is not None:
                                existing.mapped_journal = True
                                existing.mapped_final = True
        except OSError:
            continue
    return ring_entries, mapped_bodies


def update_top_list(
    top_list: List[RingEntry],
    key_map: Dict[Tuple[str, str, str], RingEntry],
    entry: RingEntry,
    limit: int,
) -> str:
    key = (
        normalize_name(entry.system_name),
        normalize_name(entry.body_name),
        normalize_name(entry.ring_name),
    )
    existing = key_map.get(key)
    if existing:
        if entry.surface_density > existing.surface_density:
            existing.surface_density = entry.surface_density
            existing.source = entry.source
        if entry.mapped_journal:
            existing.mapped_journal = True
        if entry.mapped_db:
            existing.mapped_db = True
        existing.mapped_final = existing.mapped_journal or existing.mapped_db
        top_list.sort(key=ring_sort_key)
        return "update_existing"

    if len(top_list) < limit:
        top_list.append(entry)
        key_map[key] = entry
        top_list.sort(key=ring_sort_key)
        return "insert"

    worst = max(top_list, key=ring_sort_key)
    if ring_sort_key(entry) < ring_sort_key(worst):
        lowest_key = (
            normalize_name(worst.system_name),
            normalize_name(worst.body_name),
            normalize_name(worst.ring_name),
        )
        key_map.pop(lowest_key, None)
        top_list.remove(worst)
        top_list.append(entry)
        key_map[key] = entry
        top_list.sort(key=ring_sort_key)
        return "replace"
    return "skip"


def print_table(category: str, rows: List[RingEntry]) -> None:
    headers = ["Rank", "System", "Body", "Ring", "Density", "Mapped"]
    cols = [len(h) for h in headers]
    rendered = []
    for idx, row in enumerate(rows, start=1):
        density_str = f"{row.surface_density:.6e}"
        mapped_str = "YES" if row.mapped_final else "NO"
        values = [
            str(idx),
            row.system_name,
            row.body_name,
            row.ring_name,
            density_str,
            mapped_str,
        ]
        rendered.append(values)
        cols = [max(cols[i], len(values[i])) for i in range(len(cols))]
    print(f"\n{category} Rings (Top {len(rows)}):")
    print("  " + "  ".join(headers[i].ljust(cols[i]) for i in range(len(headers))))
    print("  " + "  ".join("-" * cols[i] for i in range(len(headers))))
    for values in rendered:
        print("  " + "  ".join(values[i].ljust(cols[i]) for i in range(len(headers))))


def copy_schema(conn_src: sqlite3.Connection, conn_dst: sqlite3.Connection) -> None:
    rows = conn_src.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type"
    ).fetchall()
    for row in rows:
        obj_type, _, sql = row
        if not sql:
            continue
        if obj_type == "table":
            conn_dst.execute(sql)
    conn_dst.commit()


def copy_indices_and_views(conn_src: sqlite3.Connection, conn_dst: sqlite3.Connection) -> None:
    rows = conn_src.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type"
    ).fetchall()
    for obj_type, _, sql in rows:
        if not sql:
            continue
        if obj_type in {"index", "view", "trigger"}:
            conn_dst.execute(sql)
    conn_dst.commit()


def copy_table_rows(
    conn_src: sqlite3.Connection,
    conn_dst: sqlite3.Connection,
    table: TableInfo,
    where_clause: str,
    params: Sequence[object],
) -> None:
    columns = table.column_names()
    col_sql = ", ".join(quote_ident(col) for col in columns)
    placeholders = ", ".join(["?"] * len(columns))
    select_sql = f"SELECT {col_sql} FROM {quote_ident(table.name)} {where_clause}"
    insert_sql = (
        f"INSERT INTO {quote_ident(table.name)} ({col_sql}) VALUES ({placeholders})"
    )
    cursor = conn_src.execute(select_sql, params)
    batch = cursor.fetchmany(500)
    while batch:
        conn_dst.executemany(insert_sql, batch)
        batch = cursor.fetchmany(500)


def should_copy_entire_table(conn_src: sqlite3.Connection, table: TableInfo) -> bool:
    try:
        count = conn_src.execute(
            f"SELECT COUNT(*) FROM {quote_ident(table.name)}"
        ).fetchone()[0]
    except sqlite3.Error:
        return False
    return count <= 1000


def build_top_lists_from_sector(
    conn: sqlite3.Connection,
    ring_table: TableInfo,
    system_key_to_name: Dict[str, str],
    body_key_to_name: Dict[str, str],
    system_coords: Dict[str, Tuple[float, float, float]],
    anchor_coords: Tuple[float, float, float],
    limit: int,
    quiet: bool,
    counters: Dict[str, int],
) -> Tuple[Dict[str, List[RingEntry]], Dict[str, Dict[Tuple[str, str, str], RingEntry]]]:
    ring_name_col = find_ring_name_column(ring_table)
    ring_class_col = find_ring_class_column(ring_table)
    system_name_col = find_system_name_column(ring_table)
    body_name_col = find_body_name_column(ring_table)
    density_col = find_density_column(ring_table)
    mass_col = find_mass_column(ring_table)
    inner_col = find_inner_radius_column(ring_table)
    outer_col = find_outer_radius_column(ring_table)
    raw_json_col = find_raw_json_column(ring_table)
    system_key_col = find_system_key_column(ring_table)
    body_key_col = find_body_key_column(ring_table)

    if not ring_name_col and not raw_json_col:
        raise RuntimeError("Could not locate ring name column or raw_json in ring table.")
    if not ring_class_col and not raw_json_col:
        raise RuntimeError("Could not locate ring class column or raw_json in ring table.")
    if not system_name_col and not system_key_col:
        raise RuntimeError("Could not locate system name or system key column in ring table.")
    if not body_name_col and not body_key_col:
        raise RuntimeError("Could not locate body name or body key column in ring table.")

    selected_cols = [
        col
        for col in [
            ring_name_col,
            ring_class_col,
            system_name_col,
            body_name_col,
            density_col,
            mass_col,
            inner_col,
            outer_col,
            raw_json_col,
            system_key_col,
            body_key_col,
        ]
        if col
    ]
    col_sql = ", ".join(quote_ident(col) for col in selected_cols)
    query = f"SELECT {col_sql} FROM {quote_ident(ring_table.name)}"
    cursor = conn.execute(query)

    top_lists: Dict[str, List[RingEntry]] = {cat: [] for cat in CATEGORIES}
    key_maps: Dict[str, Dict[Tuple[str, str, str], RingEntry]] = {
        cat: {} for cat in CATEGORIES
    }
    skipped = 0
    for row in cursor:
        counters["db_rings_evaluated"] = counters.get("db_rings_evaluated", 0) + 1
        row_map = {col: row[idx] for idx, col in enumerate(selected_cols)}
        raw_json = parse_ring_json(row_map.get(raw_json_col))
        ring_name = row_map.get(ring_name_col) if ring_name_col else None
        if not ring_name:
            ring_name = ring_field_from_json(raw_json, ["name", "Name", "ringName", "RingName"])
        if not ring_name:
            counters["db_missing_ring_name"] = counters.get("db_missing_ring_name", 0) + 1
        ring_class = row_map.get(ring_class_col) if ring_class_col else None
        if not ring_class:
            ring_class = ring_field_from_json(raw_json, ["ringClass", "RingClass", "class", "type"])
        category = classify_ring(str(ring_class) if ring_class is not None else None)
        if not category:
            skipped += 1
            counters["db_unknown_category"] = counters.get("db_unknown_category", 0) + 1
            continue

        system_name = row_map.get(system_name_col) if system_name_col else None
        if not system_name and system_key_col:
            system_key = row_map.get(system_key_col)
            system_name = system_key_to_name.get(str(system_key)) if system_key is not None else None
        body_name = row_map.get(body_name_col) if body_name_col else None
        if not body_name and body_key_col:
            body_key = row_map.get(body_key_col)
            body_name = body_key_to_name.get(str(body_key)) if body_key is not None else None
        if not isinstance(system_name, str) or not isinstance(body_name, str):
            skipped += 1
            counters["db_missing_system_body"] = counters.get("db_missing_system_body", 0) + 1
            continue
        system_norm = normalize_name(system_name)
        coords = system_coords.get(system_norm)
        if coords is None:
            skipped += 1
            counters["db_missing_coords"] = counters.get("db_missing_coords", 0) + 1
            if not quiet:
                print(f"Warning: missing coords for system {system_name}; skipping ring {ring_name}")
            continue
        distance_ly = distance_between(coords, anchor_coords)

        density_val = safe_float(row_map.get(density_col)) if density_col else None
        if density_val is None or density_val <= 0:
            mass_val = safe_float(row_map.get(mass_col)) if mass_col else None
            inner_val = safe_float(row_map.get(inner_col)) if inner_col else None
            outer_val = safe_float(row_map.get(outer_col)) if outer_col else None
            if (mass_val is None or inner_val is None or outer_val is None) and raw_json:
                if mass_val is None:
                    mass_val = safe_float(
                        ring_field_from_json(raw_json, ["mass", "MassMT", "massMT"])
                    )
                if inner_val is None:
                    inner_val = safe_float(
                        ring_field_from_json(raw_json, ["innerRadius", "InnerRad", "innerRad"])
                    )
                if outer_val is None:
                    outer_val = safe_float(
                        ring_field_from_json(raw_json, ["outerRadius", "OuterRad", "outerRad"])
                    )
            if mass_val is None or inner_val is None or outer_val is None:
                skipped += 1
                counters["db_missing_mass_radii"] = counters.get("db_missing_mass_radii", 0) + 1
                continue
            area = ring_surface_area(inner_val, outer_val)
            if area <= 0:
                skipped += 1
                counters["db_invalid_radii"] = counters.get("db_invalid_radii", 0) + 1
                continue
            density_val = ring_density(mass_val, area)

        if not ring_name:
            skipped += 1
            continue
        if density_val <= 0:
            skipped += 1
            counters["db_invalid_density"] = counters.get("db_invalid_density", 0) + 1
            continue

        entry = RingEntry(
            category=category,
            system_name=system_name,
            body_name=body_name,
            ring_name=str(ring_name),
            surface_density=density_val,
            distance_to_anchor_ly=distance_ly,
            mapped_journal=False,
            mapped_db=False,
            mapped_final=False,
            source="sector_db",
        )
        update_top_list(top_lists[category], key_maps[category], entry, limit)

    if skipped and not quiet:
        print(f"Skipped {skipped} ring rows due to missing data.")
    return top_lists, key_maps


def write_top_rings_table(
    conn: sqlite3.Connection,
    sector: str,
    top_lists: Dict[str, List[RingEntry]],
) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS top_rings_ranked (
            sector TEXT,
            category TEXT,
            rank INTEGER,
            system_name TEXT,
            body_name TEXT,
            ring_name TEXT,
            surface_density REAL,
            distance_to_anchor_ly REAL,
            mapped_journal INTEGER,
            mapped_db INTEGER,
            mapped_final INTEGER,
            source TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS top_ring_flags (
            system_name TEXT,
            body_name TEXT,
            ring_name TEXT,
            category TEXT,
            is_top10 INTEGER,
            rank INTEGER
        )
        """
    )
    conn.execute("DELETE FROM top_rings_ranked")
    conn.execute("DELETE FROM top_ring_flags")

    updated_at = utc_now_iso()
    for category, rows in top_lists.items():
        for idx, ring in enumerate(rows, start=1):
            conn.execute(
                """
                INSERT INTO top_rings_ranked (
                    sector, category, rank, system_name, body_name, ring_name,
                    surface_density, distance_to_anchor_ly, mapped_journal, mapped_db,
                    mapped_final, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sector,
                    category,
                    idx,
                    ring.system_name,
                    ring.body_name,
                    ring.ring_name,
                    ring.surface_density,
                    ring.distance_to_anchor_ly,
                    1 if ring.mapped_journal else 0,
                    1 if ring.mapped_db else 0,
                    1 if ring.mapped_final else 0,
                    ring.source,
                    updated_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO top_ring_flags (
                    system_name, body_name, ring_name, category, is_top10, rank
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ring.system_name,
                    ring.body_name,
                    ring.ring_name,
                    category,
                    1,
                    idx,
                ),
            )
    conn.commit()


def create_top_ring_view(conn: sqlite3.Connection, ring_table: Optional[TableInfo]) -> None:
    if not ring_table:
        return
    ring_name_col = find_ring_name_column(ring_table)
    system_name_col = find_system_name_column(ring_table)
    body_name_col = find_body_name_column(ring_table)
    if not ring_name_col or not system_name_col or not body_name_col:
        return
    conn.execute("DROP VIEW IF EXISTS rings_with_top_flags")
    conn.execute(
        f"""
        CREATE VIEW rings_with_top_flags AS
        SELECT r.*, f.category AS top_category, f.rank AS top_rank
        FROM {quote_ident(ring_table.name)} AS r
        LEFT JOIN top_ring_flags AS f
            ON r.{quote_ident(system_name_col)} = f.system_name
            AND r.{quote_ident(body_name_col)} = f.body_name
            AND r.{quote_ident(ring_name_col)} = f.ring_name
        """
    )
    conn.commit()


def write_run_metadata(
    conn: sqlite3.Connection,
    sector: str,
    sector_db_path: str,
    anchor_system: str,
    anchor_coords: Tuple[float, float, float],
    since: str,
    limit: int,
    counters: Dict[str, int],
) -> None:
    """Write run metadata for traceability."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute("DELETE FROM run_metadata")
    metadata = {
        "sector": sector,
        "sector_db": sector_db_path,
        "anchor_system": anchor_system,
        "anchor_x": str(anchor_coords[0]),
        "anchor_y": str(anchor_coords[1]),
        "anchor_z": str(anchor_coords[2]),
        "since": since,
        "limit": str(limit),
        "run_timestamp": utc_now_iso(),
        "db_rings_evaluated": str(counters.get("db_rings_evaluated", 0)),
        "journal_rings_matched": str(counters.get("journal_rings_matched", 0)),
        "sector_systems_loaded": str(counters.get("sector_systems_loaded", 0)),
    }
    conn.executemany(
        "INSERT INTO run_metadata (key, value) VALUES (?, ?)",
        list(metadata.items()),
    )
    conn.commit()


def export_ranked_csv(conn: sqlite3.Connection, path: Path) -> None:
    import csv

    rows = conn.execute(
        """
        SELECT
            sector,
            category,
            rank,
            system_name,
            body_name,
            ring_name,
            surface_density,
            distance_to_anchor_ly,
            mapped_journal,
            mapped_db,
            mapped_final,
            source,
            updated_at
        FROM top_rings_ranked
        ORDER BY category ASC, rank ASC
        """
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "sector",
                "category",
                "rank",
                "system_name",
                "body_name",
                "ring_name",
                "surface_density",
                "distance_to_anchor_ly",
                "mapped_journal",
                "mapped_db",
                "mapped_final",
                "source",
                "updated_at",
            ]
        )
        for row in rows:
            writer.writerow(row)


def default_journal_dir() -> Path:
    user_profile = os.getenv("USERPROFILE") or str(Path.home())
    return Path(user_profile) / "Saved Games" / "Frontier Developments" / "Elite Dangerous"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Rank top rings for a sector.")
    parser.add_argument("--sector", default=None)
    parser.add_argument("--sector-library-dir", default=str(Path("data") / "sector_library"))
    parser.add_argument("--journals-dir", default=None)
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--export-csv", default=None)
    parser.add_argument(
        "--anchor-system",
        default=ANCHOR_SYSTEM_NAME,
        help=f"Anchor system for distance tie-breaking (default: {ANCHOR_SYSTEM_NAME})",
    )
    parser.add_argument(
        "--anchor-coords",
        default=None,
        help="Override anchor coordinates as x,y,z (e.g. '100.5,-20.3,45.0')",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.limit < 1:
        if not args.quiet:
            print("--limit must be >= 1.")
        return 1

    sector = args.sector
    if not sector:
        sys.stdout.write("Enter target sector name (e.g., eotchorts): ")
        sys.stdout.flush()
        sector = sys.stdin.readline().strip()
    if not sector:
        if not args.quiet:
            print("Sector name is required.")
        return 1

    sector_sanitized = sanitize_sector(sector)
    sector_db_path = Path(args.sector_library_dir) / f"sector_{sector_sanitized}.sqlite"
    if not sector_db_path.exists():
        if not args.quiet:
            print(f"Sector DB not found: {sector_db_path}")
        return 1

    journals_dir = Path(args.journals_dir) if args.journals_dir else default_journal_dir()
    try:
        since_dt = parse_since(args.since)
    except ValueError as exc:
        if not args.quiet:
            print(f"Error: {exc}")
        return 1

    anchor_system_name = args.anchor_system
    anchor_coords_override: Optional[Tuple[float, float, float]] = None
    if args.anchor_coords:
        try:
            parts = [float(p.strip()) for p in args.anchor_coords.split(",")]
            if len(parts) != 3:
                raise ValueError("expected 3 values")
            anchor_coords_override = (parts[0], parts[1], parts[2])
        except ValueError:
            if not args.quiet:
                print(f"Invalid --anchor-coords format: {args.anchor_coords!r} (expected x,y,z)")
            return 1

    conn = sqlite3.connect(str(sector_db_path))
    try:
        return _run_ranking(conn, args, sector, sector_sanitized, sector_db_path,
                            journals_dir, since_dt, anchor_system_name, anchor_coords_override)
    finally:
        conn.close()


def _run_ranking(
    conn: sqlite3.Connection,
    args,
    sector: str,
    sector_sanitized: str,
    sector_db_path: Path,
    journals_dir: Path,
    since_dt: datetime,
    anchor_system_name: str,
    anchor_coords_override: Optional[Tuple[float, float, float]],
) -> int:
    tables = table_infos(conn)
    ring_table = pick_best_table(tables, score_ring_table)
    system_table = pick_best_table(tables, score_system_table)
    body_table = pick_best_table(tables, score_body_table)
    if not ring_table:
        if not args.quiet:
            print("Unable to locate ring table in sector DB.")
        return 1
    if not system_table and not anchor_coords_override:
        if not args.quiet:
            print("Unable to locate system table in sector DB (required for anchor coordinates).")
        return 1

    system_key_to_name, system_name_to_key = build_system_maps(conn, system_table)
    body_key_to_name = build_body_map(conn, body_table)
    system_coords, anchor_coords = build_system_coords_map(
        conn, system_table, anchor_system_name
    )
    if anchor_coords_override:
        anchor_coords = anchor_coords_override
    if anchor_coords is None:
        if not args.quiet:
            print(f"Anchor system not found or missing coordinates: {anchor_system_name}")
            print("Hint: use --anchor-coords x,y,z to provide coordinates directly.")
        return 1
    sector_system_map, sector_system_names = build_system_name_set(
        conn, system_table, ring_table
    )
    if not sector_system_names:
        if not args.quiet:
            print("Unable to determine system names for sector DB.")
        return 1

    if not args.quiet:
        print(f"Using sector DB: {sector_db_path}")
        print(f"Ring table: {ring_table.name}")
        if system_table:
            print(f"System table: {system_table.name}")
        if body_table:
            print(f"Body table: {body_table.name}")
        print(f"Systems in sector DB: {len(sector_system_names)}")
        if args.verbose:
            print(f"Anchor system: {anchor_system_name} coords={anchor_coords}")

    counters: Dict[str, int] = {}
    counters["sector_systems_loaded"] = len(sector_system_names)

    try:
        top_lists, key_maps = build_top_lists_from_sector(
            conn,
            ring_table,
            system_key_to_name,
            body_key_to_name,
            system_coords,
            anchor_coords,
            args.limit,
            args.quiet,
            counters,
        )
    except RuntimeError as exc:
        if not args.quiet:
            print(f"Error: {exc}")
        return 1

    mapped_db_set, mapping_col = build_mapped_db_set(conn, body_table, ring_table)
    if mapping_col and not args.quiet:
        print(f"Mapping column detected in DB: {mapping_col}")

    replacements: Dict[str, int] = {cat: 0 for cat in CATEGORIES}
    journal_rings, mapped_bodies = parse_journals(
        journals_dir, since_dt, sector_system_map, counters
    )
    for entry in journal_rings.values():
        coords = system_coords.get(normalize_name(entry.system_name))
        if coords is None:
            counters["journal_missing_coords"] = counters.get("journal_missing_coords", 0) + 1
            if not args.quiet:
                print(
                    f"Warning: missing coords for system {entry.system_name}; skipping ring {entry.ring_name}"
                )
            continue
        entry.distance_to_anchor_ly = distance_between(coords, anchor_coords)
        entry.mapped_db = db_mapped(entry.system_name, entry.body_name, mapped_db_set)
        entry.mapped_final = entry.mapped_journal or entry.mapped_db
        action = update_top_list(
            top_lists[entry.category], key_maps[entry.category], entry, args.limit
        )
        if action == "replace":
            replacements[entry.category] += 1

    for category, rows in top_lists.items():
        rows.sort(key=ring_sort_key)
        for row in rows:
            body_key = (normalize_name(row.system_name), normalize_name(row.body_name))
            row.mapped_journal = row.mapped_journal or mapped_bodies.get(body_key, False)
            row.mapped_db = row.mapped_db or db_mapped(
                row.system_name, row.body_name, mapped_db_set
            )
            row.mapped_final = row.mapped_journal or row.mapped_db

    if not args.quiet:
        for category in CATEGORIES:
            print_table(category, top_lists[category])
        print("\nCounters:")
        print(f"  sector systems loaded: {counters.get('sector_systems_loaded', 0)}")
        print(f"  db rings evaluated: {counters.get('db_rings_evaluated', 0)}")
        print(f"  journal lines read: {counters.get('journal_lines', 0)}")
        print(f"  journal scan events: {counters.get('journal_scan_events', 0)}")
        print(f"  journal rings matched: {counters.get('journal_rings_matched', 0)}")
        print("  replacements per category:")
        for category in CATEGORIES:
            print(f"    {category}: {replacements.get(category, 0)}")
        print("  skipped ring records:")
        for key in [
            "db_missing_ring_name",
            "db_unknown_category",
            "db_missing_system_body",
            "db_missing_mass_radii",
            "db_invalid_radii",
            "db_invalid_density",
            "db_missing_coords",
            "journal_unknown_category",
            "journal_missing_mass_radii",
            "journal_invalid_radii",
            "journal_invalid_density",
            "journal_missing_coords",
        ]:
            if counters.get(key):
                print(f"    {key}: {counters.get(key, 0)}")
        if args.verbose:
            distances = [
                entry.distance_to_anchor_ly
                for category in CATEGORIES
                for entry in top_lists[category]
            ]
            if distances:
                print(
                    f"  final distance min/max: {min(distances):.2f} / {max(distances):.2f} LY"
                )

    selected_systems = {
        normalize_name(entry.system_name)
        for category in CATEGORIES
        for entry in top_lists[category]
    }
    selected_system_names = [
        sector_system_map[norm]
        for norm in selected_systems
        if norm in sector_system_map
    ]
    selected_system_keys = {
        system_name_to_key[name]
        for name in selected_system_names
        if name in system_name_to_key
    }

    output_db_path = Path(args.sector_library_dir) / f"top_rings_{sector_sanitized}.sqlite"
    if output_db_path.exists():
        output_db_path.unlink()
    with closing(sqlite3.connect(str(output_db_path))) as conn_out:
        copy_schema(conn, conn_out)

        for table in tables:
            if table.name == "top_rings_ranked" or table.name == "top_ring_flags":
                continue
            system_name_col = find_system_name_column(table)
            system_key_col = find_system_key_column(table)
            if system_name_col and selected_system_names:
                chunk = 900
                names = selected_system_names
                for idx in range(0, len(names), chunk):
                    batch = names[idx : idx + chunk]
                    placeholders = ", ".join(["?"] * len(batch))
                    where = f"WHERE {quote_ident(system_name_col)} IN ({placeholders})"
                    copy_table_rows(conn, conn_out, table, where, batch)
            elif system_key_col and selected_system_keys:
                chunk = 900
                keys = list(selected_system_keys)
                for idx in range(0, len(keys), chunk):
                    batch = keys[idx : idx + chunk]
                    placeholders = ", ".join(["?"] * len(batch))
                    where = f"WHERE {quote_ident(system_key_col)} IN ({placeholders})"
                    copy_table_rows(conn, conn_out, table, where, batch)
            else:
                if should_copy_entire_table(conn, table):
                    copy_table_rows(conn, conn_out, table, "", [])
                elif not args.quiet:
                    print(f"Skipping table without system linkage: {table.name}")

        write_top_rings_table(conn_out, sector_sanitized, top_lists)
        write_run_metadata(
            conn_out,
            sector_sanitized,
            str(sector_db_path),
            anchor_system_name,
            anchor_coords,
            args.since,
            args.limit,
            counters,
        )
        copy_indices_and_views(conn, conn_out)
        create_top_ring_view(conn_out, ring_table)
        if args.export_csv:
            export_ranked_csv(conn_out, Path(args.export_csv))

    if not args.quiet:
        print(f"\nOutput DB written to: {output_db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
