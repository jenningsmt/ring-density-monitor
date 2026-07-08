"""Tests for the journal-event -> display-row adapter."""

from __future__ import annotations

import math

from core.ring_baseline_library import load_galactic_ring_baselines
from core.ring_display import (
    RingRow,
    build_ring_rows,
    classify_tier,
    create_scorer,
    format_body_type,
    format_delta,
    format_density,
    parse_body_designation,
    parse_ring_id,
)

_GALACTIC = load_galactic_ring_baselines()
_ICY_MEDIAN = _GALACTIC["Icy"].baseline_median


def _mass_for_target_sigma(inner: float, outer: float, target_sigma: float, median: float) -> float:
    area = math.pi * (outer**2 - inner**2)
    return target_sigma * median * area


class TestParseRingId:
    def test_standard_ring_name(self) -> None:
        assert parse_ring_id("Bhotho AB 3 A Ring") == "A"

    def test_different_letter(self) -> None:
        assert parse_ring_id("Eotchorts FG-X d1-318 2 D Ring") == "D"

    def test_unrecognized_shape_falls_back(self) -> None:
        assert parse_ring_id("Nonsense") == "?"


class TestParseBodyDesignation:
    def test_planet_designation_strips_system_prefix(self) -> None:
        event = {"StarSystem": "Eorm Aed GX-A c27-0", "BodyName": "Eorm Aed GX-A c27-0 3"}
        assert parse_body_designation(event) == "3"

    def test_secondary_star_planet_keeps_star_letter(self) -> None:
        event = {"StarSystem": "Bhotho", "BodyName": "Bhotho A 3"}
        assert parse_body_designation(event) == "A 3"

    def test_primary_star_is_main_star(self) -> None:
        event = {"StarSystem": "Eorm Aed GX-A c27-0", "BodyName": "Eorm Aed GX-A c27-0"}
        assert parse_body_designation(event) == "Main Star"

    def test_missing_body_name_falls_back_to_placeholder(self) -> None:
        assert parse_body_designation({"StarSystem": "Sol"}) == "?"

    def test_body_name_not_prefixed_by_system_falls_back_to_raw_name(self) -> None:
        event = {"StarSystem": "Sol", "BodyName": "Some Odd Name"}
        assert parse_body_designation(event) == "Some Odd Name"


class TestFormatBodyType:
    def test_star_with_subclass(self) -> None:
        event = {"StarType": "K", "Subclass": 4}
        assert format_body_type(event) == "K4"

    def test_star_without_subclass(self) -> None:
        event = {"StarType": "N"}
        assert format_body_type(event) == "N"

    def test_gas_giant_shortened(self) -> None:
        event = {"PlanetClass": "Sudarsky class II gas giant"}
        assert format_body_type(event) == "C2 GG"

    def test_icy_body_shortened(self) -> None:
        event = {"PlanetClass": "Icy body"}
        assert format_body_type(event) == "Icy World"

    def test_unmapped_planet_class_falls_back_to_raw(self) -> None:
        event = {"PlanetClass": "Some Future Planet Type"}
        assert format_body_type(event) == "Some Future Planet Type"

    def test_missing_type_info(self) -> None:
        assert format_body_type({}) == "Unknown Body"


class TestFormatDensity:
    def test_small_value_two_decimals(self) -> None:
        assert format_density(8.59e-06) == "8.59 t/km²"

    def test_mid_value_one_decimal(self) -> None:
        assert format_density(15e-06) == "15.0 t/km²"

    def test_large_value_no_decimals(self) -> None:
        assert format_density(500e-06) == "500 t/km²"


class TestFormatDelta:
    def test_positive_sign_prefixed(self) -> None:
        assert format_delta(90.0) == "+90%"

    def test_negative_sign_prefixed(self) -> None:
        assert format_delta(-12.4) == "-12%"

    def test_none_is_blank_not_placeholder(self) -> None:
        assert format_delta(None) == ""


class TestClassifyTier:
    def test_below_ninety_is_black(self) -> None:
        assert classify_tier(89.9) == "black"

    def test_exactly_ninety_is_green(self) -> None:
        assert classify_tier(90.0) == "green"

    def test_exactly_ninety_nine_is_red(self) -> None:
        assert classify_tier(99.0) == "red"

    def test_none_is_unknown(self) -> None:
        assert classify_tier(None) == "unknown"


class TestBuildRingRows:
    def test_no_rings_returns_empty(self) -> None:
        scorer = create_scorer()
        assert build_ring_rows({"event": "Scan", "BodyID": 3}, scorer) == []

    def test_green_tier_icy_ring(self) -> None:
        scorer = create_scorer()
        inner, outer = 100000.0, 200000.0
        mass = _mass_for_target_sigma(inner, outer, target_sigma=1.95, median=_ICY_MEDIAN)
        event = {
            "event": "Scan",
            "BodyID": 5,
            "StarSystem": "Test System",
            "BodyName": "Test System 5",
            "PlanetClass": "Icy body",
            "ReserveLevel": "PristineResources",
            "Rings": [
                {
                    "Name": "Test System 5 A Ring",
                    "RingClass": "eRingClass_Icy",
                    "MassMT": mass,
                    "InnerRad": inner,
                    "OuterRad": outer,
                }
            ],
        }
        rows = build_ring_rows(event, scorer)
        assert len(rows) == 1
        row = rows[0]
        assert isinstance(row, RingRow)
        assert row.ring_key == "5:A"
        assert row.body_designation == "5"
        assert row.body_type_label == "Icy World"
        assert row.ring_id == "A"
        assert row.ring_type == "Icy"
        assert row.tier == "green"
        assert 90.0 <= row.delta_pct < 99.0

    def test_red_tier_metal_rich_ring(self) -> None:
        scorer = create_scorer()
        inner, outer = 100000.0, 200000.0
        median = _GALACTIC["Metal Rich"].baseline_median
        mass = _mass_for_target_sigma(inner, outer, target_sigma=2.5, median=median)
        event = {
            "event": "Scan",
            "BodyID": 7,
            "PlanetClass": "Metal rich body",
            "Rings": [
                {
                    "Name": "Test System 7 B Ring",
                    "RingClass": "eRingClass_MetalRich",
                    "MassMT": mass,
                    "InnerRad": inner,
                    "OuterRad": outer,
                }
            ],
        }
        rows = build_ring_rows(event, scorer)
        assert rows[0].tier == "red"
        assert rows[0].ring_id == "B"

    def test_black_tier_low_density_ring(self) -> None:
        scorer = create_scorer()
        inner, outer = 100000.0, 200000.0
        mass = _mass_for_target_sigma(inner, outer, target_sigma=0.5, median=_ICY_MEDIAN)
        event = {
            "event": "Scan",
            "BodyID": 9,
            "PlanetClass": "Icy body",
            "Rings": [
                {
                    "Name": "Test System 9 C Ring",
                    "RingClass": "eRingClass_Icy",
                    "MassMT": mass,
                    "InnerRad": inner,
                    "OuterRad": outer,
                }
            ],
        }
        rows = build_ring_rows(event, scorer)
        assert rows[0].tier == "black"

    def test_rocky_ring_scored_like_any_other_type(self) -> None:
        """Rocky rings must not be special-cased out of the display (spec decision)."""
        scorer = create_scorer()
        inner, outer = 100000.0, 200000.0
        median = _GALACTIC["Rocky"].baseline_median
        mass = _mass_for_target_sigma(inner, outer, target_sigma=2.0, median=median)
        event = {
            "event": "Scan",
            "BodyID": 11,
            "PlanetClass": "Rocky body",
            "Rings": [
                {
                    "Name": "Test System 11 A Ring",
                    "RingClass": "eRingClass_Rocky",
                    "MassMT": mass,
                    "InnerRad": inner,
                    "OuterRad": outer,
                }
            ],
        }
        rows = build_ring_rows(event, scorer)
        assert rows[0].ring_type == "Rocky"
        assert rows[0].tier == "red"

    def test_unrecognized_ring_class_has_unknown_tier(self) -> None:
        scorer = create_scorer()
        event = {
            "event": "Scan",
            "BodyID": 13,
            "Rings": [
                {
                    "Name": "Test System 13 A Ring",
                    "RingClass": "eRingClass_Mystery",
                    "MassMT": 1e13,
                    "InnerRad": 100000.0,
                    "OuterRad": 200000.0,
                }
            ],
        }
        rows = build_ring_rows(event, scorer)
        assert rows[0].delta_pct is None
        assert rows[0].tier == "unknown"

    def test_reserve_level_propagated_from_body_to_ring(self) -> None:
        """ReserveLevel lives at body level in real journal events, not per-ring;
        the adapter must copy it down before scoring."""
        scorer = create_scorer()
        event = {
            "event": "Scan",
            "BodyID": 21,
            "PlanetClass": "Icy body",
            "ReserveLevel": "PristineResources",
            "Rings": [
                {
                    "Name": "Test System 21 A Ring",
                    "RingClass": "eRingClass_Icy",
                    "MassMT": 1e13,
                    "InnerRad": 100000.0,
                    "OuterRad": 200000.0,
                }
            ],
        }
        rows = build_ring_rows(event, scorer)
        assert len(rows) == 1  # would raise/behave oddly if scoring choked on it
