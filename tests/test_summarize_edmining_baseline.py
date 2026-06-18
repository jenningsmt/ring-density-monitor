import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.summarize_edmining_baseline import summarize


class SummarizeEDMiningBaselineTests(unittest.TestCase):
    def test_summary_stats_and_buckets(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "crosswalk.sqlite"
            out_path = Path(tmp) / "summary.md"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE edmining_best_matches (
                        source_url TEXT PRIMARY KEY,
                        system_name TEXT,
                        planets TEXT,
                        status TEXT,
                        candidate_count INTEGER NOT NULL,
                        best_ring_id TEXT,
                        best_ring_type TEXT,
                        best_moi_final REAL,
                        best_percentile REAL,
                        best_in_icycore INTEGER NOT NULL,
                        top_candidates TEXT,
                        computed_at TEXT NOT NULL,
                        score_version TEXT NOT NULL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO edmining_best_matches (
                        source_url, system_name, planets, status, candidate_count,
                        best_ring_id, best_ring_type, best_moi_final, best_percentile,
                        best_in_icycore, top_candidates, computed_at, score_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("u1", "A", "P1", "MATCHED", 1, "r1", "Icy", 10.0, 0.40, 0, "", "t", "moi_v1"),
                        ("u2", "B", "P2", "MATCHED", 1, "r2", "Icy", 20.0, 0.70, 1, "", "t", "moi_v1"),
                        ("u3", "C", "P3", "MATCHED", 1, "r3", "Icy", 30.0, 0.90, 0, "", "t", "moi_v1"),
                        ("u4", "D", "P4", "MATCHED", 1, "r4", "Icy", 40.0, 0.97, 1, "", "t", "moi_v1"),
                        ("u5", "E", "P5", "MATCHED", 1, "r5", "Icy", 50.0, 0.995, 0, "", "t", "moi_v1"),
                        ("u6", "F", "P6", "MATCHED", 1, "r6", "Metallic", 99.0, 0.999, 0, "", "t", "moi_v1"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            summarize(db_path, "moi_v1", out_path)
            text = out_path.read_text(encoding="utf-8")
            self.assertIn("- count: 5", text)
            self.assertIn("- min: 10.000000", text)
            self.assertIn("- median: 30.000000", text)
            self.assertIn("- p90: 40.000000", text)
            self.assertIn("- p99: 40.000000", text)
            self.assertIn("- max: 50.000000", text)
            self.assertIn("- <0.5: 1", text)
            self.assertIn("- 0.5-0.8: 1", text)
            self.assertIn("- 0.8-0.95: 1", text)
            self.assertIn("- 0.95-0.99: 1", text)
            self.assertIn("- >=0.99: 1", text)


if __name__ == "__main__":
    unittest.main()
