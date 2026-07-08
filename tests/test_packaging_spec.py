"""Guards against a regression that shipped in v1.0.0: the PyInstaller spec
failed to bundle data/baselines/galactic_ring_baselines.json, so the frozen
app silently had no galactic baseline data and every ring's delta column
was blank (RingScorer degrades gracefully to "no data" rather than raising,
so this had no exception to catch it -- only a packaging-level check does).
"""

from __future__ import annotations

from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parents[1] / "RingDensityMonitor.spec"


def test_spec_bundles_the_galactic_baseline_file() -> None:
    spec_text = _SPEC_PATH.read_text(encoding="utf-8")
    assert "data/baselines/galactic_ring_baselines.json" in spec_text, (
        "RingDensityMonitor.spec must list the galactic baseline JSON in "
        "datas=[...], or the frozen app can't compute any ring's delta "
        "from the galactic norm (see core/ring_baseline_library.py's "
        "default_galactic_baseline_library_path())."
    )


def test_the_bundled_file_actually_exists_on_disk() -> None:
    source_file = _SPEC_PATH.parent / "data" / "baselines" / "galactic_ring_baselines.json"
    assert source_file.exists(), "The file the spec references must actually exist to be bundled."
