"""Ring analysis and ranking for exploration tactical guidance.

Adapted from scripts/ring_ranker.py to provide real-time ring evaluation
during exploration using sector-specific baselines.

Responsibilities:
- Load historical ring data from sector databases to establish baseline densities
- Score rings from journal Scan events using sigma-normalization
- Provide SCAN/SKIP recommendations based on ring quality

Methodology:
- Surface density = mass / area (where area = π(R_outer² - R_inner²))
- Sigma normalization = density / sector_median_density
- High sigma_norm (>1.0) = denser than typical = better mining target
- Pristine reserves + high sigma_norm = SCAN recommendation
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.ring_baseline_library import (
    RingBaselineEntry,
    load_galactic_ring_baselines,
    load_ring_baseline_library,
    normalize_ring_type_key,
)

logger = logging.getLogger(__name__)

# Ring class constants (from Elite Dangerous journal)
RING_CLASS_ICY = "eRingClass_Icy"
RING_CLASS_METALLIC = "eRingClass_Metalic"  # Note: Typo in game data
RING_CLASS_METALLIC_ALT = "eRingClass_Metallic"
RING_CLASS_METAL_RICH = "eRingClass_MetalRich"
RING_CLASS_ROCKY = "eRingClass_Rocky"

# Reserve level constants
PRISTINE = "PristineResources"
MAJOR = "MajorResources"
COMMON = "CommonResources"

# Scan worthiness thresholds
SIGMA_THRESHOLD_HIGH = 1.5  # Top ~20% of rings
SIGMA_THRESHOLD_MEDIUM = 1.10  # >10% above median (good use of scan time)

# Sentinel value indicating baseline loaded but no data for this ring class
SIGMA_NO_CLASS_DATA: Optional[float] = None


def _sanitize_sector_name(value: str) -> str:
    trimmed = (value or "").strip()
    replaced = "_".join(part for part in trimmed.split() if part)
    safe = "".join(ch for ch in replaced if ch.isalnum() or ch in {"_", "-"})
    return (safe or "sector").lower()


def _canonical_key_from_ring_class(ring_class: str) -> Optional[str]:
    return normalize_ring_type_key(ring_class)


def _default_sector_baseline_path_from_db(sector_db_path: Optional[str]) -> Optional[Path]:
    if not sector_db_path:
        return None
    db_path = Path(sector_db_path)
    stem = db_path.stem
    if not stem.startswith("sector_"):
        return None
    sector_slug = stem[len("sector_"):]
    if not sector_slug:
        return None
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "data" / "baselines" / "sectors" / f"{_sanitize_sector_name(sector_slug)}.json"


@dataclass
class RingData:
    """Parsed ring data from journal Scan event or sector database."""
    name: str
    ring_class: str
    reserve_level: Optional[str] = None
    mass_mt: float = 0.0
    inner_rad_m: float = 0.0
    outer_rad_m: float = 0.0
    # Derived fields
    surface_area_m2: float = 0.0
    surface_density: float = 0.0


@dataclass
class RingRecommendation:
    """Ring scan recommendation with supporting metrics."""
    should_scan: bool
    reason: str
    sigma_norm: Optional[float] = 0.0  # None = no baseline data for this class
    sector_sigma: Optional[float] = None
    galactic_sigma: Optional[float] = None
    sector_baseline_median: Optional[float] = None
    galactic_baseline_median: Optional[float] = None
    percentile: Optional[int] = None
    priority_score: float = 0.0
    no_class_data: bool = False  # True when baseline loaded but no data for this class


class SectorRingBaseline:
    """Sector-specific baseline for ring density normalization.

    Loads historical ring data from a sector database and calculates
    median densities by ring class to enable sigma-normalization.
    """

    def __init__(self, sector_db_path: Optional[str] = None):
        """Initialize baseline (empty until load is called).

        Args:
            sector_db_path: Optional path to sector database. If None,
                           baseline will be empty (no normalization).
        """
        self.sector_db_path = sector_db_path
        self.median_density_icy: float = 0.0
        self.median_density_metallic: float = 0.0
        self.median_density_metal_rich: float = 0.0
        self.median_density_rocky: float = 0.0
        self.ring_count: int = 0
        self.is_loaded: bool = False

    def load(self) -> bool:
        """Load ring data from sector database and compute medians.

        Returns:
            True if baseline loaded successfully, False otherwise.
        """
        if not self.sector_db_path or not Path(self.sector_db_path).exists():
            logger.warning(f"Sector database not found: {self.sector_db_path}")
            return False

        try:
            with closing(sqlite3.connect(self.sector_db_path)) as conn:
                cursor = conn.cursor()
                # Query all rings from sector database
                cursor.execute("SELECT raw_json FROM rings")
                rows = cursor.fetchall()

            if not rows:
                logger.warning(f"No rings found in sector database: {self.sector_db_path}")
                return False

            # Parse ring data and calculate densities
            icy_densities = []
            metallic_densities = []
            metal_rich_densities = []
            rocky_densities = []

            for (raw_json,) in rows:
                try:
                    ring_dict = json.loads(raw_json)
                    ring = self._parse_ring_from_edsm(ring_dict)

                    if ring.surface_density <= 0:
                        continue

                    # Categorize by ring type (EDSM uses different naming)
                    ring_type = ring_dict.get("type", "").lower()
                    if "icy" in ring_type:
                        icy_densities.append(ring.surface_density)
                    elif "rocky" in ring_type:
                        rocky_densities.append(ring.surface_density)
                    elif "metallic" in ring_type:
                        metallic_densities.append(ring.surface_density)
                    elif "metal" in ring_type or "rich" in ring_type:
                        metal_rich_densities.append(ring.surface_density)

                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.debug(f"Skipping invalid ring data: {e}")
                    continue

            # Calculate medians
            self.median_density_icy = self._calculate_median(icy_densities)
            self.median_density_metallic = self._calculate_median(metallic_densities)
            self.median_density_metal_rich = self._calculate_median(metal_rich_densities)
            self.median_density_rocky = self._calculate_median(rocky_densities)
            self.ring_count = len(rows)
            self.is_loaded = True

            logger.info(
                f"Loaded sector baseline: {self.ring_count} rings, "
                f"median densities: icy={self.median_density_icy:.2e}, "
                f"metallic={self.median_density_metallic:.2e}, "
                f"metal_rich={self.median_density_metal_rich:.2e}, "
                f"rocky={self.median_density_rocky:.2e}"
            )
            return True

        except sqlite3.Error as e:
            logger.error(f"Failed to load sector baseline: {e}")
            return False

    def _parse_ring_from_edsm(self, ring_dict: dict) -> RingData:
        """Parse ring data from EDSM format (raw_json in sector DB).

        EDSM format uses string numbers and different field names.
        """
        mass_str = ring_dict.get("mass", "0")
        inner_str = ring_dict.get("innerRadius", "0")
        outer_str = ring_dict.get("outerRadius", "0")

        mass_mt = float(mass_str) if mass_str else 0.0
        inner_rad_m = float(inner_str) if inner_str else 0.0
        outer_rad_m = float(outer_str) if outer_str else 0.0

        surface_area = _calculate_ring_surface_area(inner_rad_m, outer_rad_m)
        surface_density = _calculate_ring_density(mass_mt, surface_area)

        return RingData(
            name=ring_dict.get("name", "Unknown Ring"),
            ring_class=ring_dict.get("type", ""),
            mass_mt=mass_mt,
            inner_rad_m=inner_rad_m,
            outer_rad_m=outer_rad_m,
            surface_area_m2=surface_area,
            surface_density=surface_density,
        )

    def _calculate_median(self, values: List[float]) -> float:
        """Calculate median of a list of values."""
        if not values:
            return 0.0
        sorted_values = sorted(values)
        n = len(sorted_values)
        mid = n // 2
        if n % 2 == 1:
            return sorted_values[mid]
        else:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0

    def get_median_for_class(self, ring_class: str) -> float:
        """Get median density for a given ring class.

        Args:
            ring_class: Journal ring class (eRingClass_Icy, etc.)

        Returns:
            Median density for that class, or 0.0 if unknown.
        """
        if ring_class == RING_CLASS_ICY:
            return self.median_density_icy
        elif ring_class in {RING_CLASS_METALLIC, RING_CLASS_METALLIC_ALT}:
            return self.median_density_metallic
        elif ring_class == RING_CLASS_METAL_RICH:
            return self.median_density_metal_rich
        elif ring_class == RING_CLASS_ROCKY:
            return self.median_density_rocky
        else:
            return 0.0


class RingScorer:
    """Scores rings from journal Scan events using sector and galactic baselines."""

    def __init__(self, baseline: Optional[SectorRingBaseline] = None):
        """Initialize ring scorer.

        Args:
            baseline: Optional sector baseline for normalization.
                     If None, scoring uses simplified rules without sigma_norm.
        """
        self.baseline = baseline
        self._galactic_baselines: dict[str, RingBaselineEntry] = load_galactic_ring_baselines()
        self._sector_baselines: dict[str, RingBaselineEntry] = self._load_sector_baselines_once()

    def _load_sector_baselines_once(self) -> dict[str, RingBaselineEntry]:
        sector_db_path = getattr(self.baseline, "sector_db_path", None)
        baseline_path = _default_sector_baseline_path_from_db(sector_db_path)
        if not baseline_path:
            return {}
        return load_ring_baseline_library(baseline_path)

    def _lookup_baseline_median(
        self,
        baselines: dict[str, RingBaselineEntry],
        ring_class: str,
    ) -> Optional[float]:
        canonical = _canonical_key_from_ring_class(ring_class)
        if not canonical:
            return None
        entry = baselines.get(canonical)
        return float(entry.baseline_median) if entry else None

    def score_ring(self, ring_dict: dict) -> Tuple[RingData, RingRecommendation]:
        """Score a ring from a journal Scan event.

        Args:
            ring_dict: Ring dictionary from journal Scan event "Rings" array.
                      Expected fields: Name, RingClass, MassMT, InnerRad, OuterRad
                      Optional: ReserveLevel (from parent body)

        Returns:
            Tuple of (RingData, RingRecommendation)
        """
        # Parse ring data from journal format
        ring = self._parse_ring_from_journal(ring_dict)

        # Calculate legacy sector sigma normalization used by recommendation logic.
        sigma_norm: Optional[float] = 0.0
        no_class_data = False
        baseline_available = bool(self.baseline and self.baseline.is_loaded)

        if baseline_available:
            median_density = self.baseline.get_median_for_class(ring.ring_class)
            if median_density > 0 and ring.surface_density > 0:
                sigma_norm = ring.surface_density / median_density
            elif median_density == 0:
                # Baseline loaded but no data for this ring class
                sigma_norm = SIGMA_NO_CLASS_DATA
                no_class_data = True

        sector_baseline_median = self._lookup_baseline_median(self._sector_baselines, ring.ring_class)
        galactic_baseline_median = self._lookup_baseline_median(self._galactic_baselines, ring.ring_class)
        sector_sigma = (
            ring.surface_density / sector_baseline_median
            if sector_baseline_median and ring.surface_density > 0
            else None
        )
        galactic_sigma = (
            ring.surface_density / galactic_baseline_median
            if galactic_baseline_median and ring.surface_density > 0
            else None
        )

        # Generate recommendation
        recommendation = self._evaluate_ring(
            ring,
            sigma_norm,
            baseline_available,
            no_class_data,
            sector_sigma=sector_sigma,
            galactic_sigma=galactic_sigma,
            sector_baseline_median=sector_baseline_median,
            galactic_baseline_median=galactic_baseline_median,
        )

        return ring, recommendation

    def _parse_ring_from_journal(self, ring_dict: dict) -> RingData:
        """Parse ring data from journal Scan event or backfilled EDSM/Spansh format.

        Journal fields:  MassMT, InnerRad, OuterRad
        Backfill fields: mass, innerRadius, outerRadius
        Both formats are accepted; journal names take precedence.
        """
        mass_mt = float(ring_dict.get("MassMT") or ring_dict.get("mass") or 0)
        inner_rad_m = float(ring_dict.get("InnerRad") or ring_dict.get("innerRadius") or 0)
        outer_rad_m = float(ring_dict.get("OuterRad") or ring_dict.get("outerRadius") or 0)

        surface_area = _calculate_ring_surface_area(inner_rad_m, outer_rad_m)
        surface_density = _calculate_ring_density(mass_mt, surface_area)

        return RingData(
            name=ring_dict.get("Name", "Unknown Ring"),
            ring_class=ring_dict.get("RingClass", ""),
            reserve_level=ring_dict.get("ReserveLevel"),  # May be None
            mass_mt=mass_mt,
            inner_rad_m=inner_rad_m,
            outer_rad_m=outer_rad_m,
            surface_area_m2=surface_area,
            surface_density=surface_density,
        )

    def _evaluate_ring(
        self,
        ring: RingData,
        sigma_norm: Optional[float],
        baseline_available: bool,
        no_class_data: bool = False,
        *,
        sector_sigma: Optional[float],
        galactic_sigma: Optional[float],
        sector_baseline_median: Optional[float],
        galactic_baseline_median: Optional[float],
    ) -> RingRecommendation:
        """Evaluate whether a ring should be scanned.

        Recommendation logic:
        1. Non-pristine reserves → SKIP (unless exceptionally dense)
        2. Rocky rings → SKIP (low value)
        3. Pristine + (Icy OR Metallic/MetalRich) + high sigma → SCAN
        4. Unknown reserve level but high sigma → SCAN (optimistic)
        5. No baseline data for class → treat as "no baseline" (don't claim "below median")
        """
        ring_class = ring.ring_class
        reserve = ring.reserve_level or "Unknown"
        common_kwargs = {
            "sector_sigma": sector_sigma,
            "galactic_sigma": galactic_sigma,
            "sector_baseline_median": sector_baseline_median,
            "galactic_baseline_median": galactic_baseline_median,
        }

        # Rocky rings are low value but still display sigma_norm
        if ring_class == RING_CLASS_ROCKY:
            return RingRecommendation(
                should_scan=False,
                reason=f"Rocky (low value)",
                sigma_norm=sigma_norm,
                no_class_data=no_class_data,
                **common_kwargs,
            )

        # Check for valuable ring classes
        is_icy = ring_class == RING_CLASS_ICY
        is_metallic = ring_class in {RING_CLASS_METALLIC, RING_CLASS_METALLIC_ALT, RING_CLASS_METAL_RICH}
        is_valuable_class = is_icy or is_metallic

        if not is_valuable_class:
            return RingRecommendation(
                should_scan=False,
                reason=f"Unknown ring class: {ring_class}",
                sigma_norm=sigma_norm,
                no_class_data=no_class_data,
                **common_kwargs,
            )

        # Pristine reserves are priority targets
        is_pristine = reserve == PRISTINE
        ring_type = "Icy" if is_icy else "Metallic"

        # Handle case where baseline is loaded but has no data for this ring class
        if is_pristine and no_class_data:
            return RingRecommendation(
                should_scan=True,
                reason=f"Pristine {ring_type} (no class baseline data)",
                sigma_norm=sigma_norm,
                priority_score=1.0,
                no_class_data=True,
                **common_kwargs,
            )

        if is_pristine and not baseline_available:
            return RingRecommendation(
                should_scan=True,
                reason=f"Pristine {ring_type} (no baseline)",
                sigma_norm=sigma_norm,
                priority_score=1.0,
                **common_kwargs,
            )

        # For non-pristine with no class data, we can't make density-based recommendations
        if no_class_data:
            return RingRecommendation(
                should_scan=False,
                reason=f"{reserve} {ring_type} (no class baseline data)",
                sigma_norm=sigma_norm,
                no_class_data=True,
                **common_kwargs,
            )

        # Determine scan worthiness based on reserve level and density
        # sigma_norm is guaranteed to be a float here (not None)
        sigma = sigma_norm or 0.0

        if is_pristine and sigma >= SIGMA_THRESHOLD_HIGH:
            percentile = self._estimate_percentile(sigma)
            return RingRecommendation(
                should_scan=True,
                reason=f"Pristine {ring_type}, σ={sigma:.2f} (top {100-percentile}%)",
                sigma_norm=sigma,
                percentile=percentile,
                priority_score=sigma * 2.0,  # High priority
                **common_kwargs,
            )
        elif is_pristine and sigma >= SIGMA_THRESHOLD_MEDIUM:
            return RingRecommendation(
                should_scan=True,
                reason=f"Pristine {ring_type}, above median density",
                sigma_norm=sigma,
                priority_score=sigma,
                **common_kwargs,
            )
        elif is_pristine:
            # Pristine but below median density - not worth scanning
            return RingRecommendation(
                should_scan=False,
                reason=f"Pristine {ring_type} (density below median)",
                sigma_norm=sigma,
                priority_score=0.0,
                **common_kwargs,
            )
        elif sigma >= SIGMA_THRESHOLD_HIGH * 1.5:  # Exceptionally dense
            return RingRecommendation(
                should_scan=True,
                reason=f"Exceptionally dense ({reserve}), σ={sigma:.2f}",
                sigma_norm=sigma,
                priority_score=sigma * 1.5,
                **common_kwargs,
            )
        elif reserve in {MAJOR, COMMON}:
            return RingRecommendation(
                should_scan=False,
                reason=f"{reserve}, σ={sigma:.2f} (low priority)",
                sigma_norm=sigma,
                **common_kwargs,
            )
        else:
            # Unknown reserve level - use density as primary signal
            if sigma >= SIGMA_THRESHOLD_HIGH:
                return RingRecommendation(
                    should_scan=True,
                    reason=f"Unknown reserves, high density (σ={sigma:.2f})",
                    sigma_norm=sigma,
                    priority_score=sigma,
                    **common_kwargs,
                )
            else:
                return RingRecommendation(
                    should_scan=False,
                    reason=f"Unknown reserves, low density",
                    sigma_norm=sigma,
                    **common_kwargs,
                )

    def _estimate_percentile(self, sigma_norm: float) -> int:
        """Estimate percentile rank from sigma_norm.

        Rough approximation assuming normal distribution.
        """
        if sigma_norm >= 3.0:
            return 99
        elif sigma_norm >= 2.5:
            return 95
        elif sigma_norm >= 2.0:
            return 90
        elif sigma_norm >= 1.5:
            return 80
        elif sigma_norm >= 1.0:
            return 50
        else:
            return int(sigma_norm * 50)


# Helper functions (from ring_ranker.py)

def _calculate_ring_surface_area(inner_rad_m: float, outer_rad_m: float) -> float:
    """Calculate ring surface area: π(R_outer² - R_inner²)."""
    if inner_rad_m <= 0 or outer_rad_m <= 0 or outer_rad_m <= inner_rad_m:
        return 0.0
    return math.pi * (outer_rad_m ** 2 - inner_rad_m ** 2)


def _calculate_ring_density(mass_mt: float, area_m2: float) -> float:
    """Calculate surface density: mass / area."""
    if mass_mt <= 0 or area_m2 <= 0:
        return 0.0
    return mass_mt / area_m2
