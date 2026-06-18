import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.export_edmining_known_sites import run_export
from contextlib import closing


class ExportEDMiningKnownSitesTests(unittest.TestCase):
    def _create_db(self, db_path: Path) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE known_sites_edmining (
                    source_url TEXT PRIMARY KEY,
                    source_category TEXT,
                    system_name TEXT NOT NULL,
                    planets TEXT,
                    estimated_ly_from_bubble REAL,
                    mining_type TEXT,
                    overlap TEXT,
                    thanks_to TEXT,
                    tritium_explicit INTEGER NOT NULL DEFAULT 0,
                    tritium_inferred INTEGER NOT NULL DEFAULT 0,
                    parse_warnings TEXT,
                    material_type_raw TEXT,
                    materials_json TEXT,
                    description TEXT,
                    extracted_at_utc TEXT NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO known_sites_edmining (
                    source_url,
                    source_category,
                    system_name,
                    planets,
                    estimated_ly_from_bubble,
                    mining_type,
                    overlap,
                    thanks_to,
                    tritium_explicit,
                    tritium_inferred,
                    parse_warnings,
                    material_type_raw,
                    materials_json,
                    description,
                    extracted_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "https://edmining.com/mining-location/zeta/",
                        "tritium",
                        "Zeta",
                        "AB2 and AB3 rings",
                        123.4,
                        "Laser",
                        "Double Overlap",
                        "CMDR Z",
                        1,
                        0,
                        None,
                        "Tritium, Platinum",
                        "[\"Tritium\", \"Platinum\"]",
                        "Desc Z",
                        "2026-01-01T00:00:00+00:00",
                    ),
                    (
                        "https://edmining.com/mining-location/alpha/",
                        "tritium",
                        "Alpha",
                        "CD1 rings",
                        22.0,
                        "Core",
                        "1",
                        "CMDR A",
                        0,
                        1,
                        "materials_missing",
                        "",
                        "[]",
                        "Desc A",
                        "2026-01-02T00:00:00+00:00",
                    ),
                ],
            )
            conn.commit()

    def test_export_creates_both_csvs_with_deterministic_order_and_keys(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "sites.sqlite"
            out_dir = tmp_path / "exports"
            self._create_db(db_path)

            known_count, submission_count = run_export(db_path, out_dir)
            self.assertEqual(known_count, 2)
            self.assertEqual(submission_count, 2)

            known_csv = out_dir / "edmining_tritium_known_sites.csv"
            submission_csv = out_dir / "edmining_submission_template.csv"
            self.assertTrue(known_csv.exists())
            self.assertTrue(submission_csv.exists())

            with known_csv.open("r", newline="", encoding="utf-8") as handle:
                known_rows = list(csv.DictReader(handle))
            with submission_csv.open("r", newline="", encoding="utf-8") as handle:
                submission_rows = list(csv.DictReader(handle))

            self.assertEqual(
                known_rows[0].keys(),
                {
                    "site_key",
                    "system_name",
                    "planets",
                    "mining_type",
                    "overlap",
                    "materials_json",
                    "thanks_to",
                    "estimated_ly_from_bubble",
                    "source_url",
                    "extracted_at_utc",
                },
            )
            self.assertEqual(known_rows[0]["system_name"], "Alpha")
            self.assertEqual(known_rows[1]["system_name"], "Zeta")
            self.assertEqual(known_rows[1]["site_key"], "zeta|ab2 and ab3")
            self.assertNotIn("rings", known_rows[1]["site_key"])

            self.assertEqual(
                list(submission_rows[0].keys()),
                [
                    "system_name",
                    "planets",
                    "mining_type",
                    "overlap",
                    "materials",
                    "thanks_to",
                    "description",
                    "ring_hunter_moi",
                    "ring_hunter_percentile",
                    "ring_hunter_cohort",
                    "ring_hunter_notes",
                    "evidence_screenshots_url",
                    "submitted_by",
                    "submitted_at_utc",
                ],
            )
            self.assertEqual(submission_rows[0]["ring_hunter_moi"], "")


if __name__ == "__main__":
    unittest.main()
