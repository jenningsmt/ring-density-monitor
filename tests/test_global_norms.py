import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.apply_schema import apply_schema_file
from scripts.rings.compute_global_norms import compute_global_norms
from contextlib import closing


class GlobalNormsTests(unittest.TestCase):
    def _create_base_db(self, path: Path) -> None:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                """
                CREATE TABLE rings_scored (
                    score_version TEXT,
                    ring_type TEXT,
                    ring_id TEXT,
                    moi0 REAL
                )
                """
            )
            conn.commit()
        apply_schema_file(path, Path("scripts/rings/schema_phase3.sql"))

    def test_global_norms_population_stddev_and_quantiles(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "norms.sqlite"
            self._create_base_db(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                conn.executemany(
                    """
                    INSERT INTO rings_scored (score_version, ring_type, ring_id, moi0)
                    VALUES (?, ?, ?, ?)
                    """,
                    [("moi_v1", "Metallic", f"r{i:02d}", float(i)) for i in range(1, 11)],
                )
                conn.commit()

                upserted = compute_global_norms(
                    conn=conn,
                    score_version="moi_v1",
                    ring_types=["Metallic"],
                    metrics=["moi0"],
                    quantiles=[0.95, 0.99, 0.995, 0.999],
                    algo_version="norm_v1",
                    dry_run=False,
                )
                self.assertEqual(upserted, 1)

                row = conn.execute(
                    """
                    SELECT n, mean, stddev, p95, p99, p99_5, p99_9, min_value, max_value
                    FROM global_norms
                    WHERE score_version=? AND ring_type=? AND metric=?
                    """,
                    ("moi_v1", "Metallic", "moi0"),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], 10)
            self.assertAlmostEqual(row[1], 5.5, places=12)
            expected_stddev = math.sqrt(8.25)  # population stddev for [1..10]
            self.assertAlmostEqual(row[2], expected_stddev, places=12)
            self.assertEqual(row[3], 9.0)   # floor((10-1)*0.95)=8 -> 9th value
            self.assertEqual(row[4], 9.0)   # floor(8.91)=8 -> 9th value
            self.assertEqual(row[5], 9.0)   # floor(8.955)=8 -> 9th value
            self.assertEqual(row[6], 9.0)   # floor(8.991)=8 -> 9th value
            self.assertEqual(row[7], 1.0)
            self.assertEqual(row[8], 10.0)

    def test_global_norms_n_equals_one(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "norms_single.sqlite"
            self._create_base_db(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO rings_scored (score_version, ring_type, ring_id, moi0)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("moi_v1", "Icy", "i1", 42.0),
                )
                conn.commit()

                upserted = compute_global_norms(
                    conn=conn,
                    score_version="moi_v1",
                    ring_types=["Icy"],
                    metrics=["moi0"],
                    quantiles=[0.95, 0.99, 0.995, 0.999],
                    algo_version="norm_v1",
                    dry_run=False,
                )
                self.assertEqual(upserted, 1)
                row = conn.execute(
                    """
                    SELECT n, mean, stddev, p95, p99, p99_5, p99_9, min_value, max_value
                    FROM global_norms
                    WHERE score_version=? AND ring_type=? AND metric=?
                    """,
                    ("moi_v1", "Icy", "moi0"),
                ).fetchone()

            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], 42.0)
            self.assertEqual(row[2], 0.0)
            self.assertEqual(row[3], 42.0)
            self.assertEqual(row[4], 42.0)
            self.assertEqual(row[5], 42.0)
            self.assertEqual(row[6], 42.0)
            self.assertEqual(row[7], 42.0)
            self.assertEqual(row[8], 42.0)

    def test_global_norms_n_equals_zero_skips_upsert(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "norms_zero.sqlite"
            self._create_base_db(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                conn.executemany(
                    """
                    INSERT INTO rings_scored (score_version, ring_type, ring_id, moi0)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        ("moi_v1", "Rocky", "z1", None),
                        ("moi_v1", "Rocky", "z2", None),
                    ],
                )
                conn.commit()

                upserted = compute_global_norms(
                    conn=conn,
                    score_version="moi_v1",
                    ring_types=["Rocky"],
                    metrics=["moi0"],
                    quantiles=[0.95, 0.99, 0.995, 0.999],
                    algo_version="norm_v1",
                    dry_run=False,
                )
                self.assertEqual(upserted, 0)
                count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM global_norms
                    WHERE score_version=? AND ring_type=? AND metric=?
                    """,
                    ("moi_v1", "Rocky", "moi0"),
                ).fetchone()[0]
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
