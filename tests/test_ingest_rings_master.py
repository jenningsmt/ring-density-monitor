import gc
import io
import gzip
import json
import math
import re
import sqlite3
import tempfile
import unittest
import warnings
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.rings import ingest_rings_master

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


def _run_ingest_closed(args: list[str], quiet: bool = True) -> int:
    tracked: list[sqlite3.Connection] = []
    original_connect = ingest_rings_master.sqlite3.connect

    def _tracked_connect(*c_args, **c_kwargs):
        conn = original_connect(*c_args, **c_kwargs)
        tracked.append(conn)
        return conn

    try:
        run_args = list(args)
        if quiet and "--quiet" not in run_args:
            run_args.append("--quiet")
        with patch.object(ingest_rings_master.sqlite3, "connect", side_effect=_tracked_connect):
            return ingest_rings_master.main(run_args)
    finally:
        _close_tracked_connections(tracked)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()


def _sample_system() -> dict:
    return {
        "name": "Test System",
        "id64": 123456789,
        "coords": {"x": 10.0, "y": 20.0, "z": 30.0},
        "bodies": [
            {
                "name": "Test System A 1",
                "bodyId": 5,
                "type": "Planet",
                "subType": "Icy body",
                "distanceToArrival": 500.0,
                "gravity": 0.2,
                "rings": [
                    {
                        "name": "Test Ring A",
                        "ringClass": "eRingClass_Icy",
                        "reserveLevel": "Pristine",
                        "massMT": 100.0,
                        "innerRad": 1.0,
                        "outerRad": 3.0,
                    }
                ],
            }
        ],
    }


def _sample_system_with_rings(system_name: str, body_name: str, ring_count: int) -> dict:
    rings = []
    for i in range(ring_count):
        rings.append(
            {
                "name": f"{body_name} Ring {i+1}",
                "ringClass": "eRingClass_Icy" if i % 2 == 0 else "eRingClass_Metallic",
                "reserveLevel": "Pristine",
                "massMT": 100.0 + i,
                "innerRad": 1.0 + i,
                "outerRad": 3.0 + i,
            }
        )
    return {
        "name": system_name,
        "id64": 111111 + ring_count,
        "coords": {"x": 10.0, "y": 20.0, "z": 30.0},
        "bodies": [
            {
                "name": body_name,
                "bodyId": 7,
                "type": "Planet",
                "subType": "Icy body",
                "distanceToArrival": 100.0,
                "gravity": 0.1,
                "rings": rings,
            }
        ],
    }


class IngestRingsMasterTests(unittest.TestCase):
    def test_resolve_output_db_with_date_suffix(self) -> None:
        resolved = ingest_rings_master.resolve_output_db(
            str(Path("tmp") / "rings_master.sqlite"),
            date_suffix=True,
            date_iso="2026-02-13",
        )
        self.assertEqual(resolved.as_posix(), "tmp/rings_master_2026-02-13.sqlite")

    def test_progress_line_includes_scan_and_rate_fields(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "progress.sqlite"
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(_sample_system()) + "\n")

            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = _run_ingest_closed(
                    [
                        "--input",
                        str(input_path),
                        "--output-db",
                        str(output_path),
                        "--progress-seconds",
                        "1",
                    ],
                    quiet=False,
                )
            self.assertEqual(exit_code, 0)
            output = buf.getvalue()
            m = re.search(
                r"progress systems_scanned=\d+ rings_seen=\d+ rings_inserted=\d+ "
                r"elapsed_seconds=\d+\.\d+ rings_per_sec=\d+\.\d+ systems_per_sec=\d+\.\d+",
                output,
            )
            self.assertIsNotNone(m)

    def test_quiet_mode_suppresses_progress_and_summary_output(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "quiet.sqlite"
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(_sample_system()) + "\n")

            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = _run_ingest_closed(
                    [
                        "--input",
                        str(input_path),
                        "--output-db",
                        str(output_path),
                        "--progress-seconds",
                        "1",
                        "--quiet",
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(buf.getvalue().strip(), "")

    def test_ingest_jsonl_inserts_ring_and_derived_values(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "nested" / "rings_master.sqlite"

            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(_sample_system()) + "\n")

            exit_code = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--commit-every",
                    "1",
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())

            with closing(sqlite3.connect(output_path)) as conn:
                row = conn.execute(
                    """
                    SELECT ring_id, ring_width, ring_area, surface_density, linear_density, raw_ring_json, raw_body_json, raw_system_json
                    FROM rings_raw
                    """
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertGreater(len(row[0]), 0)
                self.assertAlmostEqual(row[1], 2.0, places=6)
                self.assertAlmostEqual(row[2], math.pi * 8.0, places=6)
                self.assertAlmostEqual(row[3], 100.0 / (math.pi * 8.0), places=6)
                self.assertAlmostEqual(row[4], 50.0, places=6)
                self.assertIsNone(row[5])
                self.assertIsNone(row[6])
                self.assertIsNone(row[7])

                survey_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ring_survey'"
                ).fetchone()
                self.assertIsNotNone(survey_exists)
                scored_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rings_scored'"
                ).fetchone()
                self.assertIsNotNone(scored_exists)
                scored_cols = {
                    r[1] for r in conn.execute("PRAGMA table_info(rings_scored)").fetchall()
                }
                self.assertIn("ring_type", scored_cols)
                runs_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='score_runs'"
                ).fetchone()
                self.assertIsNotNone(runs_exists)
                systems_raw_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='systems_raw'"
                ).fetchone()
                self.assertIsNotNone(systems_raw_exists)
                bodies_raw_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bodies_raw'"
                ).fetchone()
                self.assertIsNotNone(bodies_raw_exists)
                rings_payloads_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rings_payloads'"
                ).fetchone()
                self.assertIsNotNone(rings_payloads_exists)
                idx_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_rings_scored_score_version'"
                ).fetchone()
                self.assertIsNotNone(idx_exists)

    def test_ring_id_stable_across_runs_and_json_doc_array_supported(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "ring_hunter_library" / "rings_master.sqlite"
            systems = [_sample_system()]

            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                json.dump(systems, handle)

            first = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--commit-every",
                    "1",
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(first, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                ring_id_1 = conn.execute("SELECT ring_id FROM rings_raw").fetchone()[0]
                count_1 = conn.execute("SELECT COUNT(*) FROM rings_raw").fetchone()[0]

            second = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--commit-every",
                    "1",
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(second, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                ring_id_2 = conn.execute("SELECT ring_id FROM rings_raw").fetchone()[0]
                count_2 = conn.execute("SELECT COUNT(*) FROM rings_raw").fetchone()[0]

            self.assertEqual(ring_id_1, ring_id_2)
            self.assertEqual(count_1, 1)
            self.assertEqual(count_2, 1)

    def test_limit_stops_at_exact_ring_count(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "ring_hunter_library" / "rings_master.sqlite"
            systems = [
                _sample_system_with_rings("Sys A", "Sys A 1", 3),
                _sample_system_with_rings("Sys B", "Sys B 1", 3),
            ]
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                json.dump(systems, handle)

            exit_code = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--limit",
                    "4",
                    "--commit-every",
                    "2",
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM rings_raw").fetchone()[0]
            self.assertEqual(count, 4)

    def test_chunking_does_not_change_deterministic_ring_ids(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_a = tmp_path / "a.sqlite"
            output_b = tmp_path / "b.sqlite"
            systems = [
                _sample_system_with_rings("Sys A", "Sys A 1", 4),
                _sample_system_with_rings("Sys B", "Sys B 1", 4),
            ]
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                json.dump(systems, handle)

            self.assertEqual(
                _run_ingest_closed(
                    [
                        "--input",
                        str(input_path),
                        "--output-db",
                        str(output_a),
                        "--commit-every",
                        "1",
                        "--progress-seconds",
                        "1",
                    ]
                ),
                0,
            )
            self.assertEqual(
                _run_ingest_closed(
                    [
                        "--input",
                        str(input_path),
                        "--output-db",
                        str(output_b),
                        "--commit-every",
                        "5",
                        "--progress-seconds",
                        "1",
                    ]
                ),
                0,
            )

            with closing(sqlite3.connect(output_a)) as conn:
                ids_a = [r[0] for r in conn.execute("SELECT ring_id FROM rings_raw ORDER BY ring_id").fetchall()]
            with closing(sqlite3.connect(output_b)) as conn:
                ids_b = [r[0] for r in conn.execute("SELECT ring_id FROM rings_raw ORDER BY ring_id").fetchall()]
            self.assertEqual(ids_a, ids_b)

    def test_wal_flag_enables_wal_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "wal.sqlite"
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(_sample_system()) + "\n")

            exit_code = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--wal",
                    "--synchronous",
                    "NORMAL",
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                self.skipTest(f"WAL not available on this platform/filesystem (journal_mode={mode})")
            self.assertEqual(str(mode).lower(), "wal")

    def test_ring_body_payload_storage_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "payloads.sqlite"
            system_entry = {
                "name": "Dedup Sys",
                "id64": 999999,
                "coords": {"x": 1.0, "y": 2.0, "z": 3.0},
                "bodies": [
                    {
                        "name": "Dedup Sys A 1",
                        "bodyId": 10,
                        "rings": [
                            {
                                "name": "Dedup Ring 1",
                                "ringClass": "eRingClass_Icy",
                                "massMT": 101.0,
                                "innerRad": 1.0,
                                "outerRad": 2.0,
                            }
                        ],
                    }
                ],
            }
            system_entry_2 = {
                "name": "Dedup Sys",
                "id64": 999999,
                "coords": {"x": 1.0, "y": 2.0, "z": 3.0},
                "bodies": [
                    {
                        "name": "Dedup Sys A 1",
                        "bodyId": 10,
                        "rings": [
                            {
                                "name": "Dedup Ring 2",
                                "ringClass": "eRingClass_Metallic",
                                "massMT": 102.0,
                                "innerRad": 2.0,
                                "outerRad": 3.0,
                            }
                        ],
                    }
                ],
            }
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                json.dump([system_entry, system_entry_2], handle)

            exit_code = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--store-raw-json",
                    "ring+body",
                    "--commit-every",
                    "1",
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                body_count = conn.execute("SELECT COUNT(*) FROM bodies_raw").fetchone()[0]
                ring_payload_count = conn.execute("SELECT COUNT(*) FROM rings_payloads").fetchone()[0]
                system_payload_count = conn.execute("SELECT COUNT(*) FROM systems_raw").fetchone()[0]
                raw_cols = conn.execute(
                    "SELECT raw_ring_json, raw_body_json, raw_system_json FROM rings_raw"
                ).fetchall()

            self.assertEqual(body_count, 1)
            self.assertEqual(ring_payload_count, 2)
            self.assertEqual(system_payload_count, 0)
            self.assertTrue(all(r[0] is None and r[1] is None and r[2] is None for r in raw_cols))

    def test_primary_star_spectral_fields_populated(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "stars.sqlite"
            system = {
                "name": "Star Sys",
                "id64": 888888,
                "coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                "bodies": [
                    {
                        "name": "Star Sys A",
                        "bodyId": 1,
                        "type": "Star",
                        "isMainStar": True,
                        "spectralClass": "K8 V",
                    },
                    {
                        "name": "Star Sys A 1",
                        "bodyId": 2,
                        "type": "Planet",
                        "rings": [
                            {
                                "name": "Star Ring 1",
                                "ringClass": "eRingClass_Metallic",
                                "massMT": 100.0,
                                "innerRad": 1.0,
                                "outerRad": 2.0,
                            }
                        ],
                    },
                ],
            }
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                json.dump([system], handle)

            exit_code = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                row = conn.execute(
                    """
                    SELECT
                        primary_star_spectral,
                        primary_star_type,
                        primary_star_subtype,
                        primary_star_luminosity,
                        primary_star_class
                    FROM rings_raw
                    LIMIT 1
                    """
                ).fetchone()
            self.assertEqual(row[0], "K8 V")
            self.assertEqual(row[1], "K")
            self.assertEqual(row[2], "8")
            self.assertEqual(row[3], "V")
            self.assertEqual(row[4], "K8 V")

    def test_missing_primary_star_spectral_fields_are_null(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "nostarspec.sqlite"
            system = {
                "name": "No Spec Sys",
                "id64": 777777,
                "coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                "bodies": [
                    {
                        "name": "No Spec Sys A",
                        "bodyId": 1,
                        "type": "Star",
                        "isMainStar": True,
                    },
                    {
                        "name": "No Spec Sys A 1",
                        "bodyId": 2,
                        "type": "Planet",
                        "rings": [
                            {
                                "name": "No Spec Ring 1",
                                "ringClass": "eRingClass_Icy",
                                "massMT": 100.0,
                                "innerRad": 1.0,
                                "outerRad": 2.0,
                            }
                        ],
                    },
                ],
            }
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                json.dump([system], handle)

            exit_code = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                row = conn.execute(
                    """
                    SELECT
                        primary_star_spectral,
                        primary_star_type,
                        primary_star_subtype,
                        primary_star_luminosity,
                        primary_star_class
                    FROM rings_raw
                    LIMIT 1
                    """
                ).fetchone()
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])
            self.assertIsNone(row[2])
            self.assertIsNone(row[3])
            self.assertIsNone(row[4])

    def test_primary_star_subtype_fallback_for_categorical_star(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "categorical.sqlite"
            system = {
                "name": "BH Sys",
                "id64": 776655,
                "coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                "bodies": [
                    {
                        "name": "BH Sys A",
                        "bodyId": 1,
                        "type": "Star",
                        "isMainStar": True,
                        "subType": "Black Hole",
                        "spectralClass": None,
                    },
                    {
                        "name": "BH Sys A 1",
                        "bodyId": 2,
                        "type": "Planet",
                        "rings": [
                            {
                                "name": "BH Ring 1",
                                "ringClass": "eRingClass_Icy",
                                "massMT": 100.0,
                                "innerRad": 1.0,
                                "outerRad": 2.0,
                            }
                        ],
                    },
                ],
            }
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                json.dump([system], handle)

            exit_code = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                row = conn.execute(
                    """
                    SELECT
                        primary_star_spectral,
                        primary_star_type,
                        primary_star_subtype,
                        primary_star_luminosity,
                        primary_star_class
                    FROM rings_raw
                    LIMIT 1
                    """
                ).fetchone()
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])
            self.assertEqual(row[2], "Black Hole")
            self.assertIsNone(row[3])
            self.assertEqual(row[4], "Black Hole")

    def test_parse_spectral_returns_five_items_for_weird_and_empty(self) -> None:
        weird = ingest_rings_master._parse_spectral_components("Wolf-Rayet")
        self.assertEqual(len(weird), 5)
        self.assertEqual(weird[0], "Wolf-Rayet")
        self.assertIsNone(weird[1])
        self.assertIsNone(weird[2])
        self.assertIsNone(weird[3])
        self.assertIsNone(weird[4])

        empty = ingest_rings_master._parse_spectral_components("")
        self.assertEqual(len(empty), 5)
        self.assertEqual(empty, (None, None, None, None, None))

    def test_unexpected_spectral_type_does_not_crash_and_stays_null(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "galaxy.json.gz"
            output_path = tmp_path / "weirdspec.sqlite"
            system = {
                "name": "Weird Spec Sys",
                "id64": 666666,
                "coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                "bodies": [
                    {
                        "name": "Weird Spec Sys A",
                        "bodyId": 1,
                        "type": "Star",
                        "isMainStar": True,
                        "spectralType": 123,  # unexpected type
                    },
                    {
                        "name": "Weird Spec Sys A 1",
                        "bodyId": 2,
                        "type": "Planet",
                        "rings": [
                            {
                                "name": "Weird Ring 1",
                                "ringClass": "eRingClass_Icy",
                                "massMT": 100.0,
                                "innerRad": 1.0,
                                "outerRad": 2.0,
                            }
                        ],
                    },
                ],
            }
            with gzip.open(input_path, "wt", encoding="utf-8") as handle:
                json.dump([system], handle)

            exit_code = _run_ingest_closed(
                [
                    "--input",
                    str(input_path),
                    "--output-db",
                    str(output_path),
                    "--progress-seconds",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)

            with closing(sqlite3.connect(output_path)) as conn:
                row = conn.execute(
                    """
                    SELECT
                        primary_star_spectral,
                        primary_star_type,
                        primary_star_subtype,
                        primary_star_luminosity,
                        primary_star_class
                    FROM rings_raw
                    LIMIT 1
                    """
                ).fetchone()
            self.assertEqual(row, (None, None, None, None, None))


if __name__ == "__main__":
    unittest.main()
