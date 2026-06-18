#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test ring analysis with real Eotchorts sector data."""

import sys
from pathlib import Path

# Force UTF-8 encoding for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from core.ring_analysis import RingScorer, SectorRingBaseline, RING_CLASS_ICY, RING_CLASS_METALLIC, PRISTINE

def main():
    print("=" * 60)
    print("Ring Analysis Integration Test - Eotchorts Sector")
    print("=" * 60)

    # Load Eotchorts sector baseline
    repo_root = Path(__file__).resolve().parents[1]
    sector_db_path = str(repo_root / "data" / "sector_library" / "sector_eotchorts.sqlite")
    print(f"\nLoading baseline from: {sector_db_path}")

    baseline = SectorRingBaseline(sector_db_path)
    if not baseline.load():
        print("ERROR: Failed to load baseline!")
        return 1

    print(f"[OK] Baseline loaded successfully!")
    print(f"  - Total rings: {baseline.ring_count}")
    print(f"  - Median density (Icy): {baseline.median_density_icy:.2e}")
    print(f"  - Median density (Metallic): {baseline.median_density_metallic:.2e}")
    print(f"  - Median density (Metal Rich): {baseline.median_density_metal_rich:.2e}")

    # Create scorer
    scorer = RingScorer(baseline)

    # Test Case 1: High-density pristine icy ring
    print("\n" + "-" * 60)
    print("Test 1: High-density pristine icy ring")
    print("-" * 60)

    test_ring_1 = {
        "Name": "Test System 1 A Ring",
        "RingClass": RING_CLASS_ICY,
        "MassMT": 5e13,  # Very high mass
        "InnerRad": 100000,
        "OuterRad": 500000,
        "ReserveLevel": PRISTINE,
    }

    ring_data, recommendation = scorer.score_ring(test_ring_1)
    print(f"Ring: {ring_data.name}")
    print(f"  Class: {ring_data.ring_class}")
    print(f"  Mass: {ring_data.mass_mt:.2e} MT")
    print(f"  Surface Area: {ring_data.surface_area_m2:.2e} m²")
    print(f"  Surface Density: {ring_data.surface_density:.2e}")
    print(f"  Sigma Norm: {recommendation.sigma_norm:.2f}")
    print(f"  RECOMMENDATION: {'SCAN [+]' if recommendation.should_scan else 'SKIP [-]'}")
    print(f"  Reason: {recommendation.reason}")

    # Test Case 2: Low-density common metallic ring
    print("\n" + "-" * 60)
    print("Test 2: Low-density common metallic ring")
    print("-" * 60)

    test_ring_2 = {
        "Name": "Test System 2 A Ring",
        "RingClass": RING_CLASS_METALLIC,
        "MassMT": 1e12,  # Low mass
        "InnerRad": 200000,
        "OuterRad": 400000,
        "ReserveLevel": "CommonResources",
    }

    ring_data, recommendation = scorer.score_ring(test_ring_2)
    print(f"Ring: {ring_data.name}")
    print(f"  Class: {ring_data.ring_class}")
    print(f"  Mass: {ring_data.mass_mt:.2e} MT")
    print(f"  Sigma Norm: {recommendation.sigma_norm:.2f}")
    print(f"  RECOMMENDATION: {'SCAN [+]' if recommendation.should_scan else 'SKIP [-]'}")
    print(f"  Reason: {recommendation.reason}")

    # Test Case 3: Pristine metallic ring (medium density)
    print("\n" + "-" * 60)
    print("Test 3: Pristine metallic ring (medium density)")
    print("-" * 60)

    test_ring_3 = {
        "Name": "Test System 3 A Ring",
        "RingClass": RING_CLASS_METALLIC,
        "MassMT": 2e13,
        "InnerRad": 150000,
        "OuterRad": 350000,
        "ReserveLevel": PRISTINE,
    }

    ring_data, recommendation = scorer.score_ring(test_ring_3)
    print(f"Ring: {ring_data.name}")
    print(f"  Sigma Norm: {recommendation.sigma_norm:.2f}")
    print(f"  Priority Score: {recommendation.priority_score:.2f}")
    print(f"  RECOMMENDATION: {'SCAN [+]' if recommendation.should_scan else 'SKIP [-]'}")
    print(f"  Reason: {recommendation.reason}")

    print("\n" + "=" * 60)
    print("Integration test completed successfully!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    exit(main())
