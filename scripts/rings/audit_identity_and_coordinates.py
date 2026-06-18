from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from contextlib import closing


DEFAULT_OUT = Path("docs/ring_hunter/phase4_identity_coordinate_audit.md")
IDENTITY_TOKENS = ("system", "star", "address", "name", "body", "planet", "belt")
COORD_CANDIDATES = (
    "x",
    "y",
    "z",
    "coord_x",
    "coord_y",
    "coord_z",
    "system_x",
    "system_y",
    "system_z",
    "pos_x",
    "pos_y",
    "pos_z",
)
RINGS_RAW_IDENTITY_PREF = (
    "system_name",
    "star_system",
    "system_address",
    "body_name",
    "body_id",
    "ring_name",
    "x",
    "y",
    "z",
)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def coord_columns(columns: list[str]) -> list[str]:
    lowered = {c.lower(): c for c in columns}
    out: list[str] = []
    for candidate in COORD_CANDIDATES:
        if candidate in lowered:
            out.append(lowered[candidate])
    return out


def identity_columns(columns: list[str]) -> list[str]:
    out: list[str] = []
    for col in columns:
        low = col.lower()
        if any(tok in low for tok in IDENTITY_TOKENS):
            out.append(col)
    return out


def markdown_table(name: str, cols: list[str]) -> str:
    return f"- `{name}`: {', '.join(f'`{c}`' for c in cols) if cols else '(none)'}"


def probe_join_cohort_to_rings_raw(conn: sqlite3.Connection, probe_n: int) -> tuple[list[str], list[sqlite3.Row], str]:
    tables = set(list_tables(conn))
    if "cohort_members" not in tables or "rings_raw" not in tables:
        return [], [], "Skipped: requires both `cohort_members` and `rings_raw`."
    cm_cols = set(table_columns(conn, "cohort_members"))
    rr_cols = set(table_columns(conn, "rings_raw"))
    if "ring_id" not in cm_cols or "ring_id" not in rr_cols:
        return [], [], "Skipped: `ring_id` missing in one of the tables."

    selected_rr = [c for c in RINGS_RAW_IDENTITY_PREF if c in rr_cols]
    if not selected_rr:
        return [], [], "Skipped: no preferred identity/coord columns present in `rings_raw`."

    conn.row_factory = sqlite3.Row
    select_cols = ", ".join([f"rr.{c}" for c in selected_rr])
    query = f"""
        SELECT cm.ring_id, {select_cols}
        FROM cohort_members cm
        JOIN rings_raw rr ON rr.ring_id = cm.ring_id
        ORDER BY cm.ring_id ASC
        LIMIT ?
    """
    rows = conn.execute(query, (probe_n,)).fetchall()
    return ["ring_id", *selected_rr], rows, "OK"


def probe_rings_raw_to_coord_tables(
    conn: sqlite3.Connection,
    probe_n: int,
) -> tuple[list[str], list[tuple[list[str], list[sqlite3.Row], str]]]:
    tables = list_tables(conn)
    if "rings_raw" not in tables:
        return [], []
    rr_cols = set(table_columns(conn, "rings_raw"))
    candidate_tables: list[str] = []
    for table in tables:
        if table == "rings_raw":
            continue
        cols = table_columns(conn, table)
        if coord_columns(cols):
            candidate_tables.append(table)

    probes: list[tuple[list[str], list[sqlite3.Row], str]] = []
    for table in candidate_tables:
        t_cols = set(table_columns(conn, table))
        join_mode = None
        if "system_address" in rr_cols and "system_address" in t_cols:
            join_mode = ("system_address", "system_address")
        elif "system_name" in rr_cols and "system_name" in t_cols:
            join_mode = ("system_name", "system_name")
        if join_mode is None:
            continue

        t_coord_cols = coord_columns(list(t_cols))
        if not t_coord_cols:
            continue
        conn.row_factory = sqlite3.Row
        left_key, right_key = join_mode
        select_cols = ", ".join([f"rr.{left_key} AS rr_{left_key}"] + [f"t.{c}" for c in t_coord_cols])
        query = f"""
            SELECT rr.ring_id, {select_cols}
            FROM rings_raw rr
            JOIN {table} t ON rr.{left_key} = t.{right_key}
            ORDER BY rr.ring_id ASC
            LIMIT ?
        """
        rows = conn.execute(query, (probe_n,)).fetchall()
        label = f"`rings_raw.{left_key} -> {table}.{right_key}`"
        probes.append((["ring_id", f"rr_{left_key}", *t_coord_cols], rows, label))

    return candidate_tables, probes


def format_probe_rows(rows: list[sqlite3.Row], columns: list[str]) -> list[str]:
    out: list[str] = []
    for row in rows:
        parts = [f"{col}={row[col]!r}" for col in columns]
        out.append("- " + ", ".join(parts))
    return out


def choose_recommendations(conn: sqlite3.Connection) -> tuple[str, str, str]:
    tables = set(list_tables(conn))
    if "rings_raw" not in tables:
        return (
            "No recommendation: `rings_raw` table missing.",
            "No recommendation: `rings_raw` table missing.",
            "No recommendation: `rings_raw` table missing.",
        )
    rr_cols = set(table_columns(conn, "rings_raw"))

    id_cols = [c for c in ("system_name", "body_name", "ring_name") if c in rr_cols]
    identity_path = (
        f"`cohort_members.ring_id -> rings_raw.ring_id` then select {', '.join(f'`rings_raw.{c}`' for c in id_cols)}"
        if id_cols
        else "`cohort_members.ring_id -> rings_raw.ring_id` (identity columns need manual selection)"
    )

    if {"x", "y", "z"}.issubset(rr_cols):
        coord_path = "`cohort_members.ring_id -> rings_raw.ring_id` then select `rings_raw.x`, `rings_raw.y`, `rings_raw.z`"
    else:
        coord_path = "Direct `rings_raw.x/y/z` not fully present; use `rings_raw` join key (system_address or system_name) into a system/coords table."

    edmining_note = (
        "Likely join candidate: EDMining `system_name + planets` to Ring Hunter `rings_raw.system_name + rings_raw.body_name`.\n"
        "Normalize EDMining planets by splitting on `and`, stripping `rings`, trimming whitespace, and lowercasing."
    )
    return identity_path, coord_path, edmining_note


def run_audit(db_path: Path, out_path: Path, probe_n: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        tables = list_tables(conn)
        table_to_cols = {t: table_columns(conn, t) for t in tables}
        coord_tables = {t: coord_columns(cols) for t, cols in table_to_cols.items() if coord_columns(cols)}
        identity_table_cols = {t: identity_columns(cols) for t, cols in table_to_cols.items() if identity_columns(cols)}
        probe_cols, probe_rows, probe_status = probe_join_cohort_to_rings_raw(conn, probe_n)
        coord_candidates, coord_probes = probe_rings_raw_to_coord_tables(conn, probe_n)
        identity_path, coord_path, edmining_note = choose_recommendations(conn)

    lines: list[str] = []
    lines.append("# Phase 4.0 Identity and Coordinate Audit")
    lines.append("")
    lines.append(f"- DB: `{db_path}`")
    lines.append(f"- Probe rows per join: `{probe_n}`")
    lines.append("")
    lines.append("## Table Inventory")
    if not tables:
        lines.append("- No user tables found.")
    else:
        for table in tables:
            lines.append(markdown_table(table, table_to_cols[table]))
    lines.append("")
    lines.append("## Coordinate Candidate Columns")
    if not coord_tables:
        lines.append("- No coordinate-like columns detected.")
    else:
        for table, cols in sorted(coord_tables.items()):
            lines.append(markdown_table(table, cols))
    lines.append("")
    lines.append("## Identity Candidate Columns")
    if not identity_table_cols:
        lines.append("- No identity-like columns detected.")
    else:
        for table, cols in sorted(identity_table_cols.items()):
            lines.append(markdown_table(table, cols))
    lines.append("")
    lines.append("## rings_raw Focus")
    if "rings_raw" in table_to_cols:
        rr = table_to_cols["rings_raw"]
        lines.append(markdown_table("rings_raw", rr))
        key_checks = {
            "system name": any(c in rr for c in ("system_name", "star_system", "name")),
            "system_address": "system_address" in rr,
            "body name/designation": any(c in rr for c in ("body_name", "body", "planet", "body_id")),
            "ring name/designation": "ring_name" in rr,
            "coordinates x/y/z": all(c in rr for c in ("x", "y", "z")),
        }
        for label, ok in key_checks.items():
            lines.append(f"- {label}: {'yes' if ok else 'no'}")
    else:
        lines.append("- `rings_raw` not found.")
    lines.append("")
    lines.append("## Join Probe 1: cohort_members -> rings_raw")
    lines.append(f"- Status: {probe_status}")
    if probe_cols and probe_rows:
        lines.append(f"- Selected columns: {', '.join(f'`{c}`' for c in probe_cols)}")
        lines.extend(format_probe_rows(probe_rows, probe_cols))
    lines.append("")
    lines.append("## Join Probe 2: rings_raw -> coordinate tables")
    if not coord_candidates:
        lines.append("- No separate coordinate tables detected for join probing.")
    elif not coord_probes:
        lines.append("- Candidate coordinate tables exist but no compatible join key (`system_address`/`system_name`) detected.")
    else:
        for cols, rows, label in coord_probes:
            lines.append(f"- Path: {label}")
            lines.append(f"- Columns: {', '.join(f'`{c}`' for c in cols)}")
            if rows:
                lines.extend(format_probe_rows(rows, cols))
            else:
                lines.append("- (no sample rows returned)")
    lines.append("")
    lines.append("## Recommended Join Paths")
    lines.append(f"- Identity path: {identity_path}")
    lines.append(f"- Coordinate path: {coord_path}")
    lines.append("")
    lines.append("## EDMining Join Feasibility")
    lines.append(f"- {edmining_note}")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit ring_id identity/coordinate join paths for Phase 4.0.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Markdown report output path.")
    parser.add_argument("--probe-n", type=int, default=5, help="Number of sample rows per join probe.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_audit(Path(args.db), Path(args.out), max(args.probe_n, 1))
    print(f"Wrote audit report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
