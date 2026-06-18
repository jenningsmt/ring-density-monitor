import csv
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.rings import ring_hunter_reports
from contextlib import closing


def _create_fixture_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE rings_raw (
                ring_id TEXT PRIMARY KEY,
                system_name TEXT NOT NULL,
                body_name TEXT NOT NULL,
                ring_name TEXT NOT NULL,
                ring_type TEXT NULL,
                x REAL NULL,
                y REAL NULL,
                z REAL NULL,
                arrival_distance_ls REAL NULL,
                surface_density REAL NULL,
                linear_density REAL NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE rings_scored (
                ring_id TEXT PRIMARY KEY,
                score_version TEXT NOT NULL,
                moi_final REAL NULL,
                ssd_score REAL NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO rings_raw (
                ring_id, system_name, body_name, ring_name, ring_type,
                x, y, z, arrival_distance_ls,
                surface_density, linear_density
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("r1", "S1", "B1", "R1", "Metallic", 1.0, 0.0, 0.0, 200.0, 10.0, 20.0),
                ("r2", "S2", "B2", "R2", "Metallic", 3.0, 0.0, 0.0, 100.0, 11.0, 21.0),
                ("r3", "S3", "B3", "R3", "Icy", 2.0, 0.0, 0.0, 150.0, 12.0, 22.0),
                ("r4", "S4", "B4", "R4", "Icy", 0.0, 2.0, 0.0, 80.0, 13.0, 23.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO rings_scored (ring_id, score_version, moi_final, ssd_score)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("r1", "moi_v1", 95.0, None),
                ("r2", "moi_v1", 92.0, None),
            ],
        )
        conn.commit()


class RingHunterReportsTests(unittest.TestCase):
    def test_export_top_metallic_uses_rings_scored_order(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "rings_master.sqlite"
            out_dir = tmp_path / "out"
            _create_fixture_db(db_path)

            exit_code = ring_hunter_reports.main(
                [
                    "--db",
                    str(db_path),
                    "--out",
                    str(out_dir),
                    "--top",
                    "2",
                    "--quiet",
                    "export-top-metallic",
                ]
            )
            self.assertEqual(exit_code, 0)

            csv_path = out_dir / "top_metallic.csv"
            self.assertTrue(csv_path.exists())
            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["ring_id"], "r1")
            self.assertEqual(rows[1]["ring_id"], "r2")

    def test_export_top_icy_ssd_errors_when_scores_missing(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "rings_master.sqlite"
            out_dir = tmp_path / "out"
            _create_fixture_db(db_path)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = ring_hunter_reports.main(
                    [
                        "--db",
                        str(db_path),
                        "--out",
                        str(out_dir),
                        "--top",
                        "2",
                        "export-top-icy-ssd",
                    ]
                )

            self.assertNotEqual(exit_code, 0)
            self.assertIn("SSD scores not found for score_version=moi_v1", stdout.getvalue())
            self.assertFalse((out_dir / "top_icy_ssd.csv").exists())


if __name__ == "__main__":
    unittest.main()
