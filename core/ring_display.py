"""Adapter: raw journal Scan events -> ring display rows.

Pure and deterministic (no I/O, no clock reads, no Tkinter) so it can be
unit-tested directly. The UI layer is responsible for row lifetime
(first-seen timestamps, black-tier auto-hide, system-departure clearing) —
this module only computes what a ring's row *should say* right now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.ring_analysis import RingScorer
from core.ring_baseline_library import normalize_ring_type_key

# Delta-from-galactic-norm color tiers, per the ring-density-monitor spec.
TIER_BLACK = "black"    # < +90%  — not worth a DSS scan
TIER_GREEN = "green"    # >= +90% and < +99% — should DSS
TIER_RED = "red"        # >= +99% — elite ring, must DSS
TIER_UNKNOWN = "unknown"  # no galactic baseline data for this ring type

_DELTA_GREEN_THRESHOLD = 90.0
_DELTA_RED_THRESHOLD = 99.0

# Display-only scaling: raw surface density is mass-field / area_m2 with an
# unlabeled unit. Scaling by 1e6 turns it into a "per km^2" reading, which
# lines up with the galactic baseline's actual magnitude (~8.6 at the Icy
# median) and reads far better than the raw ~1e-6 scientific notation. This
# is purely cosmetic: sigma/delta math is computed upstream on the raw,
# unscaled value and is unaffected by this choice.
_DENSITY_DISPLAY_SCALE = 1e6
_DENSITY_UNIT_LABEL = "t/km²"

_GAS_GIANT_CLASS_LABELS = {
    "sudarsky class i gas giant": "C1 GG",
    "sudarsky class ii gas giant": "C2 GG",
    "sudarsky class iii gas giant": "C3 GG",
    "sudarsky class iv gas giant": "C4 GG",
    "sudarsky class v gas giant": "C5 GG",
    "helium gas giant": "He GG",
    "helium rich gas giant": "He-Rich GG",
    "gas giant with water based life": "Water-Life GG",
    "gas giant with ammonia based life": "Ammonia-Life GG",
    "water giant": "Water GG",
}

_PLANET_CLASS_LABELS = {
    "metal rich body": "Metal Rich World",
    "high metal content body": "High Metal World",
    "rocky body": "Rocky World",
    "icy body": "Icy World",
    "rocky ice body": "Rocky Ice World",
    "earthlike body": "Earthlike World",
    "water world": "Water World",
    "ammonia world": "Ammonia World",
}


@dataclass(frozen=True)
class RingRow:
    """One ring's worth of display data, independent of UI lifetime state."""

    ring_key: str
    body_designation: str
    body_type_label: str
    ring_id: str
    ring_type: str
    density_label: str
    delta_pct: Optional[float]
    tier: str


def create_scorer() -> RingScorer:
    """Build a RingScorer for standalone use: galactic baseline only, no sector DB."""
    return RingScorer(baseline=None)


def classify_tier(delta_pct: Optional[float]) -> str:
    if delta_pct is None:
        return TIER_UNKNOWN
    if delta_pct >= _DELTA_RED_THRESHOLD:
        return TIER_RED
    if delta_pct >= _DELTA_GREEN_THRESHOLD:
        return TIER_GREEN
    return TIER_BLACK


def format_density(surface_density: float) -> str:
    scaled = surface_density * _DENSITY_DISPLAY_SCALE
    if scaled >= 100:
        return f"{scaled:,.0f} {_DENSITY_UNIT_LABEL}"
    if scaled >= 10:
        return f"{scaled:.1f} {_DENSITY_UNIT_LABEL}"
    return f"{scaled:.2f} {_DENSITY_UNIT_LABEL}"


def format_delta(delta_pct: Optional[float]) -> str:
    """Sign-prefixed, no-decimal percentage. Empty string when unavailable
    (never a placeholder like 'N/A' — an absent value should read as blank)."""
    if delta_pct is None:
        return ""
    return f"{delta_pct:+.0f}%"


def parse_ring_id(ring_name: str) -> str:
    """Extract the ring letter (A/B/C/D) from a journal ring Name field.

    Journal ring names look like "<Body designation> <Ring letter> Ring",
    e.g. "Bhotho AB 3 A Ring" -> "A". Falls back to "?" if the shape is
    unrecognized rather than guessing.
    """
    tokens = ring_name.strip().split()
    if len(tokens) >= 2 and tokens[-1].lower() == "ring":
        candidate = tokens[-2]
        if len(candidate) == 1 and candidate.isalpha():
            return candidate.upper()
    return "?"


def parse_body_designation(scan_event: dict) -> str:
    """Extract the in-game body designation from a Scan event.

    Frontier's internal BodyID (e.g. 27) has no relationship to the number
    a player sees in the system map / DSS / third-party ring tools (e.g.
    "3"). The in-game designation is the part of BodyName left over after
    stripping the StarSystem prefix, e.g. "Eorm Aed GX-A c27-0 3" with
    StarSystem "Eorm Aed GX-A c27-0" -> "3". The system's own primary star
    has BodyName == StarSystem (no suffix), shown as "Main Star".
    """
    star_system = str(scan_event.get("StarSystem") or "").strip()
    body_name = str(scan_event.get("BodyName") or "").strip()
    if not body_name:
        return "?"
    if star_system and body_name == star_system:
        return "Main Star"
    if star_system and body_name.startswith(star_system):
        remainder = body_name[len(star_system):].strip()
        if remainder:
            return remainder
    return body_name


def format_body_type(scan_event: dict) -> str:
    """Format a short body-type label from a Scan event.

    Stars: StarType + Subclass (e.g. "K4"). Planets: shortened PlanetClass
    (e.g. "Sudarsky class II gas giant" -> "C2 GG"). Unknown/unmapped
    classes fall back to the raw journal string rather than "Unknown".
    """
    star_type = scan_event.get("StarType")
    if star_type:
        subclass = scan_event.get("Subclass")
        if isinstance(subclass, int):
            return f"{star_type}{subclass}"
        return str(star_type)

    planet_class = scan_event.get("PlanetClass")
    if not planet_class:
        return "Unknown Body"

    lowered = str(planet_class).strip().lower()
    if lowered in _GAS_GIANT_CLASS_LABELS:
        return _GAS_GIANT_CLASS_LABELS[lowered]
    if lowered in _PLANET_CLASS_LABELS:
        return _PLANET_CLASS_LABELS[lowered]
    return str(planet_class)


def build_ring_rows(scan_event: dict, scorer: RingScorer) -> list[RingRow]:
    """Build one RingRow per ring in a Scan event's "Rings" array.

    Returns an empty list for Scan events with no rings (most bodies).
    """
    rings = scan_event.get("Rings") or []
    if not rings:
        return []

    body_id = scan_event.get("BodyID")
    reserve_level = scan_event.get("ReserveLevel")
    body_type_label = format_body_type(scan_event)
    body_designation = parse_body_designation(scan_event)

    rows: list[RingRow] = []
    for ring in rings:
        ring_dict = dict(ring)
        if reserve_level is not None:
            ring_dict.setdefault("ReserveLevel", reserve_level)

        ring_data, recommendation = scorer.score_ring(ring_dict)

        ring_id = parse_ring_id(ring_data.name)
        ring_type = normalize_ring_type_key(ring_data.ring_class) or ring_data.ring_class or "Unknown"

        delta_pct: Optional[float] = None
        if recommendation.galactic_sigma is not None:
            delta_pct = (recommendation.galactic_sigma - 1.0) * 100.0

        rows.append(
            RingRow(
                ring_key=f"{body_id}:{ring_id}",
                body_designation=body_designation,
                body_type_label=body_type_label,
                ring_id=ring_id,
                ring_type=ring_type,
                density_label=format_density(ring_data.surface_density),
                delta_pct=delta_pct,
                tier=classify_tier(delta_pct),
            )
        )
    return rows
