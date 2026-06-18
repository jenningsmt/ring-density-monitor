import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.sequence_icy_subregions import write_itinerary


class SequenceIcySubregionsTests(unittest.TestCase):
    def _create_fixture(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE subregion_summaries (
                    score_version TEXT,
                    cohort_name TEXT,
                    subregion TEXT,
                    quadrant TEXT NOT NULL,
                    band TEXT NOT NULL,
                    n INTEGER,
                    centroid_x REAL,
                    centroid_y REAL,
                    centroid_z REAL,
                    radius_max_ly REAL,
                    rho_min REAL NOT NULL,
                    rho_median REAL NOT NULL,
                    rho_max REAL NOT NULL,
                    moi_max REAL,
                    moi_median REAL,
                    min_ring_id TEXT,
                    PRIMARY KEY(score_version, cohort_name, subregion)
                );
                CREATE TABLE icy_subregions (
                    score_version TEXT,
                    cohort_name TEXT,
                    ring_id TEXT,
                    quadrant TEXT,
                    subregion TEXT,
                    theta_deg REAL,
                    dx REAL,
                    dz REAL,
                    rho_ly REAL,
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
                INSERT INTO subregion_summaries (
                    score_version, cohort_name, subregion, quadrant, band, n, centroid_x, centroid_y, centroid_z,
                    radius_max_ly, rho_min, rho_median, rho_max, moi_max, moi_median, min_ring_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("moi_v1", "IcyCore", "E-inner", "E", "inner", 2, 10.0, 0.0, 0.0, 1.0, 1.0, 1.5, 2.0, 100.0, 90.0, "e1"),
                    ("moi_v1", "IcyCore", "N-inner", "N", "inner", 1, 10.0, 0.0, 10.0, 2.0, 1.0, 1.0, 1.0, 80.0, 80.0, "n1"),
                    ("moi_v1", "IcyCore", "W-inner", "W", "inner", 1, -10.0, 0.0, 0.0, 3.0, 1.0, 1.0, 1.0, 70.0, 70.0, "w1"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO icy_subregions (
                    score_version, cohort_name, ring_id, quadrant, subregion, theta_deg, dx, dz, rho_ly,
                    x, y, z, system_name, body_name, ring_name, moi_metric, rank
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("moi_v1", "IcyCore", "e1", "E", "E-inner", 0, 0, 0, 1, 10, 0, 0, "SE1", "BE1", "RE1", 100.0, 1),
                    ("moi_v1", "IcyCore", "e2", "E", "E-inner", 0, 0, 0, 1, 10, 0, 0, "SE2", "BE2", "RE2", 90.0, 2),
                    ("moi_v1", "IcyCore", "n1", "N", "N-inner", 0, 0, 0, 1, 10, 0, 10, "SN1", "BN1", "RN1", 80.0, 1),
                    ("moi_v1", "IcyCore", "w1", "W", "W-inner", 0, 0, 0, 1, -10, 0, 0, "SW1", "BW1", "RW1", 70.0, 1),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_deterministic_order_and_entry(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "seq_sub.sqlite"
            out1 = Path(tmp) / "itinerary1.md"
            out2 = Path(tmp) / "itinerary2.md"
            self._create_fixture(db_path)

            r1 = write_itinerary(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                start_xyz=(12.0, 0.0, 0.0),
                out_path=out1,
            )
            r2 = write_itinerary(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                start_xyz=(12.0, 0.0, 0.0),
                out_path=out2,
            )
            self.assertEqual(r1["order"], ["E-inner", "N-inner", "W-inner"])
            self.assertEqual(r1, r2)
            self.assertEqual(r1["rows"][0]["entry_ring_id"], "e1")
            self.assertEqual(out1.read_text(encoding="utf-8"), out2.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
