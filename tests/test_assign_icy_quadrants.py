import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.assign_icy_quadrants import assign_quadrants


class AssignIcyQuadrantsTests(unittest.TestCase):
    def _create_fixture(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE cohort_members (
                    score_version TEXT,
                    cohort_name TEXT,
                    ring_id TEXT,
                    rank INTEGER
                );
                CREATE TABLE rings_raw (
                    ring_id TEXT PRIMARY KEY,
                    system_name TEXT,
                    body_name TEXT,
                    ring_name TEXT,
                    x REAL,
                    y REAL,
                    z REAL
                );
                CREATE TABLE rings_scored (
                    ring_id TEXT,
                    score_version TEXT,
                    moi_final REAL
                );
                """
            )
            conn.executemany(
                "INSERT INTO cohort_members (score_version, cohort_name, ring_id, rank) VALUES (?, ?, ?, ?)",
                [
                    ("moi_v1", "IcyCore", "e1", 1),
                    ("moi_v1", "IcyCore", "n1", 2),
                    ("moi_v1", "IcyCore", "w1", 3),
                    ("moi_v1", "IcyCore", "s1", 4),
                ],
            )
            conn.executemany(
                "INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("e1", "SE", "BE", "RE", 10.0, 0.0, 0.0),   # East
                    ("n1", "SN", "BN", "RN", 0.0, 0.0, 10.0),   # North
                    ("w1", "SW", "BW", "RW", -10.0, 0.0, 0.0),  # West
                    ("s1", "SS", "BS", "RS", 0.0, 0.0, -10.0),  # South
                ],
            )
            conn.executemany(
                "INSERT INTO rings_scored (ring_id, score_version, moi_final) VALUES (?, ?, ?)",
                [
                    ("e1", "moi_v1", 10.0),
                    ("n1", "moi_v1", 20.0),
                    ("w1", "moi_v1", 30.0),
                    ("s1", "moi_v1", 40.0),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_assigns_expected_quadrants(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "quad.sqlite"
            self._create_fixture(db_path)
            summary = assign_quadrants(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                moi_metric="moi_final",
                saga_x=0.0,
                saga_y=0.0,
                saga_z=0.0,
                dry_run=False,
            )
            self.assertEqual(summary["total"], 4)
            self.assertEqual(summary["counts"], {"E": 1, "N": 1, "S": 1, "W": 1})

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT ring_id, quadrant
                    FROM icy_quadrants
                    WHERE score_version='moi_v1' AND cohort_name='IcyCore'
                    ORDER BY ring_id ASC
                    """
                ).fetchall()
                self.assertEqual(rows, [("e1", "E"), ("n1", "N"), ("s1", "S"), ("w1", "W")])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
