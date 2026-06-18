"""Canonical ring baseline library contracts and failure-safe loaders.

This module is the single normalization authority for journal/plain ring-type
strings and baseline library keys. Loader behavior is intentionally conservative:
missing/malformed library data yields an explicit unavailable state (empty result),
never fabricated medians.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CANONICAL_RING_KEYS: tuple[str, ...] = ("Icy", "Metallic", "Metal Rich", "Rocky")


@dataclass(frozen=True)
class RingBaselineEntry:
    ring_type: str
    baseline_median: float
    method: str
    source_date: str
    provenance_notes: str


def normalize_ring_type_key(value: object) -> Optional[str]:
    """Normalize journal/plain ring class strings to canonical ring keys.

    Returns one of: Icy, Metallic, Metal Rich, Rocky, or None when unrecognized.
    """
    text = str(value or "").strip()
    if not text:
        return None

    lowered = text.lower().replace("_", " ").replace("-", " ")
    lowered = " ".join(lowered.split())
    lowered = lowered.replace("eringclass ", "")
    lowered = lowered.replace("eringclass", "")

    alias_map = {
        "icy": "Icy",
        "metalic": "Metallic",  # journal typo variant
        "metallic": "Metallic",
        "metal rich": "Metal Rich",
        "metalrich": "Metal Rich",
        "rocky": "Rocky",
    }

    if lowered in alias_map:
        return alias_map[lowered]

    squashed = lowered.replace(" ", "")
    if squashed in alias_map:
        return alias_map[squashed]

    return None


def load_ring_baseline_library(path: str | Path) -> dict[str, RingBaselineEntry]:
    """Load ring baseline library with failure-safe, non-fabricating semantics.

    On missing file, malformed JSON, schema violations, or missing canonical keys,
    returns {} and logs a warning/error. No fallback values are synthesized.
    """
    file_path = Path(path)
    if not file_path.exists():
        logger.warning("Ring baseline library missing: %s", file_path)
        return {}

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:  # intentionally broad: unavailable state only
        logger.error("Failed to parse ring baseline library %s: %s", file_path, exc)
        return {}

    if not isinstance(payload, dict):
        logger.error("Invalid ring baseline library root type at %s", file_path)
        return {}

    baselines = payload.get("baselines")
    if not isinstance(baselines, dict):
        logger.error("Missing/invalid 'baselines' object in %s", file_path)
        return {}

    result: dict[str, RingBaselineEntry] = {}
    for key in CANONICAL_RING_KEYS:
        row = baselines.get(key)
        if not isinstance(row, dict):
            logger.error("Missing baseline row for canonical ring key '%s' in %s", key, file_path)
            return {}

        try:
            ring_type = normalize_ring_type_key(row.get("ring_type") or key)
            baseline_median = float(row["baseline_median"])
            method = str(row["method"])
            source_date = str(row["source_date"])
            provenance_notes = str(row["provenance_notes"])
        except Exception as exc:
            logger.error("Malformed baseline row for '%s' in %s: %s", key, file_path, exc)
            return {}

        if ring_type != key or baseline_median <= 0 or not method or not source_date or not provenance_notes:
            logger.error("Schema validation failed for ring key '%s' in %s", key, file_path)
            return {}

        result[key] = RingBaselineEntry(
            ring_type=ring_type,
            baseline_median=baseline_median,
            method=method,
            source_date=source_date,
            provenance_notes=provenance_notes,
        )

    return result


def default_galactic_baseline_library_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "baselines" / "galactic_ring_baselines.json"


def load_galactic_ring_baselines() -> dict[str, RingBaselineEntry]:
    """Load default galactic baseline library. Returns {} when unavailable."""
    return load_ring_baseline_library(default_galactic_baseline_library_path())
