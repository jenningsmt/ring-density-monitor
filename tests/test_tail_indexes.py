import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.build_tail_indexes import build_tail_indexes
from contextlib import closing


class TailIndexesTests(unittest.TestCase):
    def _create_db(self, path: Path) -> None:
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
            conn.executemany(
                """
                INSERT INTO rings_scored (score_version, ring_type, ring_id, moi0)
                VALUES (?, ?, ?, ?)
                """,
                [
                    ("moi_v1", "Metallic", "r3", 10.0),
                    ("moi_v1", "Metallic", "r1", 12.0),
                    ("moi_v1", "Metallic", "r2", 12.0),
                    ("moi_v1", "Metallic", "r4", 9.0),
                    ("moi_v1", "Icy", "i1", 100.0),
                ],
            )
            conn.commit()

    def test_build_tail_indexes_idempotent_and_ordering(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "tail.sqlite"
            self._create_db(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                metric_first, names_first = build_tail_indexes(conn, with_asc=False)
                metric_second, names_second = build_tail_indexes(conn, with_asc=False)
                conn.commit()

                self.assertEqual(metric_first, "moi0")
                self.assertEqual(metric_second, "moi0")
                self.assertIn("idx_scored_v_rt_moi0_desc", names_first)
                self.assertNotIn("idx_scored_v_rt_moi0_asc", names_first)
                self.assertEqual(names_first, names_second)

                ordered_ids = [
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT ring_id
                        FROM rings_scored
                        WHERE score_version=? AND ring_type=? AND moi0 IS NOT NULL
                        ORDER BY moi0 DESC, ring_id ASC
                        """,
                        ("moi_v1", "Metallic"),
                    ).fetchall()
                ]
            self.assertEqual(ordered_ids, ["r1", "r2", "r3", "r4"])

    def test_build_tail_indexes_with_asc(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "tail.sqlite"
            self._create_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                metric, names = build_tail_indexes(conn, with_asc=True)
                conn.commit()
            self.assertEqual(metric, "moi0")
            self.assertIn("idx_scored_v_rt_moi0_desc", names)
            self.assertIn("idx_scored_v_rt_moi0_asc", names)

    def test_build_tail_indexes_uses_moi_final_when_resolved(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "tail_final.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE rings_scored (
                        score_version TEXT,
                        ring_type TEXT,
                        ring_id TEXT,
                        moi_final REAL
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO rings_scored (score_version, ring_type, ring_id, moi_final) VALUES (?, ?, ?, ?)",
                    ("moi_v1", "Metallic", "r1", 1.0),
                )
                metric, names = build_tail_indexes(conn, with_asc=True)
                conn.commit()
            self.assertEqual(metric, "moi_final")
            self.assertIn("idx_scored_v_rt_moi_final_desc", names)
            self.assertIn("idx_scored_v_rt_moi_final_asc", names)


if __name__ == "__main__":
    unittest.main()
