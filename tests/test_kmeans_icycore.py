import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.kmeans_icycore import run_kmeans_sweep


class KMeansIcyCoreTests(unittest.TestCase):
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
            rows = [
                ("r1", 0.0, 0.0, 0.0, 90.0),
                ("r2", 1.0, 0.0, 0.0, 85.0),
                ("r3", 0.0, 1.0, 0.0, 82.0),
                ("r4", 100.0, 100.0, 0.0, 80.0),
                ("r5", 101.0, 100.0, 0.0, 79.0),
                ("r6", 100.0, 101.0, 0.0, 78.0),
                ("r7", 200.0, 0.0, 0.0, 70.0),
                ("r8", 201.0, 0.0, 0.0, 69.0),
            ]
            conn.executemany(
                "INSERT INTO cohort_members (score_version, cohort_name, ring_id, rank) VALUES (?, ?, ?, ?)",
                [("moi_v1", "IcyCore", row[0], idx + 1) for idx, row in enumerate(rows)],
            )
            conn.executemany(
                "INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(row[0], f"S{row[0]}", f"B{row[0]}", f"R{row[0]}", row[1], row[2], row[3]) for row in rows],
            )
            conn.executemany(
                "INSERT INTO rings_scored (ring_id, score_version, moi_final) VALUES (?, ?, ?)",
                [(row[0], "moi_v1", row[4]) for row in rows],
            )
            conn.commit()
        finally:
            conn.close()

    def test_kmeans_outputs_are_deterministic_and_nonempty(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "kmeans.sqlite"
            out1 = Path(tmp) / "out1"
            out2 = Path(tmp) / "out2"
            self._create_fixture(db_path)

            rows1 = run_kmeans_sweep(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                moi_metric="moi_final",
                k_list=[2, 3],
                max_iter=50,
                tol=1e-6,
                out_dir=out1,
                dry_run=False,
            )
            rows2 = run_kmeans_sweep(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                moi_metric="moi_final",
                k_list=[2, 3],
                max_iter=50,
                tol=1e-6,
                out_dir=out2,
                dry_run=False,
            )
            self.assertEqual(rows1, rows2)

            for k in (2, 3):
                clusters1 = (out1 / f"clusters_k{k}.csv").read_text(encoding="utf-8")
                clusters2 = (out2 / f"clusters_k{k}.csv").read_text(encoding="utf-8")
                members1 = (out1 / f"members_k{k}.csv").read_text(encoding="utf-8")
                members2 = (out2 / f"members_k{k}.csv").read_text(encoding="utf-8")
                self.assertEqual(clusters1, clusters2)
                self.assertEqual(members1, members2)
                self.assertTrue((out1 / f"summary_k{k}.md").exists())
                self.assertTrue((out1 / f"centroids_k{k}.json").exists())

                with (out1 / f"clusters_k{k}.csv").open("r", newline="", encoding="utf-8") as handle:
                    cluster_rows = list(csv.DictReader(handle))
                self.assertEqual(len(cluster_rows), k)
                self.assertTrue(all(int(r["size"]) > 0 for r in cluster_rows))

            self.assertTrue((out1 / "sweep_summary.csv").exists())
            with (out1 / "sweep_summary.csv").open("r", newline="", encoding="utf-8") as handle:
                sweep = list(csv.DictReader(handle))
            self.assertEqual([int(r["k"]) for r in sweep], [2, 3])


if __name__ == "__main__":
    unittest.main()
