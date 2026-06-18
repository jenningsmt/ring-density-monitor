"""Tests for ring analysis and scoring functionality."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from core.ring_analysis import (
    PRISTINE,
    RING_CLASS_ICY,
    RING_CLASS_METALLIC,
    RING_CLASS_ROCKY,
    RingScorer,
    SectorRingBaseline,
    _calculate_ring_density,
    _calculate_ring_surface_area,
)


class TestRingSurfaceAreaCalculations(unittest.TestCase):
    """Test ring surface area and density calculations."""

    def test_surface_area_calculation(self):
        """Test ring surface area formula: π(R_outer² - R_inner²)."""
        # Simple case: inner=100m, outer=200m
        area = _calculate_ring_surface_area(100.0, 200.0)
        expected = 3.14159 * (200**2 - 100**2)
        self.assertAlmostEqual(area, expected, places=0)  # Adjusted for floating point precision

    def test_surface_area_invalid_radii(self):
        """Test surface area returns 0 for invalid radii."""
        self.assertEqual(_calculate_ring_surface_area(0, 100), 0.0)
        self.assertEqual(_calculate_ring_surface_area(100, 0), 0.0)
        self.assertEqual(_calculate_ring_surface_area(200, 100), 0.0)  # outer < inner

    def test_density_calculation(self):
        """Test surface density = mass / area."""
        density = _calculate_ring_density(1000.0, 500.0)
        self.assertEqual(density, 2.0)

    def test_density_invalid_inputs(self):
        """Test density returns 0 for invalid inputs."""
        self.assertEqual(_calculate_ring_density(0, 100), 0.0)
        self.assertEqual(_calculate_ring_density(100, 0), 0.0)
        self.assertEqual(_calculate_ring_density(-100, 100), 0.0)


class TestSectorRingBaseline(unittest.TestCase):
    """Test sector baseline loading and median calculations."""

    def _create_test_sector_db(self) -> str:
        """Create a temporary sector database with test ring data."""
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test_sector.sqlite"

        with closing(sqlite3.connect(str(db_path))) as conn:
            cursor = conn.cursor()

            # Create rings table matching sector database schema
            cursor.execute(
                """
                CREATE TABLE rings (
                    system_key TEXT,
                    system_address INTEGER,
                    system_name TEXT,
                    body_key TEXT,
                    body_id INTEGER,
                    body_name TEXT,
                    ring_name TEXT,
                    ring_class TEXT,
                    mass_mt REAL,
                    inner_rad REAL,
                    outer_rad REAL,
                    reserve_level TEXT,
                    raw_json TEXT
                )
                """
            )

            # Add test rings with known densities
            test_rings = [
                # Icy rings
                {"name": "Icy Ring 1", "type": "Icy", "mass": "1000000000000", "innerRadius": "100000", "outerRadius": "200000"},
                {"name": "Icy Ring 2", "type": "Icy", "mass": "2000000000000", "innerRadius": "100000", "outerRadius": "200000"},
                {"name": "Icy Ring 3", "type": "Icy", "mass": "3000000000000", "innerRadius": "100000", "outerRadius": "200000"},
                # Metallic rings
                {"name": "Metallic Ring 1", "type": "Metallic", "mass": "5000000000000", "innerRadius": "100000", "outerRadius": "200000"},
                {"name": "Metallic Ring 2", "type": "Metallic", "mass": "6000000000000", "innerRadius": "100000", "outerRadius": "200000"},
            ]

            for ring in test_rings:
                cursor.execute(
                    "INSERT INTO rings (raw_json) VALUES (?)",
                    (json.dumps(ring),),
                )

            conn.commit()

        return str(db_path)

    def test_baseline_load_success(self):
        """Test successful baseline loading from sector database."""
        db_path = self._create_test_sector_db()
        baseline = SectorRingBaseline(db_path)

        self.assertTrue(baseline.load())
        self.assertTrue(baseline.is_loaded)
        self.assertEqual(baseline.ring_count, 5)
        self.assertGreater(baseline.median_density_icy, 0)
        self.assertGreater(baseline.median_density_metallic, 0)

    def test_baseline_load_nonexistent_file(self):
        """Test baseline loading fails gracefully for missing file."""
        baseline = SectorRingBaseline("/nonexistent/path.sqlite")
        self.assertFalse(baseline.load())
        self.assertFalse(baseline.is_loaded)

    def test_median_calculation(self):
        """Test median density calculation."""
        db_path = self._create_test_sector_db()
        baseline = SectorRingBaseline(db_path)
        baseline.load()

        # With 3 icy rings of mass 1e12, 2e12, 3e12 (same area), median should be 2e12/area
        # Area = π(200000² - 100000²) ≈ 9.42e10
        expected_median_icy_approx = 2e12 / (3.14159 * (200000**2 - 100000**2))

        self.assertGreater(baseline.median_density_icy, 0)
        self.assertAlmostEqual(baseline.median_density_icy, expected_median_icy_approx, delta=expected_median_icy_approx * 0.1)


class TestRingScorer(unittest.TestCase):
    """Test ring scoring and recommendation logic."""

    def test_pristine_icy_high_density_scan_recommended(self):
        """Test pristine icy ring with high density gets SCAN recommendation."""
        # Mock baseline with low median density (makes test ring high sigma_norm)
        class MockBaseline:
            is_loaded = True
            def get_median_for_class(self, ring_class):
                # Median density should be around 50 (lower than test ring's ~106)
                return 50.0

        scorer = RingScorer(baseline=MockBaseline())

        ring_dict = {
            "Name": "Test Ring A",
            "RingClass": RING_CLASS_ICY,
            "MassMT": 1e13,
            "InnerRad": 100000,
            "OuterRad": 200000,
            "ReserveLevel": PRISTINE,
        }

        ring_data, recommendation = scorer.score_ring(ring_dict)

        self.assertTrue(recommendation.should_scan)
        self.assertIn("Pristine", recommendation.reason)
        self.assertGreater(recommendation.sigma_norm, 1.5)  # Should be high sigma

    def test_rocky_ring_skip_recommended(self):
        """Test rocky rings always get SKIP recommendation."""
        scorer = RingScorer(baseline=None)

        ring_dict = {
            "Name": "Rocky Ring A",
            "RingClass": RING_CLASS_ROCKY,
            "MassMT": 1e13,
            "InnerRad": 100000,
            "OuterRad": 200000,
            "ReserveLevel": PRISTINE,
        }

        ring_data, recommendation = scorer.score_ring(ring_dict)

        self.assertFalse(recommendation.should_scan)
        self.assertIn("Rocky", recommendation.reason)

    def test_common_reserves_low_density_skip(self):
        """Test common reserves with low density gets SKIP."""
        class MockBaseline:
            is_loaded = True
            def get_median_for_class(self, ring_class):
                # High median density (makes test ring low sigma)
                return 200.0

        scorer = RingScorer(baseline=MockBaseline())

        ring_dict = {
            "Name": "Common Ring",
            "RingClass": RING_CLASS_METALLIC,
            "MassMT": 1e13,
            "InnerRad": 100000,
            "OuterRad": 200000,
            "ReserveLevel": "CommonResources",
        }

        ring_data, recommendation = scorer.score_ring(ring_dict)

        self.assertFalse(recommendation.should_scan)
        self.assertIn("Common", recommendation.reason)

    def test_no_baseline_pristine_scan(self):
        """Test pristine rings get SCAN even without baseline."""
        scorer = RingScorer(baseline=None)

        ring_dict = {
            "Name": "Pristine Ring",
            "RingClass": RING_CLASS_ICY,
            "MassMT": 1e13,
            "InnerRad": 100000,
            "OuterRad": 200000,
            "ReserveLevel": PRISTINE,
        }

        ring_data, recommendation = scorer.score_ring(ring_dict)

        # Should still recommend scanning pristine even without sigma_norm
        self.assertTrue(recommendation.should_scan)
        self.assertIn("Pristine", recommendation.reason)

    def test_ring_data_parsing(self):
        """Test ring data is correctly parsed from journal format."""
        scorer = RingScorer(baseline=None)

        ring_dict = {
            "Name": "Test System 1 A Ring",
            "RingClass": RING_CLASS_METALLIC,
            "MassMT": 5e12,
            "InnerRad": 150000,
            "OuterRad": 300000,
        }

        ring_data, _ = scorer.score_ring(ring_dict)

        self.assertEqual(ring_data.name, "Test System 1 A Ring")
        self.assertEqual(ring_data.ring_class, RING_CLASS_METALLIC)
        self.assertEqual(ring_data.mass_mt, 5e12)
        self.assertGreater(ring_data.surface_area_m2, 0)
        self.assertGreater(ring_data.surface_density, 0)


class TestNoClassBaselineData(unittest.TestCase):
    """Tests for C-6: handling baseline loaded but no data for ring class."""

    def test_pristine_icy_no_class_data_still_scanned(self) -> None:
        """C-6: Pristine ring with no class baseline data must recommend SCAN, not 'below median'.

        When baseline is loaded but has no data for a specific ring class (e.g., icy),
        we should NOT compute sigma_norm=0 and say "below median" - instead, treat
        it like "no baseline" and recommend scanning pristine rings.
        """
        # Mock baseline that has data but NOT for icy rings
        class MockBaselineNoIcy:
            is_loaded = True
            def get_median_for_class(self, ring_class):
                if ring_class == RING_CLASS_ICY:
                    return 0.0  # No icy ring data in baseline
                return 100.0  # Other classes have data

        scorer = RingScorer(baseline=MockBaselineNoIcy())

        ring_dict = {
            "Name": "Pristine Icy Ring",
            "RingClass": RING_CLASS_ICY,
            "MassMT": 1e13,
            "InnerRad": 100000,
            "OuterRad": 200000,
            "ReserveLevel": PRISTINE,
        }

        ring_data, recommendation = scorer.score_ring(ring_dict)

        # Should recommend scanning (not say "below median")
        self.assertTrue(recommendation.should_scan)
        self.assertTrue(recommendation.no_class_data)
        self.assertIn("no class baseline data", recommendation.reason)
        self.assertNotIn("below median", recommendation.reason)

    def test_common_reserves_no_class_data_not_scanned(self) -> None:
        """C-6: Non-pristine ring with no class data should not recommend scan."""
        class MockBaselineNoMetallic:
            is_loaded = True
            def get_median_for_class(self, ring_class):
                if ring_class == RING_CLASS_METALLIC:
                    return 0.0  # No metallic ring data
                return 100.0

        scorer = RingScorer(baseline=MockBaselineNoMetallic())

        ring_dict = {
            "Name": "Common Metallic Ring",
            "RingClass": RING_CLASS_METALLIC,
            "MassMT": 1e13,
            "InnerRad": 100000,
            "OuterRad": 200000,
            "ReserveLevel": "CommonResources",
        }

        ring_data, recommendation = scorer.score_ring(ring_dict)

        # Should NOT recommend scanning non-pristine
        self.assertFalse(recommendation.should_scan)
        self.assertTrue(recommendation.no_class_data)
        self.assertIn("no class baseline data", recommendation.reason)
        # Should NOT claim it's below median
        self.assertNotIn("below median", recommendation.reason)

    def test_no_class_data_sigma_is_none(self) -> None:
        """C-6: When no class data, sigma_norm should be None (sentinel)."""
        class MockBaselineNoIcy:
            is_loaded = True
            def get_median_for_class(self, ring_class):
                return 0.0 if ring_class == RING_CLASS_ICY else 100.0

        scorer = RingScorer(baseline=MockBaselineNoIcy())

        ring_dict = {
            "Name": "Icy Ring",
            "RingClass": RING_CLASS_ICY,
            "MassMT": 1e13,
            "InnerRad": 100000,
            "OuterRad": 200000,
            "ReserveLevel": PRISTINE,
        }

        ring_data, recommendation = scorer.score_ring(ring_dict)

        # sigma_norm should be None (sentinel for no class data)
        self.assertIsNone(recommendation.sigma_norm)
        self.assertTrue(recommendation.no_class_data)


class TestRingAnalysisIntegration(unittest.TestCase):
    """Integration tests for ring analysis workflow."""

    def test_full_ring_scoring_workflow(self):
        """Test complete workflow: load baseline, score ring, get recommendation."""
        # Create test database
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test_sector.sqlite"

        with closing(sqlite3.connect(str(db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE rings (raw_json TEXT)
                """
            )

            # Add several rings to establish baseline
            for i in range(10):
                ring = {
                    "name": f"Ring {i}",
                    "type": "Icy",
                    "mass": str(1e12 * (i + 1)),
                    "innerRadius": "100000",
                    "outerRadius": "200000",
                }
                cursor.execute("INSERT INTO rings (raw_json) VALUES (?)", (json.dumps(ring),))

            conn.commit()

        # Load baseline
        baseline = SectorRingBaseline(str(db_path))
        self.assertTrue(baseline.load())

        # Score a high-density ring
        scorer = RingScorer(baseline=baseline)
        ring_dict = {
            "Name": "High Density Ring",
            "RingClass": RING_CLASS_ICY,
            "MassMT": 20e12,  # Much higher than baseline
            "InnerRad": 100000,
            "OuterRad": 200000,
            "ReserveLevel": PRISTINE,
        }

        ring_data, recommendation = scorer.score_ring(ring_dict)

        # Should recommend scanning due to high density
        self.assertTrue(recommendation.should_scan)
        self.assertGreater(recommendation.sigma_norm, 1.5)
        self.assertGreater(recommendation.priority_score, 0)


if __name__ == "__main__":
    unittest.main()
