import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings import apply_schema
from contextlib import closing


class Phase3SchemaTests(unittest.TestCase):
    def test_apply_schema_creates_phase3_tables(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "rings_master.sqlite"
            schema_path = Path("scripts/rings/schema_phase3.sql")

            created = apply_schema.apply_schema_file(db_path, schema_path)
            self.assertEqual(
                created,
                ["cohort_cutoffs", "cohort_members", "global_norms"],
            )

            with closing(sqlite3.connect(db_path)) as conn:
                names = {
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type='table'
                        """
                    ).fetchall()
                }
            self.assertIn("global_norms", names)
            self.assertIn("cohort_cutoffs", names)
            self.assertIn("cohort_members", names)


if __name__ == "__main__":
    unittest.main()
