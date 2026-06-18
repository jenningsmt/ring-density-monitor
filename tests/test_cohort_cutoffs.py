import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.apply_schema import apply_schema_file
from scripts.rings.compute_cohort_cutoffs import compute_and_materialize, fetch_nth_theta
from contextlib import closing


class CohortCutoffTests(unittest.TestCase):
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
                    ("moi_v1", "Metallic", "b", 10.0),
                    ("moi_v1", "Metallic", "a", 10.0),
                    ("moi_v1", "Metallic", "c", 9.0),
                    ("moi_v1", "Metallic", "b2", 9.0),
                    ("moi_v1", "Metallic", "d", 8.0),
                ],
            )
            conn.commit()
        apply_schema_file(path, Path("scripts/rings/schema_phase3.sql"))

    def test_deterministic_cutoff_and_materialized_top_n(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "cohorts.sqlite"
            self._create_db(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                theta_ring_id, theta_value = fetch_nth_theta(conn, "moi_v1", "Metallic", 3, "moi0")
                self.assertEqual(theta_ring_id, "b2")
                self.assertEqual(theta_value, 9.0)

                first = compute_and_materialize(
                    conn=conn,
                    score_version="moi_v1",
                    cohort_name="top3",
                    ring_type="Metallic",
                    target_n=3,
                    algo_version="cutoff_v1",
                    dry_run=False,
                )
                second = compute_and_materialize(
                    conn=conn,
                    score_version="moi_v1",
                    cohort_name="top3",
                    ring_type="Metallic",
                    target_n=3,
                    algo_version="cutoff_v1",
                    dry_run=False,
                )

                self.assertEqual(first[0], "b2")
                self.assertEqual(first[1], 9.0)
                self.assertEqual(first[2], 3)
                self.assertEqual(second[0], first[0])
                self.assertEqual(second[1], first[1])
                self.assertEqual(second[2], first[2])

                cutoff_row = conn.execute(
                    """
                    SELECT target_n, theta_value, theta_ring_id
                    FROM cohort_cutoffs
                    WHERE score_version=? AND cohort_name=? AND ring_type=?
                    """,
                    ("moi_v1", "top3", "Metallic"),
                ).fetchone()
                self.assertEqual(cutoff_row, (3, 9.0, "b2"))

                member_rows = conn.execute(
                    """
                    SELECT ring_id, rank_in_cohort, moi0
                    FROM cohort_members
                    WHERE score_version=? AND cohort_name=?
                    ORDER BY rank_in_cohort ASC
                    """,
                    ("moi_v1", "top3"),
                ).fetchall()
                self.assertEqual(member_rows, [("a", 1, 10.0), ("b", 2, 10.0), ("b2", 3, 9.0)])

    def test_target_n_greater_than_available_raises(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "cohorts.sqlite"
            self._create_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                with self.assertRaises(RuntimeError):
                    compute_and_materialize(
                        conn=conn,
                        score_version="moi_v1",
                        cohort_name="top10",
                        ring_type="Metallic",
                        target_n=10,
                        algo_version="cutoff_v1",
                        dry_run=False,
                    )


if __name__ == "__main__":
    unittest.main()
