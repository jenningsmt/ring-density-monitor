import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.build_metprox import build_metprox


class BuildMetProxTests(unittest.TestCase):
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
                CREATE TABLE expedition_waypoints (
                    route_id TEXT,
                    seq INTEGER,
                    ring_id TEXT,
                    system_name TEXT,
                    body_name TEXT,
                    ring_name TEXT,
                    x REAL,
                    y REAL,
                    z REAL,
                    step_distance_ly REAL,
                    cumulative_distance_ly REAL,
                    PRIMARY KEY(route_id, seq)
                );
                CREATE TABLE cohort_cutoffs (
                    score_version TEXT,
                    cohort_name TEXT,
                    ring_type TEXT,
                    target_n INTEGER,
                    theta_value REAL,
                    theta_ring_id TEXT,
                    computed_at TEXT,
                    algo_version TEXT,
                    notes TEXT,
                    PRIMARY KEY(score_version, cohort_name, ring_type)
                );
                """
            )
            conn.executemany(
                """
                INSERT INTO expedition_waypoints (
                    route_id, seq, ring_id, system_name, body_name, ring_name, x, y, z, step_distance_ly, cumulative_distance_ly
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("routeA", 1, "w1", "S", "B", "R", 0.0, 0.0, 0.0, 0.0, 0.0),
                    ("routeA", 2, "w2", "S", "B", "R", 10.0, 0.0, 0.0, 10.0, 10.0),
                    ("routeA", 3, "w3", "S", "B", "R", 20.0, 0.0, 0.0, 10.0, 20.0),
                ],
            )
            conn.executemany(
                """
                INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("m1", "SM1", "BM1", "RM1", 0.0, 3.0, 0.0),
                    ("m2", "SM2", "BM2", "RM2", 10.0, 4.0, 0.0),
                    ("m3", "SM3", "BM3", "RM3", 5.0, 0.0, 0.0),
                    ("m4", "SM4", "BM4", "RM4", 30.0, 0.0, 0.0),
                ],
            )
            conn.executemany(
                """
                INSERT INTO rings_scored (ring_id, score_version, ring_type, moi_final)
                VALUES (?, ?, ?, ?)
                """,
                [
                    ("m1", "moi_v1", "Metallic", 90.0),
                    ("m2", "moi_v1", "Metallic", 80.0),
                    ("m3", "moi_v1", "Metallic", 85.0),
                    ("m4", "moi_v1", "Metallic", 95.0),
                ],
            )
            conn.execute(
                """
                INSERT INTO cohort_cutoffs (
                    score_version, cohort_name, ring_type, target_n, theta_value,
                    theta_ring_id, computed_at, algo_version, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("moi_v1", "MetTail", "Metallic", 2, 82.0, "m3", "now", "v1", ""),
            )
            conn.commit()
        finally:
            conn.close()

    def test_theta_mode_radius_and_seq_tie_break(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "metprox.sqlite"
            self._create_fixture(db_path)

            summary = build_metprox(
                db_path=db_path,
                route_id="routeA",
                score_version="moi_v1",
                moi_metric="moi_final",
                ring_type="Metallic",
                radius_ly=5.0,
                use_theta=True,
                theta=None,
                top_per_waypoint=None,
                dry_run=False,
            )
            self.assertEqual(summary["count"], 2)

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT ring_id, moi_metric, distance_to_route_ly, source_waypoint_seq
                    FROM metprox_members
                    WHERE route_id=?
                    ORDER BY moi_metric DESC, ring_id ASC
                    """,
                    ("routeA",),
                ).fetchall()
                self.assertEqual([row[0] for row in rows], ["m1", "m3"])
                # m3 is exactly 5LY from seq1 and seq2; smallest seq must be chosen deterministically.
                self.assertEqual(rows[1][3], 1)
            finally:
                conn.close()

    def test_top_per_waypoint_mode_dedup(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "metprox.sqlite"
            self._create_fixture(db_path)

            summary = build_metprox(
                db_path=db_path,
                route_id="routeA",
                score_version="moi_v1",
                moi_metric="moi_final",
                ring_type="Metallic",
                radius_ly=5.0,
                use_theta=False,
                theta=None,
                top_per_waypoint=1,
                dry_run=False,
            )
            self.assertEqual(summary["count"], 2)
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT ring_id FROM metprox_members WHERE route_id=? ORDER BY moi_metric DESC, ring_id ASC",
                    ("routeA",),
                ).fetchall()
                self.assertEqual([row[0] for row in rows], ["m1", "m3"])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
