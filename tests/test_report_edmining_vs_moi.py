import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.report_edmining_vs_moi import find_candidate_rings, normalize_planet_tokens, write_report


def _create_base_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE known_sites_edmining (
            source_url TEXT PRIMARY KEY,
            system_name TEXT,
            planets TEXT
        );
        CREATE TABLE rings_raw (
            ring_id TEXT PRIMARY KEY,
            system_name TEXT,
            body_name TEXT,
            ring_name TEXT
        );
        CREATE TABLE rings_scored (
            ring_id TEXT,
            score_version TEXT,
            ring_type TEXT,
            moi_final REAL
        );
        CREATE TABLE cohort_members (
            ring_id TEXT,
            score_version TEXT,
            cohort_name TEXT
        );
        """
    )


class ReportEDMiningVsMOITests(unittest.TestCase):
    def test_planet_token_spacing_matches_body_name(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "crosswalk.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE rings_raw (
                        ring_id TEXT PRIMARY KEY,
                        system_name TEXT,
                        body_name TEXT,
                        ring_name TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name)
                    VALUES ('r1', 'X', 'X AB 2', 'X AB 2 Ring')
                    """
                )
                conn.commit()

                tokens = normalize_planet_tokens("AB2 and AB3 rings")
                self.assertIn("ab 2", tokens)
                matches = find_candidate_rings(conn, "x", tokens)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].ring_id, "r1")
            finally:
                conn.close()

    def test_numeric_token_boundary_does_not_match_13(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "crosswalk.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE rings_raw (
                        ring_id TEXT PRIMARY KEY,
                        system_name TEXT,
                        body_name TEXT,
                        ring_name TEXT
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        ("r3", "X", "X AB 3", "X AB 3 Ring"),
                        ("r13", "X", "X AB 13", "X AB 13 Ring"),
                    ],
                )
                conn.commit()

                tokens = normalize_planet_tokens("3 rings")
                self.assertEqual(tokens, ["3"])
                matches = find_candidate_rings(conn, "x", tokens)
                self.assertEqual([m.ring_id for m in matches], ["r3"])
            finally:
                conn.close()

    def test_percentile_uses_sql_counts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "crosswalk.sqlite"
            out_path = Path(tmp) / "report.md"
            conn = sqlite3.connect(db_path)
            try:
                _create_base_tables(conn)
                conn.execute(
                    "INSERT INTO known_sites_edmining (source_url, system_name, planets) VALUES (?, ?, ?)",
                    ("u1", "X", "AB2 rings"),
                )
                conn.execute(
                    "INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name) VALUES (?, ?, ?, ?)",
                    ("r2", "X", "X AB 2", "X AB 2 Ring"),
                )
                conn.executemany(
                    "INSERT INTO rings_scored (ring_id, score_version, ring_type, moi_final) VALUES (?, ?, ?, ?)",
                    [
                        ("r1", "moi_v1", "Metallic", 10.0),
                        ("r2", "moi_v1", "Metallic", 20.0),
                        ("r3", "moi_v1", "Metallic", 30.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            write_report(db_path, "moi_v1", out_path)
            text = out_path.read_text(encoding="utf-8")
            self.assertIn(
                "| X | AB2 rings | MATCHED | 1 | r2 | Metallic | 20.000000 | 0.666667 | no | r2:20.000000:Metallic |",
                text,
            )

    def test_multiple_candidates_pick_best_deterministically(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "crosswalk.sqlite"
            out_path = Path(tmp) / "report.md"
            conn = sqlite3.connect(db_path)
            try:
                _create_base_tables(conn)
                conn.execute(
                    "INSERT INTO known_sites_edmining (source_url, system_name, planets) VALUES (?, ?, ?)",
                    ("u1", "X", "AB3 rings"),
                )
                conn.executemany(
                    "INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name) VALUES (?, ?, ?, ?)",
                    [
                        ("m1", "X", "X AB 3", "X AB 3 Ring"),
                        ("i1", "X", "X AB 3", "X AB 3 Ring"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO rings_scored (ring_id, score_version, ring_type, moi_final) VALUES (?, ?, ?, ?)",
                    [
                        ("m1", "moi_v1", "Metallic", 99.0),
                        ("i1", "moi_v1", "Icy", 10.0),
                    ],
                )
                conn.execute(
                    "INSERT INTO cohort_members (ring_id, score_version, cohort_name) VALUES (?, ?, ?)",
                    ("i1", "moi_v1", "IcyCore"),
                )
                conn.commit()
            finally:
                conn.close()

            write_report(db_path, "moi_v1", out_path)
            text = out_path.read_text(encoding="utf-8")
            self.assertIn("| X | AB3 rings | MATCHED_MULTIPLE | 2 | i1 | Icy | 10.000000 | 1.000000 | yes |", text)
            self.assertIn("i1:10.000000:Icy; m1:99.000000:Metallic", text)

    def test_write_db_and_csv(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "crosswalk.sqlite"
            out_path = Path(tmp) / "report.md"
            csv_path = Path(tmp) / "report.csv"
            conn = sqlite3.connect(db_path)
            try:
                _create_base_tables(conn)
                conn.executemany(
                    "INSERT INTO known_sites_edmining (source_url, system_name, planets) VALUES (?, ?, ?)",
                    [
                        ("u2", "Y", "AB2 rings"),
                        ("u1", "X", "AB1 rings"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name) VALUES (?, ?, ?, ?)",
                    [
                        ("r1", "X", "X AB 1", "X AB 1 Ring"),
                        ("r2", "Y", "Y AB 2", "Y AB 2 Ring"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO rings_scored (ring_id, score_version, ring_type, moi_final) VALUES (?, ?, ?, ?)",
                    [
                        ("r1", "moi_v1", "Icy", 11.0),
                        ("r2", "moi_v1", "Icy", 12.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            write_report(db_path, "moi_v1", out_path, write_db=True, out_csv=csv_path)

            conn = sqlite3.connect(db_path)
            try:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(edmining_best_matches)").fetchall()
                }
                self.assertIn("source_url", columns)
                self.assertIn("best_ring_id", columns)
                count = conn.execute("SELECT COUNT(*) FROM edmining_best_matches").fetchone()[0]
                self.assertEqual(count, 2)
            finally:
                conn.close()

            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["system_name"] for row in rows], ["X", "Y"])


if __name__ == "__main__":
    unittest.main()
