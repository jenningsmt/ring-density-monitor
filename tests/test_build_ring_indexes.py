import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings import build_ring_indexes
from contextlib import closing


def _create_min_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE rings_raw (
                ring_id TEXT PRIMARY KEY,
                ring_type TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE rings_scored (
                ring_id TEXT PRIMARY KEY,
                ring_type TEXT,
                moi_final REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO rings_scored (ring_id, ring_type, moi_final) VALUES (?, ?, ?)",
            [
                ("a", "Metallic", 0.9),
                ("b", "Metallic", 0.8),
                ("c", "Icy", 0.7),
            ],
        )
        conn.commit()


class BuildRingIndexesTests(unittest.TestCase):
    def test_analysis_indexes_created_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "rings_master.sqlite"
            _create_min_db(db_path)

            first = build_ring_indexes.main(["--db", str(db_path), "--analysis"])
            self.assertEqual(first, 0)
            second = build_ring_indexes.main(["--db", str(db_path), "--analysis"])
            self.assertEqual(second, 0)

            with closing(sqlite3.connect(db_path)) as conn:
                names = {
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type='index'
                        """
                    ).fetchall()
                }

            self.assertIn("idx_rings_raw_ring_type_ring_id", names)
            self.assertIn("idx_rings_scored_moi_final_desc_notnull", names)
            self.assertIn("idx_rings_scored_ring_type_moi_final_desc_notnull", names)

            with closing(sqlite3.connect(db_path)) as conn:
                plan_rows = conn.execute(
                    """
                    EXPLAIN QUERY PLAN
                    SELECT ring_id
                    FROM rings_scored
                    WHERE ring_type='Metallic' AND moi_final IS NOT NULL
                    ORDER BY moi_final DESC
                    LIMIT 10
                    """
                ).fetchall()
                plan_text = " | ".join(str(row[-1]) for row in plan_rows)
                self.assertIn("idx_rings_scored_ring_type_moi_final_desc_notnull", plan_text)
                self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plan_text)


if __name__ == "__main__":
    unittest.main()
