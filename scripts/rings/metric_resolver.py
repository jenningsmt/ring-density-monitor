from __future__ import annotations

import re
import sqlite3


FALLBACK_MOI_COLUMNS = ["moi_final", "moi_normalized", "moi_raw", "moi0", "moi_0", "moi"]


def get_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def resolve_moi_metric(conn: sqlite3.Connection, preferred: str | None = None) -> str:
    cols = get_table_columns(conn, "rings_scored")
    if preferred is not None:
        if preferred in cols:
            return preferred
        raise ValueError(
            f"Requested MOI metric '{preferred}' not found in rings_scored. "
            f"Available columns: {', '.join(sorted(cols))}"
        )
    for candidate in FALLBACK_MOI_COLUMNS:
        if candidate in cols:
            return candidate
    raise ValueError(
        "No MOI metric column found in rings_scored. "
        f"Tried: {', '.join(FALLBACK_MOI_COLUMNS)}. "
        f"Available columns: {', '.join(sorted(cols))}"
    )


def sanitize_identifier_for_index(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower()
