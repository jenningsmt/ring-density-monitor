import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.build_icy_route import build_route


class BuildIcyRouteTests(unittest.TestCase):
    def _create_fixture(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
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
                    ring_type TEXT,
                    moi_final REAL
                );
                CREATE TABLE cohort_members (
                    score_version TEXT,
                    cohort_name TEXT,
                    ring_id TEXT,
                    rank INTEGER,
                    moi0 REAL
                );
                """
            )
            conn.executemany(
                """
                INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("r_a", "Sys", "A", "A Ring", 1.0, 0.0, 0.0),
                    ("r_b", "Sys", "B", "B Ring", -1.0, 0.0, 0.0),
                    ("r_c", "Sys", "C", "C Ring", 0.0, 1.0, 0.0),
                    ("r_d", "Sys", "D", "D Ring", 2.0, 0.0, 0.0),
                    ("r_e", "Sys", "E", "E Ring", -2.0, 0.0, 0.0),
                ],
            )
            conn.executemany(
                """
                INSERT INTO rings_scored (ring_id, score_version, ring_type, moi_final)
                VALUES (?, ?, ?, ?)
                """,
                [
                    ("r_a", "moi_v1", "Icy", 30.0),
                    ("r_b", "moi_v1", "Icy", 30.0),
                    ("r_c", "moi_v1", "Icy", 20.0),
                    ("r_d", "moi_v1", "Icy", 10.0),
                    ("r_e", "moi_v1", "Icy", 5.0),
                ],
            )
            conn.executemany(
                """
                INSERT INTO cohort_members (score_version, cohort_name, ring_id, rank, moi0)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("moi_v1", "IcyCore", "r_c", 1, 20.0),
                    ("moi_v1", "IcyCore", "r_b", 2, 30.0),
                    ("moi_v1", "IcyCore", "r_a", 3, 30.0),
                    ("moi_v1", "IcyCore", "r_d", 4, 10.0),
                    ("moi_v1", "IcyCore", "r_e", 5, 5.0),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_build_route_is_deterministic_and_tie_breaks(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "route.sqlite"
            self._create_fixture(db_path)

            s1 = build_route(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                moi_metric="moi_final",
                anchor_mode="explicit_xyz",
                anchor_xyz=(0.0, 0.0, 0.0),
            )
            s2 = build_route(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                moi_metric="moi_final",
                anchor_mode="explicit_xyz",
                anchor_xyz=(0.0, 0.0, 0.0),
            )
            self.assertEqual(s1["route_id"], s2["route_id"])

            conn = sqlite3.connect(db_path)
            try:
                waypoints = conn.execute(
                    """
                    SELECT seq, ring_id, step_distance_ly, cumulative_distance_ly
                    FROM expedition_waypoints
                    WHERE route_id=?
                    ORDER BY seq ASC
                    """,
                    (s1["route_id"],),
                ).fetchall()
                self.assertEqual(len(waypoints), 5)
                seq_ring_ids = [row[1] for row in waypoints]
                self.assertEqual(seq_ring_ids[0], "r_a")
                self.assertEqual(seq_ring_ids, ["r_a", "r_d", "r_c", "r_b", "r_e"])

                total_from_steps = sum(float(row[2]) for row in waypoints)
                last_cumulative = float(waypoints[-1][3])
                self.assertAlmostEqual(total_from_steps, last_cumulative, places=9)

                route_total = conn.execute(
                    "SELECT total_distance_ly FROM expedition_routes WHERE route_id=?",
                    (s1["route_id"],),
                ).fetchone()[0]
                self.assertAlmostEqual(float(route_total), last_cumulative, places=9)

                expected = (
                    1.0
                    + 1.0
                    + math.sqrt(5.0)
                    + math.sqrt(2.0)
                    + 1.0
                )
                self.assertAlmostEqual(float(route_total), expected, places=9)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
