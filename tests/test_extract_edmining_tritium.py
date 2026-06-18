import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.rings.extract_edmining_tritium import (
    build_parser,
    apply_tritium_flags,
    save_debug_artifacts,
    crawl_listing_urls,
    load_into_db,
    parse_listing,
    parse_location,
    sort_rows,
    write_rows_csv,
)


FIXTURES = Path("tests/fixtures")


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ExtractEDMiningTritiumTests(unittest.TestCase):
    def test_debug_args_parse(self) -> None:
        args = build_parser().parse_args(
            [
                "--debug-one-url",
                "https://edmining.com/mining-location/alpha-system/",
                "--debug-save-html",
                "tmp/debug",
                "--debug-save-n",
                "3",
                "--debug-dump-labels",
            ]
        )
        self.assertEqual(args.debug_one_url, "https://edmining.com/mining-location/alpha-system/")
        self.assertEqual(args.debug_save_html, "tmp/debug")
        self.assertEqual(args.debug_save_n, 3)
        self.assertTrue(args.debug_dump_labels)

    def test_debug_save_writes_html_and_json(self) -> None:
        html = _read_fixture("edmining_location_tritium.html")
        row = apply_tritium_flags(parse_location(html, "https://edmining.com/mining-location/alpha-system/"))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out_dir = Path(tmp) / "debug"
            html_path, json_path = save_debug_artifacts(
                out_dir=out_dir,
                source_url=row["source_url"],
                html=html,
                parsed_row=row,
            )
            self.assertTrue(html_path.exists())
            self.assertTrue(json_path.exists())
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["source_url"], row["source_url"])
            self.assertEqual(data["system_name"], "Alpha System")

    def test_parse_listing_extracts_location_urls_and_dedupes(self) -> None:
        html = _read_fixture("edmining_listing_page_1.html")
        urls = parse_listing(html, "https://edmining.com/location-material-type/tritium/")
        self.assertEqual(
            urls,
            [
                "https://edmining.com/mining-location/alpha-system/",
                "https://edmining.com/mining-location/bravo-system/",
            ],
        )

    def test_parse_location_extracts_expected_fields(self) -> None:
        html = _read_fixture("edmining_location_tritium.html")
        row = apply_tritium_flags(parse_location(html, "https://edmining.com/mining-location/alpha-system/"))
        self.assertEqual(row["system_name"], "Alpha System")
        self.assertEqual(row["source_category"], "tritium")
        self.assertEqual(row["planets"], "Alpha A 2 Rings")
        self.assertEqual(row["estimated_ly_from_bubble"], 1234.5)
        self.assertEqual(row["mining_type"], "Laser Mining")
        self.assertEqual(row["overlap"], "2")
        self.assertEqual(row["thanks_to"], "CMDR Example")
        self.assertEqual(row["material_type_raw"], "Tritium, Platinum")
        self.assertEqual(row["materials"], ["Tritium", "Platinum"])
        self.assertEqual(row["tritium_explicit"], 1)
        self.assertEqual(row["tritium_inferred"], 0)
        self.assertIn("Excellent overlap", row["description"])

    def test_tritium_flags_explicit_and_inferred(self) -> None:
        tritium_row = apply_tritium_flags(parse_location(
            _read_fixture("edmining_location_tritium.html"),
            "https://edmining.com/mining-location/alpha-system/",
        ))
        other_row = apply_tritium_flags(parse_location(
            _read_fixture("edmining_location_missing_material.html"),
            "https://edmining.com/mining-location/echo-system/",
        ))
        self.assertEqual(tritium_row["tritium_explicit"], 1)
        self.assertEqual(tritium_row["tritium_inferred"], 0)
        self.assertEqual(other_row["tritium_explicit"], 0)
        self.assertEqual(other_row["tritium_inferred"], 1)
        self.assertIn("materials_missing", other_row["parse_warnings"])

    def test_non_tritium_detail_still_inferred(self) -> None:
        other_row = apply_tritium_flags(parse_location(
            _read_fixture("edmining_location_non_tritium.html"),
            "https://edmining.com/mining-location/bravo-system/",
        ))
        self.assertEqual(other_row["tritium_explicit"], 0)
        self.assertEqual(other_row["tritium_inferred"], 1)

    def test_bhotho_like_block_text_parsing(self) -> None:
        row = parse_location(
            _read_fixture("edmining_location_bhotho_like.html"),
            "https://edmining.com/mining-location/bhotho/",
        )
        self.assertEqual(row["planets"], "AB2 and AB3 rings")
        self.assertIn("Tritium", row["materials"])
        self.assertEqual(row["mining_type"], "Laser")
        self.assertIn("Double Overlap", row["overlap"] or "")
        self.assertIn("S-BURLING15", row["thanks_to"] or "")
        self.assertEqual(row["tritium_explicit"], 1)
        self.assertEqual(row["tritium_inferred"], 0)

    def test_csv_sorting_is_deterministic(self) -> None:
        rows = [
            {
                "source_url": "https://edmining.com/mining-location/zeta/",
                "source_category": "tritium",
                "system_name": "Zeta",
                "planets": "A 2",
                "estimated_ly_from_bubble": 10.0,
                "mining_type": "Laser",
                "overlap": "1",
                "thanks_to": "A",
                "tritium_explicit": 1,
                "tritium_inferred": 0,
                "parse_warnings": None,
                "material_type_raw": "Tritium",
                "materials": ["Tritium"],
                "description": "Desc",
                "extracted_at_utc": "2026-01-01T00:00:00+00:00",
            },
            {
                "source_url": "https://edmining.com/mining-location/alpha/",
                "source_category": "tritium",
                "system_name": "Alpha",
                "planets": "B 1",
                "estimated_ly_from_bubble": 20.0,
                "mining_type": "Core",
                "overlap": "2",
                "thanks_to": "B",
                "tritium_explicit": 1,
                "tritium_inferred": 0,
                "parse_warnings": None,
                "material_type_raw": "Tritium, Platinum",
                "materials": ["Tritium", "Platinum"],
                "description": "Desc2",
                "extracted_at_utc": "2026-01-01T00:00:00+00:00",
            },
        ]
        sorted_rows = sort_rows(rows)
        self.assertEqual([r["system_name"] for r in sorted_rows], ["Alpha", "Zeta"])

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out_csv = Path(tmp) / "out.csv"
            write_rows_csv(rows, out_csv)
            with out_csv.open("r", newline="", encoding="utf-8") as handle:
                parsed = list(csv.DictReader(handle))
            self.assertEqual(parsed[0]["system_name"], "Alpha")
            self.assertEqual(parsed[0]["source_category"], "tritium")
            self.assertEqual(parsed[0]["tritium_explicit"], "1")
            self.assertEqual(json.loads(parsed[0]["materials_json"]), ["Tritium", "Platinum"])

    def test_db_upsert_creates_table_and_inserts_rows(self) -> None:
        rows = [
            apply_tritium_flags(parse_location(
                _read_fixture("edmining_location_tritium.html"),
                "https://edmining.com/mining-location/alpha-system/",
            ))
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "sites.sqlite"
            loaded = load_into_db(db_path, rows)
            self.assertEqual(loaded, 1)
            with closing(sqlite3.connect(db_path)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM known_sites_edmining").fetchone()[0]
                self.assertEqual(count, 1)
                row = conn.execute(
                    "SELECT source_category, tritium_explicit, tritium_inferred FROM known_sites_edmining LIMIT 1"
                ).fetchone()
                self.assertEqual(row, ("tritium", 1, 0))

    def test_db_upsert_migrates_missing_columns(self) -> None:
        rows = [
            apply_tritium_flags(parse_location(
                _read_fixture("edmining_location_missing_material.html"),
                "https://edmining.com/mining-location/echo-system/",
            ))
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "sites.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE known_sites_edmining (
                        source_url TEXT PRIMARY KEY,
                        system_name TEXT NOT NULL,
                        planets TEXT,
                        estimated_ly_from_bubble REAL,
                        mining_type TEXT,
                        overlap TEXT,
                        thanks_to TEXT,
                        material_type_raw TEXT,
                        materials_json TEXT,
                        description TEXT,
                        extracted_at_utc TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

            loaded = load_into_db(db_path, rows)
            self.assertEqual(loaded, 1)
            with closing(sqlite3.connect(db_path)) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(known_sites_edmining)").fetchall()}
                self.assertIn("source_category", cols)
                self.assertIn("tritium_explicit", cols)
                self.assertIn("tritium_inferred", cols)
                self.assertIn("parse_warnings", cols)
                row = conn.execute(
                    "SELECT tritium_explicit, tritium_inferred, parse_warnings FROM known_sites_edmining LIMIT 1"
                ).fetchone()
                self.assertEqual(row[0], 0)
                self.assertEqual(row[1], 1)
                self.assertIn("materials_missing", row[2] or "")

    def test_crawl_listing_stops_gracefully_on_404_pagination(self) -> None:
        page1_url = "https://edmining.com/location-material-type/tritium/"
        page2_url = "https://edmining.com/location-material-type/tritium/page/2/"
        page3_url = "https://edmining.com/location-material-type/tritium/page/3/"
        page1_html = _read_fixture("edmining_listing_page_1.html")
        page2_html = _read_fixture("edmining_listing_page_2.html")

        def _fake_fetch(_session, url, _timeout, _sleep, _verbose):
            if url == page1_url:
                return page1_html
            if url == page2_url:
                return page2_html
            if url == page3_url:
                raise RuntimeError(f"HTTP 404 for {page3_url}")
            raise RuntimeError(f"Unexpected URL: {url}")

        with patch("scripts.rings.extract_edmining_tritium.fetch_with_retries", side_effect=_fake_fetch):
            urls, pages_scraped = crawl_listing_urls(
                session=object(),  # fetch is mocked
                start_url=page1_url,
                max_pages=10,
                sleep_seconds=0.0,
                timeout=20.0,
                verbose=False,
            )

        self.assertEqual(pages_scraped, 2)
        self.assertEqual(
            urls,
            [
                "https://edmining.com/mining-location/alpha-system/",
                "https://edmining.com/mining-location/bravo-system/",
                "https://edmining.com/mining-location/charlie-system/",
                "https://edmining.com/mining-location/delta-system/",
            ],
        )


if __name__ == "__main__":
    unittest.main()
