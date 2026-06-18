from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.ring_baseline_library import (
    CANONICAL_RING_KEYS,
    load_ring_baseline_library,
    normalize_ring_type_key,
)


def test_normalize_ring_type_key_maps_journal_and_plain_forms() -> None:
    assert normalize_ring_type_key("eRingClass_Icy") == "Icy"
    assert normalize_ring_type_key("Icy") == "Icy"

    assert normalize_ring_type_key("eRingClass_Metalic") == "Metallic"
    assert normalize_ring_type_key("eRingClass_Metallic") == "Metallic"
    assert normalize_ring_type_key("metallic") == "Metallic"

    assert normalize_ring_type_key("eRingClass_MetalRich") == "Metal Rich"
    assert normalize_ring_type_key("Metal Rich") == "Metal Rich"
    assert normalize_ring_type_key("metal_rich") == "Metal Rich"

    assert normalize_ring_type_key("eRingClass_Rocky") == "Rocky"
    assert normalize_ring_type_key("Rocky") == "Rocky"

    assert normalize_ring_type_key("UnknownRingClass") is None


def test_load_ring_baseline_library_success_all_four_ring_types() -> None:
    path = Path("data/baselines/galactic_ring_baselines.json")
    result = load_ring_baseline_library(path)

    assert set(result.keys()) == set(CANONICAL_RING_KEYS)
    for key in CANONICAL_RING_KEYS:
        assert result[key].ring_type == key
        assert result[key].baseline_median > 0
        assert result[key].method == "median surface density"
        assert result[key].source_date == "2026-02-12"
        assert "rings_master_2026-02-13.sqlite" in result[key].provenance_notes


def test_load_ring_baseline_library_missing_file_returns_empty() -> None:
    result = load_ring_baseline_library("/tmp/does-not-exist-galactic-ring-baselines.json")
    assert result == {}


def test_load_ring_baseline_library_malformed_file_returns_empty(tmp_path: Path) -> None:
    malformed = tmp_path / "bad.json"
    malformed.write_text("{not json", encoding="utf-8")

    result = load_ring_baseline_library(malformed)
    assert result == {}


def test_load_ring_baseline_library_precision_values() -> None:
    result = load_ring_baseline_library("data/baselines/galactic_ring_baselines.json")

    assert result["Icy"].baseline_median == pytest.approx(8.591067943500732920e-06, rel=0.0, abs=1e-21)
    assert result["Metallic"].baseline_median == pytest.approx(9.251325540340475000e-06, rel=0.0, abs=1e-21)
    assert result["Metal Rich"].baseline_median == pytest.approx(8.998798817181079103e-06, rel=0.0, abs=1e-21)
    assert result["Rocky"].baseline_median == pytest.approx(8.975781190398602962e-06, rel=0.0, abs=1e-21)


def test_load_ring_baseline_library_missing_ring_key_returns_empty(tmp_path: Path) -> None:
    payload = {
        "library_name": "galactic_ring_baselines",
        "version": 1,
        "baselines": {
            "Icy": {
                "ring_type": "Icy",
                "baseline_median": 1.0,
                "method": "median surface density",
                "source_date": "2026-02-12",
                "provenance_notes": "test",
            }
        },
    }
    p = tmp_path / "incomplete.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    result = load_ring_baseline_library(p)
    assert result == {}
