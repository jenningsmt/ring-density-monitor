from __future__ import annotations

"""Ingest Spansh ring data into rings_master.sqlite.

Raw JSON payload capture is optional and deduplicated for scale:
- systems_raw: one row per system_address
- bodies_raw: one row per (system_address, body_id)
- rings_payloads: one row per ring_id

By default (--store-raw-json none), rings_raw stays lean and raw_*_json columns are NULL.

If Spansh does not provide spectral strings, we store star "subType" label into
primary_star_subtype/primary_star_class for categorical analysis.
"""

import argparse
import gzip
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


DEFAULT_INPUT = Path("data/source_data/galaxy.json.gz")
DEFAULT_OUTPUT = Path("data/ring_hunter_library/rings_master.sqlite")
DEFAULT_COMMIT_EVERY = 25000
DEFAULT_PROGRESS_SECONDS = 10
DEFAULT_CHECKPOINT_EVERY = 100000
DEFAULT_STORE_RAW_JSON = "none"


RAW_JSON_MODES = {"none", "ring", "ring+body", "all"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_date_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def resolve_output_db(output_db: str, date_suffix: bool = False, date_iso: Optional[str] = None) -> Path:
    base = Path(output_db)
    if not date_suffix:
        return base
    suffix_date = date_iso or utc_date_iso()
    if base.suffix.lower() == ".sqlite":
        return base.with_name(f"{base.stem}_{suffix_date}{base.suffix}")
    return base.with_name(f"{base.name}_{suffix_date}")


def open_text(path: Path):
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def detect_format(path: Path) -> str:
    with open_text(path) as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("["):
                return "json_doc"
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    return "unknown"
                return "jsonl" if isinstance(parsed, dict) else "json_doc"
            if stripped.startswith("{"):
                return "json_doc"
            return "unknown"
    return "unknown"


def iter_systems_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with open_text(path) as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                yield parsed


def iter_systems_json_doc(path: Path) -> Iterable[dict[str, Any]]:
    decoder = json.JSONDecoder()
    with open_text(path) as handle:
        buffer = ""
        pos = 0
        eof = False

        def read_more() -> bool:
            nonlocal buffer, eof
            chunk = handle.read(65536)
            if chunk == "":
                eof = True
                return False
            buffer += chunk
            return True

        def skip_whitespace() -> None:
            nonlocal pos
            while True:
                while pos < len(buffer) and buffer[pos].isspace():
                    pos += 1
                if pos < len(buffer):
                    return
                if not read_more():
                    return

        skip_whitespace()
        if pos >= len(buffer):
            return
        if buffer[pos] != "[":
            raise ValueError("Expected JSON document array ('[').")
        pos += 1

        while True:
            skip_whitespace()
            if pos >= len(buffer):
                if eof:
                    raise ValueError("Unexpected EOF while parsing JSON array.")
                read_more()
                continue

            if buffer[pos] == "]":
                return

            while True:
                try:
                    parsed, end = decoder.raw_decode(buffer, pos)
                    break
                except json.JSONDecodeError:
                    if eof:
                        raise
                    if not read_more():
                        raise
            pos = end
            if isinstance(parsed, dict):
                yield parsed

            skip_whitespace()
            if pos >= len(buffer):
                if eof:
                    raise ValueError("Unexpected EOF after JSON value.")
                read_more()
                continue
            if buffer[pos] == ",":
                pos += 1
            elif buffer[pos] == "]":
                return
            else:
                raise ValueError(f"Expected ',' or ']' in JSON array, found {buffer[pos]!r}.")

            if pos > 1_000_000:
                buffer = buffer[pos:]
                pos = 0


def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def first_value(data: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def as_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    return None


def normalize_id_component(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return format(value, ".15g")
    return str(value)


def compute_ring_width(inner_radius: Optional[float], outer_radius: Optional[float]) -> Optional[float]:
    if inner_radius is None or outer_radius is None:
        return None
    if outer_radius <= inner_radius:
        return None
    return outer_radius - inner_radius


def compute_ring_area(inner_radius: Optional[float], outer_radius: Optional[float]) -> Optional[float]:
    if inner_radius is None or outer_radius is None:
        return None
    if outer_radius <= inner_radius:
        return None
    return math.pi * ((outer_radius * outer_radius) - (inner_radius * inner_radius))


def compute_surface_density(mass: Optional[float], ring_area: Optional[float]) -> Optional[float]:
    if mass is None or ring_area is None or ring_area <= 0:
        return None
    return mass / ring_area


def compute_linear_density(mass: Optional[float], ring_width: Optional[float]) -> Optional[float]:
    if mass is None or ring_width is None or ring_width <= 0:
        return None
    return mass / ring_width


def compute_ring_id(
    system_address: Optional[int],
    system_name: str,
    body_id: Optional[int],
    body_name: str,
    ring_name: str,
    inner_rad_source: Any,
    outer_rad_source: Any,
) -> str:
    source = (
        f"{normalize_id_component(system_address if system_address is not None else system_name)}|"
        f"{normalize_id_component(body_id if body_id is not None else body_name)}|"
        f"{normalize_id_component(ring_name)}|"
        f"{normalize_id_component(inner_rad_source)}|"
        f"{normalize_id_component(outer_rad_source)}"
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rings_raw (
            ring_id TEXT PRIMARY KEY,
            system_name TEXT NOT NULL,
            system_address INTEGER NULL,
            x REAL NULL,
            y REAL NULL,
            z REAL NULL,
            primary_star_spectral TEXT NULL,
            primary_star_type TEXT NULL,
            primary_star_subtype TEXT NULL,
            primary_star_luminosity TEXT NULL,
            primary_star_class TEXT NULL,
            body_name TEXT NOT NULL,
            body_id INTEGER NULL,
            body_type TEXT NULL,
            body_sub_type TEXT NULL,
            ring_name TEXT NOT NULL,
            ring_type TEXT NULL,
            reserve_level TEXT NULL,
            mass REAL NULL,
            inner_radius REAL NULL,
            outer_radius REAL NULL,
            ring_width REAL NULL,
            ring_area REAL NULL,
            surface_density REAL NULL,
            linear_density REAL NULL,
            arrival_distance_ls REAL NULL,
            parent_body_gravity REAL NULL,
            raw_ring_json TEXT NULL,
            raw_body_json TEXT NULL,
            raw_system_json TEXT NULL,
            dens_percentile REAL NULL,
            area_percentile REAL NULL,
            arrival_dist_percentile REAL NULL,
            linear_density_percentile REAL NULL,
            parent_gravity_percentile REAL NULL,
            moi_base REAL NULL,
            moi_survey REAL NULL,
            moi_final REAL NULL,
            moi_version TEXT NULL,
            moi_ssd_tritium REAL NULL,
            moi_ssd_version TEXT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    rings_raw_cols = {row[1] for row in conn.execute("PRAGMA table_info(rings_raw)").fetchall()}
    for col_name in (
        "primary_star_spectral",
        "primary_star_type",
        "primary_star_subtype",
        "primary_star_luminosity",
        "primary_star_class",
    ):
        if col_name not in rings_raw_cols:
            conn.execute(f"ALTER TABLE rings_raw ADD COLUMN {col_name} TEXT NULL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ring_survey (
            ring_id TEXT PRIMARY KEY,
            dss_completed INTEGER,
            target_hotspot_count INTEGER,
            target_hotspot_confidence TEXT,
            overlap_status TEXT,
            overlap_count_class TEXT,
            last_updated_utc TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rings_scored (
            ring_id TEXT PRIMARY KEY,
            ring_type TEXT NULL,
            score_version TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            moi_raw REAL NULL,
            moi_normalized REAL NULL,
            moi_final REAL NULL,
            ssd_linear_density REAL NULL,
            ssd_parent_g REAL NULL,
            ssd_score_raw REAL NULL,
            ssd_score REAL NULL,
            norm_population TEXT NULL,
            norm_count INTEGER NULL,
            flags TEXT NULL
        )
        """
    )
    ring_scored_cols = {row[1] for row in conn.execute("PRAGMA table_info(rings_scored)").fetchall()}
    if "ring_type" not in ring_scored_cols:
        conn.execute("ALTER TABLE rings_scored ADD COLUMN ring_type TEXT NULL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS score_runs (
            run_id INTEGER PRIMARY KEY,
            score_version TEXT NOT NULL,
            params_json TEXT NULL,
            git_commit TEXT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS systems_raw (
            system_address INTEGER PRIMARY KEY,
            system_name TEXT,
            raw_system_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bodies_raw (
            system_address INTEGER NOT NULL,
            body_id INTEGER NOT NULL,
            body_name TEXT,
            raw_body_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (system_address, body_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rings_payloads (
            ring_id TEXT PRIMARY KEY,
            raw_ring_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    # Keep indexing minimal for ingest/runtime performance.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rings_raw_ring_id ON rings_raw(ring_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ring_survey_ring_id ON ring_survey(ring_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rings_scored_score_version ON rings_scored(score_version)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rings_raw_system ON rings_raw(system_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rings_raw_ring_type ON rings_raw(ring_type)")
    conn.commit()


def extract_system_name(system: dict[str, Any]) -> Optional[str]:
    return as_str(first_value(system, ("name", "systemName", "StarSystem")))


def extract_system_address(system: dict[str, Any]) -> Optional[int]:
    return safe_int(first_value(system, ("id64", "systemAddress", "SystemAddress")))


def extract_coords(system: dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    coords = system.get("coords")
    if isinstance(coords, dict):
        return (
            safe_float(coords.get("x")),
            safe_float(coords.get("y")),
            safe_float(coords.get("z")),
        )
    return (
        safe_float(system.get("x")),
        safe_float(system.get("y")),
        safe_float(system.get("z")),
    )


def extract_bodies(system: dict[str, Any]) -> list[dict[str, Any]]:
    bodies = first_value(system, ("bodies", "Bodies"))
    if isinstance(bodies, list):
        return [body for body in bodies if isinstance(body, dict)]
    return []


def extract_rings(body: dict[str, Any]) -> list[dict[str, Any]]:
    rings = first_value(body, ("rings", "Rings"))
    if isinstance(rings, list):
        return [ring for ring in rings if isinstance(ring, dict)]
    return []


def build_ring_row(system: dict[str, Any], body: dict[str, Any], ring: dict[str, Any], updated_at: str) -> Optional[tuple[Any, ...]]:
    system_name = extract_system_name(system)
    body_name = as_str(first_value(body, ("name", "Name", "bodyName", "BodyName")))
    ring_name = as_str(first_value(ring, ("name", "Name", "ringName", "RingName")))
    if not system_name or not body_name or not ring_name:
        return None

    system_address = extract_system_address(system)
    x, y, z = extract_coords(system)
    body_id = safe_int(first_value(body, ("bodyId", "BodyID", "id")))
    body_type = as_str(first_value(body, ("type", "bodyType", "class")))
    body_sub_type = as_str(first_value(body, ("subType", "subtype", "subClass", "subclass", "planetClass")))
    ring_type = as_str(first_value(ring, ("ringClass", "RingClass", "type", "class")))
    reserve_level = as_str(first_value(ring, ("reserveLevel", "ReserveLevel")))
    mass = safe_float(first_value(ring, ("massMT", "MassMT", "mass")))
    inner_source = first_value(ring, ("innerRad", "InnerRad", "innerRadius", "InnerRadius"))
    outer_source = first_value(ring, ("outerRad", "OuterRad", "outerRadius", "OuterRadius"))
    inner_radius = safe_float(inner_source)
    outer_radius = safe_float(outer_source)
    arrival_distance_ls = safe_float(first_value(body, ("distanceToArrival", "DistanceFromArrivalLS", "distanceFromArrivalLS")))
    parent_body_gravity = safe_float(first_value(body, ("gravity", "Gravity", "surfaceGravity", "SurfaceGravity")))

    ring_width = compute_ring_width(inner_radius, outer_radius)
    ring_area = compute_ring_area(inner_radius, outer_radius)
    surface_density = compute_surface_density(mass, ring_area)
    linear_density = compute_linear_density(mass, ring_width)
    primary_star = extract_primary_star_fields(system)

    ring_id = compute_ring_id(
        system_address=system_address,
        system_name=system_name,
        body_id=body_id,
        body_name=body_name,
        ring_name=ring_name,
        inner_rad_source=inner_source,
        outer_rad_source=outer_source,
    )

    return (
        ring_id,
        system_name,
        system_address,
        x,
        y,
        z,
        primary_star["primary_star_spectral"],
        primary_star["primary_star_type"],
        primary_star["primary_star_subtype"],
        primary_star["primary_star_luminosity"],
        primary_star["primary_star_class"],
        body_name,
        body_id,
        body_type,
        body_sub_type,
        ring_name,
        ring_type,
        reserve_level,
        mass,
        inner_radius,
        outer_radius,
        ring_width,
        ring_area,
        surface_density,
        linear_density,
        arrival_distance_ls,
        parent_body_gravity,
        None,
        None,
        None,
        updated_at,
    )


def upsert_ring_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO rings_raw (
            ring_id,
            system_name,
            system_address,
            x,
            y,
            z,
            primary_star_spectral,
            primary_star_type,
            primary_star_subtype,
            primary_star_luminosity,
            primary_star_class,
            body_name,
            body_id,
            body_type,
            body_sub_type,
            ring_name,
            ring_type,
            reserve_level,
            mass,
            inner_radius,
            outer_radius,
            ring_width,
            ring_area,
            surface_density,
            linear_density,
            arrival_distance_ls,
            parent_body_gravity,
            raw_ring_json,
            raw_body_json,
            raw_system_json,
            updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def upsert_system_payload_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO systems_raw (
            system_address, system_name, raw_system_json, updated_at_utc
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(system_address) DO UPDATE SET
            system_name=excluded.system_name,
            raw_system_json=excluded.raw_system_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        rows,
    )


def upsert_body_payload_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO bodies_raw (
            system_address, body_id, body_name, raw_body_json, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(system_address, body_id) DO UPDATE SET
            body_name=excluded.body_name,
            raw_body_json=excluded.raw_body_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        rows,
    )


def upsert_ring_payload_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO rings_payloads (
            ring_id, raw_ring_json, updated_at_utc
        ) VALUES (?, ?, ?)
        ON CONFLICT(ring_id) DO UPDATE SET
            raw_ring_json=excluded.raw_ring_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        rows,
    )


def include_system_payload(mode: str) -> bool:
    return mode == "all"


def include_body_payload(mode: str) -> bool:
    return mode in {"ring+body", "all"}


def include_ring_payload(mode: str) -> bool:
    return mode in {"ring", "ring+body", "all"}


def _is_star_like(body: dict[str, Any]) -> bool:
    body_type = as_str(first_value(body, ("type", "bodyType", "BodyType", "body_type")))
    if isinstance(body_type, str) and body_type.strip().lower() == "star":
        return True
    star_type = as_str(first_value(body, ("starType", "StarType")))
    if isinstance(star_type, str) and star_type.strip():
        return True
    return False


def _parse_spectral_components(
    spectral: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    if spectral is None:
        return None, None, None, None, None
    text = spectral.strip()
    if not text:
        return None, None, None, None, None
    m = re.search(
        r"^\s*([OBAFGKMLTY])\s*([0-9](?:\.[0-9])?)?\s*(VII|VI|IV|III|II|I|V)?\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        return text, None, None, None, None
    star_type = m.group(1).upper() if m.group(1) else None
    subtype = m.group(2) if m.group(2) else None
    luminosity = m.group(3).upper() if m.group(3) else None
    star_class = None
    if star_type is not None and subtype is not None:
        star_class = f"{star_type}{subtype}".strip()
        if luminosity:
            star_class = f"{star_class} {luminosity}".strip()
    return text, star_type, subtype, luminosity, star_class


def extract_primary_star_fields(system: dict[str, Any]) -> dict[str, Optional[str]]:
    empty = {
        "primary_star_spectral": None,
        "primary_star_type": None,
        "primary_star_subtype": None,
        "primary_star_luminosity": None,
        "primary_star_class": None,
    }
    bodies = extract_bodies(system)
    if not bodies:
        return empty

    explicit: list[tuple[int, int, dict[str, Any]]] = []
    fallback: list[tuple[int, int, dict[str, Any]]] = []
    for idx, body in enumerate(bodies):
        if not _is_star_like(body):
            continue
        body_id = safe_int(first_value(body, ("bodyId", "BodyID", "id")))
        sort_id = body_id if body_id is not None else 10**12 + idx
        is_primary = bool(
            first_value(body, ("isMainStar", "mainStar", "isPrimaryStar", "primaryStar"))
        )
        target = explicit if is_primary else fallback
        target.append((sort_id, idx, body))

    candidates = explicit if explicit else fallback
    if not candidates:
        return empty
    candidates.sort(key=lambda item: (item[0], item[1]))
    star = candidates[0][2]

    spectral_candidate = as_str(first_value(star, ("spectralClass", "spectral_type", "spectralType")))
    spectral = spectral_candidate.strip() if isinstance(spectral_candidate, str) else None
    if spectral == "":
        spectral = None

    subtype_label = as_str(first_value(star, ("subType", "subtype")))
    if isinstance(subtype_label, str):
        subtype_label = subtype_label.strip() or None

    luminosity = as_str(first_value(star, ("luminosity",)))
    if isinstance(luminosity, str):
        luminosity = luminosity.strip() or None

    if spectral is None:
        return {
            "primary_star_spectral": None,
            "primary_star_type": None,
            "primary_star_subtype": subtype_label,
            "primary_star_luminosity": luminosity,
            "primary_star_class": subtype_label,
        }

    primary_spectral, star_type, subtype, spectral_luminosity, star_class = _parse_spectral_components(spectral)
    final_luminosity = spectral_luminosity if spectral_luminosity is not None else luminosity
    if star_type is not None and subtype is not None:
        if final_luminosity:
            star_class = f"{star_type}{subtype} {final_luminosity}".strip()
        else:
            star_class = f"{star_type}{subtype}".strip()
        final_subtype = subtype
    else:
        final_subtype = subtype_label
        star_class = subtype_label
    return {
        "primary_star_spectral": primary_spectral,
        "primary_star_type": star_type,
        "primary_star_subtype": final_subtype,
        "primary_star_luminosity": final_luminosity,
        "primary_star_class": star_class,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest ring master data from Spansh galaxy.json.gz into SQLite.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Path to Spansh galaxy.json(.gz).")
    parser.add_argument("--output-db", default=str(DEFAULT_OUTPUT), help="Output SQLite path.")
    parser.add_argument("--date-suffix", action="store_true", help="Append UTC date to output DB filename.")
    parser.add_argument("--commit-every", type=int, default=DEFAULT_COMMIT_EVERY)
    parser.add_argument("--progress-seconds", type=int, default=DEFAULT_PROGRESS_SECONDS)
    parser.add_argument("--limit", type=int, default=None, help="Stop after N rings inserted.")
    parser.add_argument("--wal", action="store_true", help="Enable WAL mode on this connection.")
    parser.add_argument(
        "--synchronous",
        choices=("FULL", "NORMAL", "OFF"),
        default="NORMAL",
        help="Set PRAGMA synchronous for this connection.",
    )
    parser.add_argument("--checkpoint-every", type=int, default=DEFAULT_CHECKPOINT_EVERY)
    parser.add_argument(
        "--store-raw-json",
        choices=sorted(RAW_JSON_MODES),
        default=DEFAULT_STORE_RAW_JSON,
        help="Optional deduplicated payload capture mode.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress and final summary output.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input)
    output_path = resolve_output_db(args.output_db, date_suffix=args.date_suffix)

    if args.commit_every <= 0:
        print("--commit-every must be >= 1.")
        return 2
    if args.progress_seconds <= 0:
        print("--progress-seconds must be >= 1.")
        return 2
    if args.limit is not None and args.limit < 0:
        print("--limit must be >= 0.")
        return 2
    if args.checkpoint_every <= 0:
        print("--checkpoint-every must be >= 1.")
        return 2

    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output_path)
    journal_mode = "delete"
    try:
        if args.wal:
            wal_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if wal_row and wal_row[0]:
                journal_mode = str(wal_row[0]).lower()
        else:
            mode_row = conn.execute("PRAGMA journal_mode").fetchone()
            if mode_row and mode_row[0]:
                journal_mode = str(mode_row[0]).lower()
        conn.execute(f"PRAGMA synchronous={args.synchronous}")
        init_db(conn)
    except Exception:
        conn.close()
        raise

    source_format = detect_format(input_path)
    if source_format == "unknown":
        print("Unable to detect source format (jsonl vs json document array).")
        conn.close()
        return 1

    iterator: Iterable[dict[str, Any]]
    if source_format == "jsonl":
        iterator = iter_systems_jsonl(input_path)
    else:
        iterator = iter_systems_json_doc(input_path)

    systems_scanned = 0
    rings_seen = 0
    rows_written = 0
    pending: list[tuple[Any, ...]] = []
    pending_system_payloads: list[tuple[Any, ...]] = []
    pending_body_payloads: list[tuple[Any, ...]] = []
    pending_ring_payloads: list[tuple[Any, ...]] = []
    start = time.monotonic()
    last_progress = start
    last_checkpoint_rows = 0
    stop_at_limit = False

    def flush_pending() -> None:
        nonlocal rows_written, last_checkpoint_rows
        if not pending and not pending_system_payloads and not pending_body_payloads and not pending_ring_payloads:
            return
        conn.execute("BEGIN")
        upsert_ring_rows(conn, pending)
        upsert_system_payload_rows(conn, pending_system_payloads)
        upsert_body_payload_rows(conn, pending_body_payloads)
        upsert_ring_payload_rows(conn, pending_ring_payloads)
        conn.commit()
        rows_written += len(pending)
        pending.clear()
        pending_system_payloads.clear()
        pending_body_payloads.clear()
        pending_ring_payloads.clear()
        if journal_mode == "wal" and (rows_written - last_checkpoint_rows) >= args.checkpoint_every:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            last_checkpoint_rows = rows_written

    def print_progress(force: bool = False) -> None:
        nonlocal last_progress
        if args.quiet:
            return
        now = time.monotonic()
        if not force and (now - last_progress) < args.progress_seconds:
            return
        elapsed = max(0.0, now - start)
        rings_inserted = rows_written + len(pending)
        rate = (rings_inserted / elapsed) if elapsed > 0 else 0.0
        systems_rate = (systems_scanned / elapsed) if elapsed > 0 else 0.0
        print(
            (
                f"progress systems_scanned={systems_scanned} rings_seen={rings_seen} "
                f"rings_inserted={rings_inserted} elapsed_seconds={elapsed:.2f} "
                f"rings_per_sec={rate:.2f} systems_per_sec={systems_rate:.2f}"
            ),
            flush=True,
        )
        last_progress = now

    try:
        for system in iterator:
            systems_scanned += 1

            if not isinstance(system, dict):
                print_progress()
                continue

            bodies = extract_bodies(system)
            seen_bodies: set[tuple[int, int]] = set()
            system_payload_added = False
            for body in bodies:
                system_address = extract_system_address(system)
                body_id = safe_int(first_value(body, ("bodyId", "BodyID", "id")))
                if include_system_payload(args.store_raw_json) and not system_payload_added:
                    if system_address is not None:
                        pending_system_payloads.append(
                            (
                                system_address,
                                extract_system_name(system),
                                json.dumps(system, ensure_ascii=True, sort_keys=True, default=str),
                                utc_now_iso(),
                            )
                        )
                        system_payload_added = True
                if include_body_payload(args.store_raw_json):
                    if system_address is not None and body_id is not None:
                        key = (system_address, body_id)
                        if key not in seen_bodies:
                            seen_bodies.add(key)
                            pending_body_payloads.append(
                                (
                                    system_address,
                                    body_id,
                                    as_str(first_value(body, ("name", "Name", "bodyName", "BodyName"))),
                                    json.dumps(body, ensure_ascii=True, sort_keys=True, default=str),
                                    utc_now_iso(),
                                )
                            )
                for ring in extract_rings(body):
                    rings_seen += 1
                    rings_inserted_so_far = rows_written + len(pending)
                    if args.limit is not None and rings_inserted_so_far >= args.limit:
                        stop_at_limit = True
                        break
                    row = build_ring_row(system, body, ring, utc_now_iso())
                    if row is None:
                        continue
                    pending.append(row)
                    if include_ring_payload(args.store_raw_json):
                        pending_ring_payloads.append(
                            (
                                row[0],
                                json.dumps(ring, ensure_ascii=True, sort_keys=True, default=str),
                                utc_now_iso(),
                            )
                        )
                if stop_at_limit:
                    break
            if stop_at_limit:
                break

            if len(pending) >= args.commit_every:
                flush_pending()

            if args.verbose:
                print_progress()
            else:
                print_progress()

        flush_pending()
        print_progress(force=True)
    except KeyboardInterrupt:
        try:
            flush_pending()
        finally:
            conn.close()
        print("Interrupted by user.")
        return 130
    except Exception:
        conn.rollback()
        conn.close()
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    elapsed = max(0.0, time.monotonic() - start)
    rate = (rows_written / elapsed) if elapsed > 0 else 0.0
    if not args.quiet:
        print(f"Source format: {source_format}")
        print(f"Systems scanned: {systems_scanned}")
        print(f"Rings seen: {rings_seen}")
        print(f"Rows upserted: {rows_written}")
        print(f"Elapsed seconds: {elapsed:.2f}")
        print(f"Rings/sec: {rate:.2f}")
        print(f"Journal mode: {journal_mode}")
        print(f"Output DB: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
