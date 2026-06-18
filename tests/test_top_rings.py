import json
import gc
import sqlite3
import tempfile
import unittest
import warnings
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import top_rings
from contextlib import closing

warnings.filterwarnings("ignore", category=ResourceWarning)


def _close_tracked_connections(tracked: list[sqlite3.Connection]) -> None:
    seen: set[int] = set()
    for conn in reversed(tracked):
        conn_id = id(conn)
        if conn_id in seen:
            continue
        seen.add(conn_id)
        try:
            if conn.in_transaction:
                conn.rollback()
        except Exception:
            pass
        try:
            sqlite3.Connection.close(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


def _run_top_rings_closed(args: list[str]) -> int:
    tracked: list[sqlite3.Connection] = []
    original_connect = top_rings.sqlite3.connect

    def _tracked_connect(*c_args, **c_kwargs):
        conn = original_connect(*c_args, **c_kwargs)
        tracked.append(conn)
        return conn

    try:
        with patch.object(top_rings.sqlite3, "connect", side_effect=_tracked_connect):
            return top_rings.main(args)
    finally:
        _close_tracked_connections(tracked)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()


def create_sector_db(path: Path) -> None:
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute(
            """
            CREATE TABLE systems (
                system_key TEXT PRIMARY KEY,
                name TEXT,
                x REAL,
                y REAL,
                z REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bodies (
                system_key TEXT,
                body_key TEXT,
                system_name TEXT,
                body_name TEXT,
                is_mapped INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE rings (
                system_key TEXT,
                system_name TEXT,
                body_name TEXT,
                ring_name TEXT,
                ring_class TEXT,
                mass_mt REAL,
                inner_rad REAL,
                outer_rad REAL
            )
            """
        )
        systems = [
            ("addr:1", "TestSector A", 1.0, 0.0, 0.0),
            ("addr:2", "TestSector B", 10.0, 0.0, 0.0),
            ("addr:3", "Eotchorts FG-X d1-318", 0.0, 0.0, 0.0),
        ]
        conn.executemany(
            "INSERT INTO systems (system_key, name, x, y, z) VALUES (?, ?, ?, ?, ?)",
            systems,
        )
        bodies = [
            ("addr:1", "body:1", "TestSector A", "TestSector A 1", 0),
            ("addr:2", "body:2", "TestSector B", "TestSector B 1", 1),
        ]
        conn.executemany(
            "INSERT INTO bodies (system_key, body_key, system_name, body_name, is_mapped) VALUES (?, ?, ?, ?, ?)",
            bodies,
        )

        rings = [
            ("addr:1", "TestSector A", "TestSector A 1", "A Icy 1", "eRingClass_Icy", 80, 1, 3),
            ("addr:1", "TestSector A", "TestSector A 1", "A Icy 2", "eRingClass_Icy", 60, 1, 3),
            ("addr:1", "TestSector A", "TestSector A 1", "A Metallic 1", "eRingClass_Metallic", 90, 1, 3),
            ("addr:1", "TestSector A", "TestSector A 1", "A Metallic 2", "eRingClass_Metalic", 70, 1, 3),
            ("addr:2", "TestSector B", "TestSector B 1", "B MetalRich 1", "Metal Rich", 85, 1, 3),
            ("addr:2", "TestSector B", "TestSector B 1", "B MetalRich 2", "eRingClass_MetalRich", 65, 1, 3),
            ("addr:2", "TestSector B", "TestSector B 1", "B Rocky 1", "eRingClass_Rocky", 50, 1, 3),
            ("addr:2", "TestSector B", "TestSector B 1", "B Rocky 2", "eRingClass_Rocky", 40, 1, 3),
        ]
        conn.executemany(
            """
            INSERT INTO rings (
                system_key, system_name, body_name, ring_name, ring_class, mass_mt, inner_rad, outer_rad
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rings,
        )
        conn.commit()


def create_sector_db_int_keys(path: Path) -> None:
    """Create a sector DB with INTEGER primary keys (common in real sector DBs)."""
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute(
        """
        CREATE TABLE systems (
            system_id INTEGER PRIMARY KEY,
            name TEXT,
            x REAL,
            y REAL,
            z REAL
        )
        """
    )
        conn.execute(
        """
        CREATE TABLE bodies (
            body_id INTEGER PRIMARY KEY,
            system_id INTEGER,
            body_name TEXT,
            system_name TEXT
        )
        """
    )
        conn.execute(
        """
        CREATE TABLE rings (
            system_id INTEGER,
            body_id INTEGER,
            ring_name TEXT,
            ring_class TEXT,
            mass_mt REAL,
            inner_rad REAL,
            outer_rad REAL
        )
        """
    )
        conn.executemany(
        "INSERT INTO systems (system_id, name, x, y, z) VALUES (?, ?, ?, ?, ?)",
        [
            (100, "IntSector A", 5.0, 0.0, 0.0),
            (200, "Eotchorts FG-X d1-318", 0.0, 0.0, 0.0),
        ],
    )
        conn.executemany(
        "INSERT INTO bodies (body_id, system_id, body_name, system_name) VALUES (?, ?, ?, ?)",
        [
            (1001, 100, "IntSector A 1", "IntSector A"),
        ],
    )
        conn.executemany(
        """INSERT INTO rings (system_id, body_id, ring_name, ring_class, mass_mt, inner_rad, outer_rad)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (100, 1001, "Int Ring Icy", "eRingClass_Icy", 90, 1, 3),
        ],
    )
        conn.commit()


def create_journal(path: Path) -> None:
    events = [
        {
            "timestamp": "2026-01-02T00:00:00Z",
            "event": "Scan",
            "StarSystem": "TestSector A",
            "BodyName": "TestSector A 1",
            "Rings": [
                {
                    "Name": "A Icy Journal",
                    "RingClass": "eRingClass_Icy",
                    "MassMT": 120,
                    "InnerRad": 1,
                    "OuterRad": 3,
                }
            ],
        },
        {
            "timestamp": "2026-01-02T00:00:10Z",
            "event": "SAAScanComplete",
            "StarSystem": "TestSector A",
            "BodyName": "TestSector A 1",
        },
        {
            "timestamp": "2026-01-02T00:00:20Z",
            "event": "Scan",
            "StarSystem": "Other Sector",
            "BodyName": "Other 1",
            "Rings": [
                {
                    "Name": "Ignore Ring",
                    "RingClass": "eRingClass_Icy",
                    "MassMT": 999,
                    "InnerRad": 1,
                    "OuterRad": 3,
                }
            ],
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event))
            handle.write("\n")


class TestTopRings(unittest.TestCase):
    def test_journal_replaces_top10_and_sets_mapped(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)
            sector_dir = temp_path / "sector_library"
            sector_dir.mkdir(parents=True, exist_ok=True)
            sector_db = sector_dir / "sector_testsector.sqlite"
            create_sector_db(sector_db)

            journals_dir = temp_path / "journals"
            journals_dir.mkdir(parents=True, exist_ok=True)
            journal_path = journals_dir / "Journal.2026-01-02.log"
            create_journal(journal_path)

            export_path = temp_path / "export.csv"
            exit_code = _run_top_rings_closed(
                [
                    "--sector",
                    "testsector",
                    "--sector-library-dir",
                    str(sector_dir),
                    "--journals-dir",
                    str(journals_dir),
                    "--since",
                    "2026-01-01",
                    "--limit",
                    "2",
                    "--export-csv",
                    str(export_path),
                    "--quiet",
                ]
            )
            self.assertEqual(exit_code, 0)

            output_db = sector_dir / "top_rings_testsector.sqlite"
            self.assertTrue(output_db.exists())
            with closing(sqlite3.connect(str(output_db))) as conn:
                rows = conn.execute(
                    """
                    SELECT ring_name, mapped_journal, mapped_db, mapped_final, source
                    FROM top_rings_ranked
                    WHERE category = ?
                    ORDER BY rank ASC
                    """,
                    (top_rings.CATEGORY_ICY,),
                ).fetchall()

            ring_names = [row[0] for row in rows]
            self.assertIn("A Icy Journal", ring_names)
            for name, mapped_journal, mapped_db, mapped_final, source in rows:
                if name == "A Icy Journal":
                    self.assertEqual(mapped_journal, 1)
                    self.assertEqual(mapped_final, 1)
                    self.assertEqual(source, "journal")

            with closing(sqlite3.connect(str(output_db))) as conn:
                rows = conn.execute(
                    """
                    SELECT ring_name, mapped_db, mapped_final
                    FROM top_rings_ranked
                    WHERE category = ?
                    ORDER BY rank ASC
                    """,
                    (top_rings.CATEGORY_METAL_RICH,),
                ).fetchall()
            for name, mapped_db, mapped_final in rows:
                if "MetalRich" in name:
                    self.assertEqual(mapped_db, 1)
                    self.assertEqual(mapped_final, 1)

            self.assertTrue(export_path.exists())
            import csv

            with export_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                csv_rows = [row for row in reader if row]
            self.assertGreaterEqual(len(csv_rows), 2)
            with closing(sqlite3.connect(str(output_db))) as conn:
                db_rows = conn.execute(
                    """
                    SELECT ring_name
                    FROM top_rings_ranked
                    ORDER BY category ASC, rank ASC
                    """
                ).fetchall()
            csv_ring_names = [row[5] for row in csv_rows[1:]]
            db_ring_names = [row[0] for row in db_rows]
            self.assertEqual(csv_ring_names, db_ring_names)


class TestCategoryNormalization(unittest.TestCase):
    def test_category_variants(self) -> None:
        self.assertEqual(top_rings.classify_ring("MetalRich"), top_rings.CATEGORY_METAL_RICH)
        self.assertEqual(top_rings.classify_ring("Metal Rich"), top_rings.CATEGORY_METAL_RICH)
        self.assertEqual(top_rings.classify_ring("eRingClass_MetalRich"), top_rings.CATEGORY_METAL_RICH)
        self.assertEqual(top_rings.classify_ring("metal-rich"), top_rings.CATEGORY_METAL_RICH)
        self.assertEqual(top_rings.classify_ring("eRingClass_Icy"), top_rings.CATEGORY_ICY)
        self.assertEqual(top_rings.classify_ring("eRingClass_Metalic"), top_rings.CATEGORY_METALLIC)
        self.assertEqual(top_rings.classify_ring("eRingClass_Rocky"), top_rings.CATEGORY_ROCKY)

    def test_classify_none_and_empty(self) -> None:
        self.assertIsNone(top_rings.classify_ring(None))
        self.assertIsNone(top_rings.classify_ring(""))
        self.assertIsNone(top_rings.classify_ring("   "))

    def test_classify_unknown_returns_none(self) -> None:
        self.assertIsNone(top_rings.classify_ring("eRingClass_SomethingNew"))
        self.assertIsNone(top_rings.classify_ring("Water"))


class TestTieBreaker(unittest.TestCase):
    def test_distance_tie_breaker(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)
            sector_dir = temp_path / "sector_library"
            sector_dir.mkdir(parents=True, exist_ok=True)
            sector_db = sector_dir / "sector_testsector.sqlite"
            create_sector_db(sector_db)
            with closing(sqlite3.connect(str(sector_db))) as conn:
                conn.execute(
                    """
                    INSERT INTO rings (
                        system_key, system_name, body_name, ring_name, ring_class, mass_mt, inner_rad, outer_rad
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "addr:2",
                        "TestSector B",
                        "TestSector B 1",
                        "B Icy Tie",
                        "eRingClass_Icy",
                        80,
                        1,
                        3,
                    ),
                )
                conn.commit()

            journals_dir = temp_path / "journals"
            journals_dir.mkdir(parents=True, exist_ok=True)

            exit_code = _run_top_rings_closed(
                [
                    "--sector",
                    "testsector",
                    "--sector-library-dir",
                    str(sector_dir),
                    "--journals-dir",
                    str(journals_dir),
                    "--since",
                    "2026-01-01",
                    "--limit",
                    "1",
                    "--quiet",
                ]
            )
            self.assertEqual(exit_code, 0)

            output_db = sector_dir / "top_rings_testsector.sqlite"
            with closing(sqlite3.connect(str(output_db))) as conn:
                rows = conn.execute(
                    """
                    SELECT ring_name, distance_to_anchor_ly
                    FROM top_rings_ranked
                    WHERE category = ?
                    ORDER BY rank ASC
                    """,
                    (top_rings.CATEGORY_ICY,),
                ).fetchall()
            self.assertEqual(len(rows), 1)
            ring_name, distance_ly = rows[0]
            self.assertEqual(ring_name, "A Icy 1")
            self.assertLess(distance_ly, 5.0)


class TestIntegerKeys(unittest.TestCase):
    """Verify that sector DBs with INTEGER primary keys resolve correctly."""

    def test_integer_system_and_body_keys(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)
            sector_dir = temp_path / "sector_library"
            sector_dir.mkdir(parents=True, exist_ok=True)
            sector_db = sector_dir / "sector_intsector.sqlite"
            create_sector_db_int_keys(sector_db)

            journals_dir = temp_path / "journals"
            journals_dir.mkdir(parents=True, exist_ok=True)

            exit_code = _run_top_rings_closed(
                [
                    "--sector",
                    "intsector",
                    "--sector-library-dir",
                    str(sector_dir),
                    "--journals-dir",
                    str(journals_dir),
                    "--since",
                    "2026-01-01",
                    "--limit",
                    "5",
                    "--quiet",
                ]
            )
            self.assertEqual(exit_code, 0)

            output_db = sector_dir / "top_rings_intsector.sqlite"
            with closing(sqlite3.connect(str(output_db))) as conn:
                rows = conn.execute(
                    """
                    SELECT ring_name, system_name, body_name
                    FROM top_rings_ranked
                    WHERE category = ?
                    """,
                    (top_rings.CATEGORY_ICY,),
                ).fetchall()

            self.assertEqual(len(rows), 1)
            ring_name, system_name, body_name = rows[0]
            self.assertEqual(ring_name, "Int Ring Icy")
            self.assertEqual(system_name, "IntSector A")
            self.assertEqual(body_name, "IntSector A 1")


class TestParseSince(unittest.TestCase):
    def test_valid_date(self) -> None:
        dt = top_rings.parse_since("2026-01-15")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 15)

    def test_valid_datetime_z(self) -> None:
        dt = top_rings.parse_since("2026-01-15T12:30:00Z")
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.hour, 12)

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            top_rings.parse_since("not-a-date")

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            top_rings.parse_since("")


class TestLimitValidation(unittest.TestCase):
    def test_limit_zero_rejected(self) -> None:
        result = _run_top_rings_closed(["--sector", "x", "--limit", "0", "--quiet"])
        self.assertEqual(result, 1)

    def test_limit_negative_rejected(self) -> None:
        result = _run_top_rings_closed(["--sector", "x", "--limit", "-5", "--quiet"])
        self.assertEqual(result, 1)


class TestJournalFileDateFilter(unittest.TestCase):
    def test_standard_format(self) -> None:
        self.assertEqual(
            top_rings.journal_file_date("Journal.2026-01-15T123456.01.log"),
            "2026-01-15",
        )

    def test_legacy_format(self) -> None:
        self.assertEqual(
            top_rings.journal_file_date("Journal.220207184508.01.log"),
            "2022-02-07",
        )

    def test_unrecognised_returns_none(self) -> None:
        self.assertIsNone(top_rings.journal_file_date("Journal..log"))

    def test_old_files_skipped(self) -> None:
        """Verify that iter_journal_files skips files predating since."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)
            # Create two journal files: one old, one recent
            old = temp_path / "Journal.2020-01-01T000000.01.log"
            old.write_text("{}\n", encoding="utf-8")
            recent = temp_path / "Journal.2026-02-01T000000.01.log"
            recent.write_text("{}\n", encoding="utf-8")

            since = datetime(2026, 1, 1, tzinfo=timezone.utc)
            files = list(top_rings.iter_journal_files(temp_path, since))
            names = [f.name for f in files]
            self.assertNotIn("Journal.2020-01-01T000000.01.log", names)
            self.assertIn("Journal.2026-02-01T000000.01.log", names)

    def test_no_since_returns_all(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)
            f1 = temp_path / "Journal.2020-01-01T000000.01.log"
            f1.write_text("{}\n", encoding="utf-8")
            f2 = temp_path / "Journal.2026-02-01T000000.01.log"
            f2.write_text("{}\n", encoding="utf-8")

            files = list(top_rings.iter_journal_files(temp_path, None))
            self.assertEqual(len(files), 2)


class TestAnchorOverride(unittest.TestCase):
    def test_anchor_coords_override(self) -> None:
        """Verify --anchor-coords works when anchor system is absent from DB."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)
            sector_dir = temp_path / "sector_library"
            sector_dir.mkdir(parents=True, exist_ok=True)
            sector_db = sector_dir / "sector_noanchor.sqlite"

            # Create a sector DB WITHOUT the default anchor system
            with closing(sqlite3.connect(str(sector_db))) as conn:
                conn.execute(
                    "CREATE TABLE systems (system_key TEXT PRIMARY KEY, name TEXT, x REAL, y REAL, z REAL)"
                )
                conn.execute(
                    "CREATE TABLE rings (system_key TEXT, system_name TEXT, body_name TEXT, "
                    "ring_name TEXT, ring_class TEXT, mass_mt REAL, inner_rad REAL, outer_rad REAL)"
                )
                conn.execute(
                    "INSERT INTO systems VALUES (?, ?, ?, ?, ?)",
                    ("addr:1", "MySector A", 10.0, 0.0, 0.0),
                )
                conn.execute(
                    "INSERT INTO rings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("addr:1", "MySector A", "MySector A 1", "Ring1", "eRingClass_Icy", 80, 1, 3),
                )
                conn.commit()

            journals_dir = temp_path / "journals"
            journals_dir.mkdir(parents=True, exist_ok=True)

            # Without override, should fail (no anchor system)
            exit_code = _run_top_rings_closed(
                [
                    "--sector", "noanchor",
                    "--sector-library-dir", str(sector_dir),
                    "--journals-dir", str(journals_dir),
                    "--since", "2026-01-01",
                    "--quiet",
                ]
            )
            self.assertEqual(exit_code, 1)

            # With --anchor-coords override, should succeed
            exit_code = _run_top_rings_closed(
                [
                    "--sector", "noanchor",
                    "--sector-library-dir", str(sector_dir),
                    "--journals-dir", str(journals_dir),
                    "--since", "2026-01-01",
                    "--anchor-coords", "0.0,0.0,0.0",
                    "--quiet",
                ]
            )
            self.assertEqual(exit_code, 0)

            output_db = sector_dir / "top_rings_noanchor.sqlite"
            with closing(sqlite3.connect(str(output_db))) as conn:
                rows = conn.execute(
                    "SELECT ring_name FROM top_rings_ranked WHERE category = ?",
                    (top_rings.CATEGORY_ICY,),
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "Ring1")

    def test_anchor_coords_bad_format(self) -> None:
        result = _run_top_rings_closed(
            ["--sector", "x", "--anchor-coords", "bad", "--quiet"]
        )
        self.assertEqual(result, 1)


class TestRunMetadata(unittest.TestCase):
    def test_run_metadata_written(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)
            sector_dir = temp_path / "sector_library"
            sector_dir.mkdir(parents=True, exist_ok=True)
            sector_db = sector_dir / "sector_testsector.sqlite"
            create_sector_db(sector_db)

            journals_dir = temp_path / "journals"
            journals_dir.mkdir(parents=True, exist_ok=True)

            exit_code = _run_top_rings_closed(
                [
                    "--sector", "testsector",
                    "--sector-library-dir", str(sector_dir),
                    "--journals-dir", str(journals_dir),
                    "--since", "2026-01-01",
                    "--quiet",
                ]
            )
            self.assertEqual(exit_code, 0)

            output_db = sector_dir / "top_rings_testsector.sqlite"
            with closing(sqlite3.connect(str(output_db))) as conn:
                rows = conn.execute(
                    "SELECT key, value FROM run_metadata"
                ).fetchall()

            metadata = dict(rows)
            self.assertEqual(metadata["sector"], "testsector")
            self.assertEqual(metadata["since"], "2026-01-01")
            self.assertEqual(metadata["limit"], "10")
            self.assertIn("run_timestamp", metadata)
            self.assertIn("anchor_system", metadata)
            self.assertIn("anchor_x", metadata)


class TestRingSurfaceArea(unittest.TestCase):
    def test_valid_area(self) -> None:
        import math
        area = top_rings.ring_surface_area(1.0, 3.0)
        expected = math.pi * (9.0 - 1.0)
        self.assertAlmostEqual(area, expected, places=6)

    def test_invalid_radii_returns_zero(self) -> None:
        self.assertEqual(top_rings.ring_surface_area(0, 3), 0.0)
        self.assertEqual(top_rings.ring_surface_area(3, 1), 0.0)
        self.assertEqual(top_rings.ring_surface_area(-1, 3), 0.0)
        self.assertEqual(top_rings.ring_surface_area(3, 3), 0.0)


class TestRingDensity(unittest.TestCase):
    def test_valid_density(self) -> None:
        self.assertAlmostEqual(top_rings.ring_density(100.0, 50.0), 2.0)

    def test_invalid_returns_zero(self) -> None:
        self.assertEqual(top_rings.ring_density(0, 50), 0.0)
        self.assertEqual(top_rings.ring_density(100, 0), 0.0)
        self.assertEqual(top_rings.ring_density(-1, 50), 0.0)


class TestNormalizeFunctions(unittest.TestCase):
    def test_normalize_name(self) -> None:
        self.assertEqual(top_rings.normalize_name("  Hello  World  "), "hello world")
        self.assertEqual(top_rings.normalize_name(None), "")
        self.assertEqual(top_rings.normalize_name(""), "")

    def test_normalize_identifier(self) -> None:
        self.assertEqual(top_rings.normalize_identifier("System_Name"), "systemname")
        self.assertEqual(top_rings.normalize_identifier("ring-class"), "ringclass")

    def test_sanitize_sector(self) -> None:
        self.assertEqual(top_rings.sanitize_sector("  my sector  "), "my_sector")
        self.assertEqual(top_rings.sanitize_sector("a/b\\c"), "abc")
        self.assertEqual(top_rings.sanitize_sector(""), "sector")


class TestMappingValueToBool(unittest.TestCase):
    def test_truthy_values(self) -> None:
        for val in [True, 1, 1.0, "1", "yes", "YES", "true", "mapped", "complete", "Completed"]:
            self.assertTrue(top_rings.mapping_value_to_bool(val), f"Expected True for {val!r}")

    def test_falsy_values(self) -> None:
        for val in [None, False, 0, 0.0, "0", "no", "false", "unmapped", "none"]:
            self.assertFalse(top_rings.mapping_value_to_bool(val), f"Expected False for {val!r}")

    def test_ambiguous_defaults_false(self) -> None:
        self.assertFalse(top_rings.mapping_value_to_bool("maybe"))
        self.assertFalse(top_rings.mapping_value_to_bool(""))


if __name__ == "__main__":
    unittest.main()

