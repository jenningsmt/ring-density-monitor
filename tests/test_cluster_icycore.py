import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.cluster_icycore import run_clustering


class ClusterIcyCoreTests(unittest.TestCase):
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
                """
                INSERT INTO cohort_members (score_version, cohort_name, ring_id, rank)
                VALUES (?, ?, ?, ?)
                """,
                [
                    ("moi_v1", "IcyCore", "r1", 1),
                    ("moi_v1", "IcyCore", "r2", 2),
                    ("moi_v1", "IcyCore", "r3", 3),
                    ("moi_v1", "IcyCore", "r4", 4),
                    ("moi_v1", "IcyCore", "r5", 5),
                    ("moi_v1", "IcyCore", "r6", 6),
                ],
            )
            conn.executemany(
                """
                INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("r1", "S1", "B1", "R1", 0.0, 0.0, 0.0),
                    ("r2", "S2", "B2", "R2", 1000.0, 0.0, 0.0),
                    ("r3", "S3", "B3", "R3", 1900.0, 0.0, 0.0),
                    ("r4", "S4", "B4", "R4", 10000.0, 0.0, 0.0),
                    ("r5", "S5", "B5", "R5", 11000.0, 0.0, 0.0),
                    ("r6", "S6", "B6", "R6", 30000.0, 0.0, 0.0),
                ],
            )
            conn.executemany(
                """
                INSERT INTO rings_scored (ring_id, score_version, moi_final)
                VALUES (?, ?, ?)
                """,
                [
                    ("r1", "moi_v1", 90.0),
                    ("r2", "moi_v1", 80.0),
                    ("r3", "moi_v1", 70.0),
                    ("r4", "moi_v1", 60.0),
                    ("r5", "moi_v1", 50.0),
                    ("r6", "moi_v1", 40.0),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_cluster_outputs_and_sweep(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "cluster.sqlite"
            out_dir = Path(tmp) / "out"
            self._create_fixture(db_path)

            sweep = run_clustering(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                moi_metric="moi_final",
                thresholds=[2000.0, 500.0],
                out_dir=out_dir,
                dry_run=False,
            )
            self.assertEqual(len(sweep), 2)
            by_threshold = {row["threshold"]: row for row in sweep}
            self.assertEqual(by_threshold[2000]["num_clusters"], 3)
            self.assertEqual(by_threshold[2000]["num_singletons"], 1)
            self.assertEqual(by_threshold[2000]["largest_cluster_size"], 3)
            self.assertEqual(by_threshold[500]["num_clusters"], 6)

            clusters_path = out_dir / "clusters_D2000.csv"
            members_path = out_dir / "members_D2000.csv"
            sweep_path = out_dir / "sweep_summary.csv"
            self.assertTrue(clusters_path.exists())
            self.assertTrue(members_path.exists())
            self.assertTrue(sweep_path.exists())

            with clusters_path.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["cluster_id"] for row in rows], ["D2000_C001", "D2000_C002", "D2000_C003"])
            self.assertEqual([row["size"] for row in rows], ["3", "2", "1"])
            self.assertEqual(rows[0]["min_ring_id"], "r1")

            with members_path.open("r", newline="", encoding="utf-8") as handle:
                members = list(csv.DictReader(handle))
            c1_rings = [row["ring_id"] for row in members if row["cluster_id"] == "D2000_C001"]
            self.assertEqual(c1_rings, ["r1", "r2", "r3"])

            with sweep_path.open("r", newline="", encoding="utf-8") as handle:
                sweep_rows = list(csv.DictReader(handle))
            self.assertEqual([row["threshold"] for row in sweep_rows], ["2000", "500"])


if __name__ == "__main__":
    unittest.main()
