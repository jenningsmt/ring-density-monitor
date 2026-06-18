import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.phase3_driver import run_phase3
from contextlib import closing


class Phase3DriverSmokeTests(unittest.TestCase):
    def _create_scored_db(self, path: Path) -> None:
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
                    ("moi_v1", "Icy", "i1", 10.0),
                    ("moi_v1", "Icy", "i2", 9.0),
                    ("moi_v1", "Icy", "i3", 8.0),
                    ("moi_v1", "Icy", "i4", 7.0),
                    ("moi_v1", "Icy", "i5", 6.0),
                    ("moi_v1", "Metallic", "m1", 20.0),
                    ("moi_v1", "Metallic", "m2", 19.0),
                    ("moi_v1", "Metallic", "m3", 18.0),
                    ("moi_v1", "Metallic", "m4", 17.0),
                    ("moi_v1", "Metallic", "m5", 16.0),
                ],
            )
            conn.commit()

    def test_phase3_driver_writes_norms_cutoffs_and_members(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "phase3.sqlite"
            self._create_scored_db(db_path)

            summary = run_phase3(
                db_path=db_path,
                score_version="moi_v1",
                metrics="moi0",
                ring_types=None,
                icy_n=3,
                met_n=2,
                with_asc=True,
                dry_run=False,
            )
            self.assertEqual(summary["global_norms_count"], 2)

            with closing(sqlite3.connect(db_path)) as conn:
                norms_count = conn.execute(
                    "SELECT COUNT(*) FROM global_norms WHERE score_version=?",
                    ("moi_v1",),
                ).fetchone()[0]
                self.assertEqual(norms_count, 2)

                cutoff_names = {
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT cohort_name
                        FROM cohort_cutoffs
                        WHERE score_version=?
                        """,
                        ("moi_v1",),
                    ).fetchall()
                }
                self.assertEqual(cutoff_names, {"IcyCore", "MetTail"})

                icy_count = conn.execute(
                    "SELECT COUNT(*) FROM cohort_members WHERE score_version=? AND cohort_name='IcyCore'",
                    ("moi_v1",),
                ).fetchone()[0]
                met_count = conn.execute(
                    "SELECT COUNT(*) FROM cohort_members WHERE score_version=? AND cohort_name='MetTail'",
                    ("moi_v1",),
                ).fetchone()[0]
                self.assertEqual(icy_count, 3)
                self.assertEqual(met_count, 2)

    def test_phase3_driver_dry_run_writes_no_phase3_rows(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "phase3_dry.sqlite"
            self._create_scored_db(db_path)

            run_phase3(
                db_path=db_path,
                score_version="moi_v1",
                metrics="moi0",
                ring_types=None,
                icy_n=3,
                met_n=2,
                with_asc=False,
                dry_run=True,
            )

            with closing(sqlite3.connect(db_path)) as conn:
                norms_count = conn.execute("SELECT COUNT(*) FROM global_norms").fetchone()[0]
                cutoffs_count = conn.execute("SELECT COUNT(*) FROM cohort_cutoffs").fetchone()[0]
                members_count = conn.execute("SELECT COUNT(*) FROM cohort_members").fetchone()[0]
                self.assertEqual(norms_count, 0)
                self.assertEqual(cutoffs_count, 0)
                self.assertEqual(members_count, 0)


if __name__ == "__main__":
    unittest.main()
