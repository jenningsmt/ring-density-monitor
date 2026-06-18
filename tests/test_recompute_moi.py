import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings import recompute_moi
from contextlib import closing


def _create_fixture_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE rings_raw (
                ring_id TEXT PRIMARY KEY,
                ring_type TEXT NULL,
                surface_density REAL NULL,
                linear_density REAL NULL,
                arrival_distance_ls REAL NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ring_survey (
                ring_id TEXT PRIMARY KEY
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO rings_raw (ring_id, ring_type, surface_density, linear_density, arrival_distance_ls)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("r001", "Metallic", 10.0, 100.0, 500.0),
                ("r002", "Icy", 20.0, 80.0, 200.0),
                ("r003", "Rocky", 15.0, 120.0, None),
                ("r004", "Metallic", None, 130.0, 100.0),  # ineligible (missing surface_density)
                ("r005", "Icy", 9.0, None, 50.0),          # ineligible (missing linear_density)
            ],
        )
        conn.commit()


class RecomputeMoiTests(unittest.TestCase):
    def test_recompute_creates_rows_for_eligible(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "rings_master.sqlite"
            _create_fixture_db(db_path)

            exit_code = recompute_moi.main(["--db", str(db_path), "--progress-seconds", "1", "--quiet"])
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT ring_id, ring_type, moi_raw, moi_normalized, moi_final, norm_population, norm_count, flags
                    FROM rings_scored
                    ORDER BY ring_id
                    """
                ).fetchall()
                self.assertEqual(len(rows), 5)
                eligible = [r for r in rows if r[2] is not None]
                self.assertEqual(len(eligible), 3)
                self.assertTrue(all(r[5] == recompute_moi.NORM_POPULATION for r in rows))
                self.assertTrue(all(r[6] == 3 for r in rows))
                ring_types = {r[0]: r[1] for r in rows}
                self.assertEqual(ring_types["r001"], "Metallic")
                self.assertEqual(ring_types["r002"], "Icy")

    def test_recompute_is_deterministic_across_runs(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "rings_master.sqlite"
            _create_fixture_db(db_path)

            first = recompute_moi.main(["--db", str(db_path), "--progress-seconds", "1", "--quiet"])
            self.assertEqual(first, 0)

            with closing(sqlite3.connect(db_path)) as conn:
                before = conn.execute(
                    """
                    SELECT ring_id, moi_normalized, moi_final
                    FROM rings_scored
                    ORDER BY ring_id
                    """
                ).fetchall()

            second = recompute_moi.main(["--db", str(db_path), "--progress-seconds", "1", "--quiet"])
            self.assertEqual(second, 0)

            with closing(sqlite3.connect(db_path)) as conn:
                after = conn.execute(
                    """
                    SELECT ring_id, moi_normalized, moi_final
                    FROM rings_scored
                    ORDER BY ring_id
                    """
                ).fetchall()

            self.assertEqual(before, after)

    def test_score_runs_written_with_finished_at(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "rings_master.sqlite"
            _create_fixture_db(db_path)

            exit_code = recompute_moi.main(["--db", str(db_path), "--progress-seconds", "1", "--quiet"])
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT score_version, started_at, finished_at, params_json
                    FROM score_runs
                    ORDER BY run_id DESC
                    LIMIT 1
                    """
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], recompute_moi.DEFAULT_SCORE_VERSION)
                self.assertIsNotNone(row[1])
                self.assertIsNotNone(row[2])
                self.assertIn("population", row[3])


if __name__ == "__main__":
    unittest.main()
