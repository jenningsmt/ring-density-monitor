from __future__ import annotations

from unittest.mock import patch

from core.models import BodyContext
from core.ring_analysis import RingRecommendation, RingScorer
from core.ring_baseline_library import RingBaselineEntry
from core.system_state_machine import SystemStateMachine


class _BaselineStub:
    is_loaded = True

    def __init__(self, median: float = 100.0, sector_db_path: str = "data/sector_library/sector_test_sector.sqlite"):
        self._median = median
        self.sector_db_path = sector_db_path

    def get_median_for_class(self, _ring_class: str) -> float:
        return self._median


def _entry(ring_type: str, median: float) -> RingBaselineEntry:
    return RingBaselineEntry(
        ring_type=ring_type,
        baseline_median=median,
        method="median surface density",
        source_date="2026-02-12",
        provenance_notes="test",
    )


def test_ring_scorer_populates_distinct_sector_and_galactic_fields() -> None:
    ring = {
        "Name": "Test A Ring",
        "RingClass": "eRingClass_Icy",
        "MassMT": 1000.0,
        "InnerRad": 1.0,
        "OuterRad": 2.0,
        "ReserveLevel": "PristineResources",
    }

    with (
        patch("core.ring_analysis.load_ring_baseline_library", return_value={"Icy": _entry("Icy", 80.0)}),
        patch("core.ring_analysis.load_galactic_ring_baselines", return_value={"Icy": _entry("Icy", 40.0)}),
    ):
        scorer = RingScorer(_BaselineStub(median=50.0))
        ring_data, recommendation = scorer.score_ring(ring)

    assert ring_data.surface_density > 0
    assert recommendation.sector_baseline_median == 80.0
    assert recommendation.galactic_baseline_median == 40.0
    assert recommendation.sector_sigma is not None
    assert recommendation.galactic_sigma is not None
    assert recommendation.sector_sigma != recommendation.galactic_sigma


def test_missing_sector_baseline_keeps_only_sector_fields_none() -> None:
    ring = {
        "Name": "Test A Ring",
        "RingClass": "eRingClass_Icy",
        "MassMT": 1000.0,
        "InnerRad": 1.0,
        "OuterRad": 2.0,
        "ReserveLevel": "PristineResources",
    }

    with (
        patch("core.ring_analysis.load_ring_baseline_library", return_value={}),
        patch("core.ring_analysis.load_galactic_ring_baselines", return_value={"Icy": _entry("Icy", 40.0)}),
    ):
        scorer = RingScorer(_BaselineStub(median=50.0))
        _, recommendation = scorer.score_ring(ring)

    assert recommendation.sector_baseline_median is None
    assert recommendation.sector_sigma is None
    assert recommendation.galactic_baseline_median == 40.0
    assert recommendation.galactic_sigma is not None


def test_missing_galactic_baseline_keeps_only_galactic_fields_none() -> None:
    ring = {
        "Name": "Test A Ring",
        "RingClass": "eRingClass_Icy",
        "MassMT": 1000.0,
        "InnerRad": 1.0,
        "OuterRad": 2.0,
        "ReserveLevel": "PristineResources",
    }

    with (
        patch("core.ring_analysis.load_ring_baseline_library", return_value={"Icy": _entry("Icy", 80.0)}),
        patch("core.ring_analysis.load_galactic_ring_baselines", return_value={}),
    ):
        scorer = RingScorer(_BaselineStub(median=50.0))
        _, recommendation = scorer.score_ring(ring)

    assert recommendation.galactic_baseline_median is None
    assert recommendation.galactic_sigma is None
    assert recommendation.sector_baseline_median == 80.0
    assert recommendation.sector_sigma is not None


def test_baseline_loaders_are_not_called_per_event() -> None:
    ring = {
        "Name": "Test A Ring",
        "RingClass": "eRingClass_Icy",
        "MassMT": 1000.0,
        "InnerRad": 1.0,
        "OuterRad": 2.0,
        "ReserveLevel": "PristineResources",
    }

    with (
        patch("core.ring_analysis.load_ring_baseline_library", return_value={"Icy": _entry("Icy", 80.0)}) as load_sector,
        patch("core.ring_analysis.load_galactic_ring_baselines", return_value={"Icy": _entry("Icy", 40.0)}) as load_galactic,
    ):
        scorer = RingScorer(_BaselineStub(median=50.0))
        scorer.score_ring(ring)
        scorer.score_ring(ring)
        scorer.score_ring(ring)

    assert load_sector.call_count == 1
    assert load_galactic.call_count == 1


def test_scored_ring_row_contains_independent_upstream_fields() -> None:
    machine = SystemStateMachine()

    ring_reco = RingRecommendation(
        should_scan=True,
        reason="ok",
        sigma_norm=1.5,
        sector_sigma=None,
        galactic_sigma=2.0,
        sector_baseline_median=None,
        galactic_baseline_median=4.0,
    )

    class _ScorerStub:
        def score_ring(self, _ring_dict):
            return type("Ring", (), {"name": "r"})(), ring_reco

    machine._ring_scorer = _ScorerStub()
    body = BodyContext(
        body_name="Test 1",
        rings=[{"Name": "Test 1 A Ring", "RingClass": "eRingClass_Icy"}],
    )

    machine._score_rings_if_available(body, reserve_level="PristineResources")

    scored_ring = body.rings[0]
    assert "_sector_sigma" in scored_ring
    assert "_galactic_sigma" in scored_ring
    assert "sector_baseline_median" in scored_ring
    assert "galactic_baseline_median" in scored_ring
    assert scored_ring["_sector_sigma"] is None
    assert scored_ring["_galactic_sigma"] == 2.0
    assert scored_ring["sector_baseline_median"] is None
    assert scored_ring["galactic_baseline_median"] == 4.0
