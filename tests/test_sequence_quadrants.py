import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.sequence_quadrants import write_itinerary


class SequenceQuadrantsTests(unittest.TestCase):
    def _create_fixture(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE quadrant_summaries (
                    score_version TEXT,
                    cohort_name TEXT,
                    quadrant TEXT,
                    n INTEGER,
                    centroid_x REAL,
                    centroid_y REAL,
                    centroid_z REAL,
                    radius_max_ly REAL,
                    moi_max REAL,
                    moi_median REAL,
                    min_ring_id TEXT,
                    PRIMARY KEY(score_version, cohort_name, quadrant)
                );
                CREATE TABLE icy_quadrants (
                    score_version TEXT,
                    cohort_name TEXT,
                    ring_id TEXT,
                    quadrant TEXT,
                    theta_deg REAL,
                    dx REAL,
                    dz REAL,
                    x REAL,
                    y REAL,
                    z REAL,
                    system_name TEXT,
                    body_name TEXT,
                    ring_name TEXT,
                    moi_metric REAL,
                    rank INTEGER,
                    PRIMARY KEY(score_version, cohort_name, ring_id)
                );
                """
            )
            conn.executemany(
                """
                INSERT INTO quadrant_summaries (
                    score_version, cohort_name, quadrant, n, centroid_x, centroid_y, centroid_z,
                    radius_max_ly, moi_max, moi_median, min_ring_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("moi_v1", "IcyCore", "E", 2, 10.0, 0.0, 0.0, 2.0, 50.0, 40.0, "e1"),
                    ("moi_v1", "IcyCore", "N", 1, 10.0, 0.0, 10.0, 1.0, 60.0, 60.0, "n1"),
                    ("moi_v1", "IcyCore", "W", 1, -10.0, 0.0, 0.0, 3.0, 30.0, 30.0, "w1"),
                    ("moi_v1", "IcyCore", "S", 1, 0.0, 0.0, -10.0, 4.0, 20.0, 20.0, "s1"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO icy_quadrants (
                    score_version, cohort_name, ring_id, quadrant, theta_deg, dx, dz,
                    x, y, z, system_name, body_name, ring_name, moi_metric, rank
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("moi_v1", "IcyCore", "e1", "E", 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, "SE1", "BE1", "RE1", 50.0, 1),
                    ("moi_v1", "IcyCore", "e2", "E", 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, "SE2", "BE2", "RE2", 45.0, 2),
                    ("moi_v1", "IcyCore", "n1", "N", 0.0, 0.0, 0.0, 10.0, 0.0, 10.0, "SN1", "BN1", "RN1", 60.0, 1),
                    ("moi_v1", "IcyCore", "w1", "W", 0.0, 0.0, 0.0, -10.0, 0.0, 0.0, "SW1", "BW1", "RW1", 30.0, 1),
                    ("moi_v1", "IcyCore", "s1", "S", 0.0, 0.0, 0.0, 0.0, 0.0, -10.0, "SS1", "BS1", "RS1", 20.0, 1),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_sequence_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "seq.sqlite"
            out_path = Path(tmp) / "itinerary.md"
            self._create_fixture(db_path)

            result = write_itinerary(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                start_xyz=(12.0, 0.0, 0.0),
                out_path=out_path,
            )
            self.assertEqual(result["order"], ["E", "N", "W", "S"])
            rows = result["rows"]
            self.assertEqual(rows[0]["entry_ring_id"], "e1")
            self.assertEqual(rows[1]["entry_ring_id"], "n1")
            self.assertTrue(out_path.exists())
            text = out_path.read_text(encoding="utf-8")
            self.assertIn("| 1 | E |", text)
            self.assertIn("| 2 | N |", text)


if __name__ == "__main__":
    unittest.main()
