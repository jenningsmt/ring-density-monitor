import csv
import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contextlib import closing

from scripts.rings.build_expedition_sequence import build_expedition_sequence, main
from scripts.rings.build_subregion_staging import apply_schema


def _setup_tables(conn: sqlite3.Connection) -> None:
    apply_schema(conn)
    conn.executescript(
        """
        CREATE TABLE rings_raw (
            ring_id TEXT PRIMARY KEY,
            system_name TEXT,
            body_name TEXT,
            ring_name TEXT,
            x REAL,
            y REAL,
            z REAL
        );
        CREATE TABLE icy_subregions (
            score_version TEXT NOT NULL,
            cohort_name TEXT NOT NULL,
            ring_id TEXT NOT NULL,
            quadrant TEXT NOT NULL,
            band TEXT NOT NULL,
            subregion TEXT NOT NULL,
            rho_ly REAL NOT NULL,
            x REAL NOT NULL,
            y REAL NOT NULL,
            z REAL NOT NULL,
            system_name TEXT,
            body_name TEXT,
            ring_name TEXT,
            moi_metric REAL,
            rank INTEGER,
            PRIMARY KEY(score_version, cohort_name, ring_id)
        );
        """
    )


def _setup_legacy_staging_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE staged_cluster_runs (
            run_id TEXT PRIMARY KEY,
            score_version TEXT NOT NULL,
            cohort_name TEXT NOT NULL,
            subregion TEXT NOT NULL,
            mode TEXT NOT NULL,
            k_final INTEGER NOT NULL,
            policy_json TEXT NOT NULL,
            created_utc TEXT NOT NULL
        );
        CREATE TABLE staged_clusters (
            run_id TEXT NOT NULL,
            score_version TEXT NOT NULL,
            cohort_name TEXT NOT NULL,
            subregion TEXT NOT NULL,
            k INTEGER NOT NULL,
            cluster_index INTEGER NOT NULL,
            cluster_id TEXT PRIMARY KEY NOT NULL,
            medoid_ring_id TEXT NOT NULL,
            size_n INTEGER NOT NULL,
            radius_max_ly REAL NOT NULL,
            radius_p90_ly REAL NOT NULL,
            centroid_x REAL NOT NULL,
            centroid_y REAL NOT NULL,
            centroid_z REAL NOT NULL,
            cost_sum REAL NOT NULL,
            created_utc TEXT NOT NULL
        );
        CREATE TABLE staged_cluster_members (
            run_id TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            score_version TEXT NOT NULL,
            cohort_name TEXT NOT NULL,
            subregion TEXT NOT NULL,
            ring_id TEXT NOT NULL,
            medoid_ring_id TEXT NOT NULL,
            dist_to_medoid_ly REAL NOT NULL,
            dist2_to_medoid REAL NOT NULL,
            assign_rank INTEGER NOT NULL,
            PRIMARY KEY(run_id, ring_id)
        );
        """
    )


def _insert_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    subregion: str,
    created_utc: str,
    clusters: list[tuple[int, str, str, int, tuple[float, float, float]]],
    members: list[tuple[str, str, float, float]],
    runs_table: str = "subregion_staging_runs",
    clusters_table: str = "subregion_staging_clusters",
    members_table: str = "subregion_staging_members",
) -> None:
    conn.execute(
        f"""
        INSERT INTO {runs_table} (
            run_id, score_version, cohort_name, subregion, mode, k_final, policy_json, created_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, "moi_v1", "IcyCore", subregion, "fixed-k", len(clusters), "algo=test", created_utc),
    )
    for cluster_index, cluster_id, medoid_ring_id, size_n, centroid in clusters:
        conn.execute(
            f"""
            INSERT INTO {clusters_table} (
                run_id, score_version, cohort_name, subregion, k, cluster_index, cluster_id,
                medoid_ring_id, size_n, radius_max_ly, radius_p90_ly,
                centroid_x, centroid_y, centroid_z, cost_sum, created_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "moi_v1",
                "IcyCore",
                subregion,
                len(clusters),
                cluster_index,
                cluster_id,
                medoid_ring_id,
                size_n,
                0.0,
                0.0,
                centroid[0],
                centroid[1],
                centroid[2],
                0.0,
                created_utc,
            ),
        )
    for cluster_id, ring_id, dist_ly, moi_metric in members:
        medoid = next(c[2] for c in clusters if c[1] == cluster_id)
        conn.execute(
            f"""
            INSERT INTO {members_table} (
                run_id, cluster_id, score_version, cohort_name, subregion, ring_id,
                medoid_ring_id, dist_to_medoid_ly, dist2_to_medoid, assign_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                cluster_id,
                "moi_v1",
                "IcyCore",
                subregion,
                ring_id,
                medoid,
                dist_ly,
                dist_ly * dist_ly,
                1,
            ),
        )
        xyz = next(c[4] for c in clusters if c[1] == cluster_id)
        conn.execute(
            """
            INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ring_id, f"SYS-{ring_id}", f"BODY-{ring_id}", f"RING-{ring_id}", xyz[0], xyz[1], xyz[2]),
        )
        conn.execute(
            """
            INSERT INTO icy_subregions (
                score_version, cohort_name, ring_id, quadrant, band, subregion, rho_ly, x, y, z,
                system_name, body_name, ring_name, moi_metric, rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "moi_v1",
                "IcyCore",
                ring_id,
                "E",
                "inner",
                subregion,
                1.0,
                xyz[0],
                xyz[1],
                xyz[2],
                f"SYS-{ring_id}",
                f"BODY-{ring_id}",
                f"RING-{ring_id}",
                moi_metric,
                1,
            ),
        )


class BuildExpeditionSequenceTests(unittest.TestCase):
    def _build_quiet(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return build_expedition_sequence(**kwargs)

    def _create_fixture(self, db_path: Path) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            _setup_tables(conn)
            conn.execute(
                "INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("start-anchor", "Phraa Blao LU-Q C21-34", "A", "A Ring", 0.0, 0.0, 0.0),
            )
            _insert_run(
                conn,
                run_id="run-old",
                subregion="E-inner",
                created_utc="2026-02-16T09:00:00+00:00",
                clusters=[(1, "c-old", "m-old", 1, (100.0, 0.0, 0.0))],
                members=[("c-old", "m-old", 0.0, 1.0)],
            )
            _insert_run(
                conn,
                run_id="run-e-new",
                subregion="E-inner",
                created_utc="2026-02-16T10:00:00+00:00",
                clusters=[
                    (1, "c-1", "m-1", 9, (1000.0, 0.0, 0.0)),
                    (2, "c-2", "m-2", 4, (3000.0, 0.0, 0.0)),
                ],
                members=[
                    ("c-1", "m-1", 0.0, 9.0),
                    ("c-1", "r-1a", 5.0, 8.0),
                    ("c-2", "m-2", 0.0, 7.0),
                    ("c-2", "r-2a", 3.0, 6.0),
                ],
            )
            _insert_run(
                conn,
                run_id="run-n-new",
                subregion="N-inner",
                created_utc="2026-02-16T10:05:00+00:00",
                clusters=[
                    (1, "c-3", "m-3", 25, (12000.0, 0.0, 0.0)),
                    (2, "c-4", "m-4", 16, (16000.0, 0.0, 0.0)),
                ],
                members=[
                    ("c-3", "m-3", 0.0, 5.0),
                    ("c-3", "r-3a", 9.0, 4.0),
                    ("c-4", "m-4", 0.0, 3.0),
                    ("c-4", "r-4a", 2.0, 2.0),
                ],
            )
            conn.commit()

    def test_deterministic_output_ordering(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "seq.sqlite"
            self._create_fixture(db_path)
            out_a = tmp_path / "a"
            out_b = tmp_path / "b"

            self._build_quiet(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregions=["E-inner", "N-inner"],
                all_subregions=False,
                start_system="Phraa Blao LU-Q C21-34",
                max_leg_ly=8000.0,
                outdir=out_a,
            )
            self._build_quiet(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregions=["N-inner", "E-inner"],
                all_subregions=False,
                start_system="Phraa Blao LU-Q C21-34",
                max_leg_ly=8000.0,
                outdir=out_b,
            )

            self.assertEqual(
                (out_a / "subregion_E-inner" / "sequence_clusters.csv").read_text(encoding="utf-8"),
                (out_b / "subregion_E-inner" / "sequence_clusters.csv").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (out_a / "subregion_N-inner" / "sequence_clusters.csv").read_text(encoding="utf-8"),
                (out_b / "subregion_N-inner" / "sequence_clusters.csv").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (out_a / "subregion_E-inner" / "sequence_targets.csv").read_text(encoding="utf-8"),
                (out_b / "subregion_E-inner" / "sequence_targets.csv").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (out_a / "subregion_N-inner" / "sequence_targets.csv").read_text(encoding="utf-8"),
                (out_b / "subregion_N-inner" / "sequence_targets.csv").read_text(encoding="utf-8"),
            )

            with (out_a / "subregion_E-inner" / "sequence_clusters.csv").open("r", newline="", encoding="utf-8") as handle:
                e_rows = list(csv.DictReader(handle))
            with (out_a / "subregion_N-inner" / "sequence_clusters.csv").open("r", newline="", encoding="utf-8") as handle:
                n_rows = list(csv.DictReader(handle))
            self.assertEqual([r["cluster_id"] for r in e_rows], ["c-1", "c-2"])
            self.assertEqual([r["cluster_id"] for r in n_rows], ["c-3", "c-4"])
            self.assertTrue(all(r["run_id"] != "run-old" for r in e_rows + n_rows))

    def test_staging_flags_trip_for_long_legs(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "seq.sqlite"
            self._create_fixture(db_path)
            out_dir = tmp_path / "out"

            self._build_quiet(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregions=None,
                all_subregions=True,
                start_system="Phraa Blao LU-Q C21-34",
                max_leg_ly=8000.0,
                outdir=out_dir,
            )

            with (out_dir / "subregion_N-inner" / "sequence_clusters.csv").open("r", newline="", encoding="utf-8") as handle:
                n_rows = list(csv.DictReader(handle))
            flagged = [r for r in n_rows if r["staging_required"] == "true"]
            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["cluster_id"], "c-3")
            self.assertEqual(flagged[0]["leg_distance_ly"], "12000.000000")

            with (out_dir / "subregion_N-inner" / "staging_recommendations.csv").open("r", newline="", encoding="utf-8") as handle:
                staging_rows = list(csv.DictReader(handle))
            self.assertEqual(len(staging_rows), 1)
            self.assertEqual(staging_rows[0]["from_cluster"], "START")
            self.assertEqual(staging_rows[0]["to_cluster"], "c-3")
            self.assertEqual(staging_rows[0]["distance_ly"], "12000.000000")

    def test_csv_headers_and_row_counts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "seq.sqlite"
            self._create_fixture(db_path)
            out_dir = tmp_path / "out"

            result = self._build_quiet(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregions=["E-inner", "N-inner"],
                all_subregions=False,
                start_system="Phraa Blao LU-Q C21-34",
                max_leg_ly=8000.0,
                outdir=out_dir,
            )
            self.assertEqual(result["cluster_rows"], 4)
            self.assertEqual(result["target_rows"], 8)
            self.assertEqual(result["staging_rows"], 1)

            with (out_dir / "subregion_E-inner" / "sequence_clusters.csv").open("r", newline="", encoding="utf-8") as handle:
                c_reader = csv.DictReader(handle)
                c_rows = list(c_reader)
            with (out_dir / "subregion_E-inner" / "sequence_targets.csv").open("r", newline="", encoding="utf-8") as handle:
                t_reader = csv.DictReader(handle)
                t_rows = list(t_reader)
            with (out_dir / "subregion_E-inner" / "staging_recommendations.csv").open("r", newline="", encoding="utf-8") as handle:
                s_reader = csv.DictReader(handle)
                s_rows = list(s_reader)

            self.assertEqual(
                list(c_reader.fieldnames or []),
                [
                    "seq",
                    "score_version",
                    "cohort_name",
                    "subregion",
                    "run_id",
                    "cluster_index",
                    "cluster_id",
                    "medoid_ring_id",
                    "leg_distance_ly",
                    "staging_required",
                    "size_n",
                    "p90_ly",
                    "centroid_rho",
                ],
            )
            self.assertEqual(
                list(t_reader.fieldnames or []),
                [
                    "seq",
                    "target_order",
                    "score_version",
                    "cohort_name",
                    "subregion",
                    "run_id",
                    "cluster_index",
                    "cluster_id",
                    "ring_id",
                    "is_medoid",
                    "dist_to_medoid_ly",
                    "dist2_to_medoid",
                    "moi_metric",
                    "system_name",
                    "body_name",
                    "ring_name",
                ],
            )
            self.assertEqual(list(s_reader.fieldnames or []), ["seq", "from_cluster", "to_cluster", "distance_ly"])
            self.assertEqual(len(c_rows), 2)
            self.assertEqual(len(t_rows), 4)
            self.assertEqual(len(s_rows), 0)
            self.assertFalse((out_dir / "sequence_clusters.csv").exists())
            self.assertFalse((out_dir / "sequence_targets.csv").exists())
            self.assertFalse((out_dir / "staging_recommendations.csv").exists())

    def test_cli_all_subregions_flag_propagates_and_creates_subregion_folders(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "seq.sqlite"
            self._create_fixture(db_path)
            out_dir = tmp_path / "out"

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = main(
                    [
                        "--db",
                        str(db_path),
                        "--score-version",
                        "moi_v1",
                        "--cohort-name",
                        "IcyCore",
                        "--all-subregions",
                        "--start-system",
                        "Phraa Blao LU-Q C21-34",
                        "--max-leg-ly",
                        "8000",
                        "--outdir",
                        str(out_dir),
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertTrue((out_dir / "subregion_E-inner").is_dir())
            self.assertTrue((out_dir / "subregion_N-inner").is_dir())
            self.assertTrue((out_dir / "subregion_E-inner" / "sequence_clusters.csv").exists())
            self.assertTrue((out_dir / "subregion_E-inner" / "sequence_targets.csv").exists())
            self.assertTrue((out_dir / "subregion_E-inner" / "staging_recommendations.csv").exists())
            self.assertTrue((out_dir / "subregion_N-inner" / "sequence_clusters.csv").exists())
            self.assertTrue((out_dir / "subregion_N-inner" / "sequence_targets.csv").exists())
            self.assertTrue((out_dir / "subregion_N-inner" / "staging_recommendations.csv").exists())
            self.assertFalse((out_dir / "sequence_clusters.csv").exists())
            self.assertFalse((out_dir / "sequence_targets.csv").exists())
            self.assertFalse((out_dir / "staging_recommendations.csv").exists())

    def test_all_subregions_with_legacy_staging_table_names(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "legacy.sqlite"
            out_dir = tmp_path / "out"
            with closing(sqlite3.connect(db_path)) as conn:
                _setup_legacy_staging_tables(conn)
                conn.executescript(
                    """
                    CREATE TABLE rings_raw (
                        ring_id TEXT PRIMARY KEY,
                        system_name TEXT,
                        body_name TEXT,
                        ring_name TEXT,
                        x REAL,
                        y REAL,
                        z REAL
                    );
                    CREATE TABLE icy_subregions (
                        score_version TEXT NOT NULL,
                        cohort_name TEXT NOT NULL,
                        ring_id TEXT NOT NULL,
                        quadrant TEXT NOT NULL,
                        band TEXT NOT NULL,
                        subregion TEXT NOT NULL,
                        rho_ly REAL NOT NULL,
                        x REAL NOT NULL,
                        y REAL NOT NULL,
                        z REAL NOT NULL,
                        system_name TEXT,
                        body_name TEXT,
                        ring_name TEXT,
                        moi_metric REAL,
                        rank INTEGER,
                        PRIMARY KEY(score_version, cohort_name, ring_id)
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("start-anchor", "Phraa Blao LU-Q C21-34", "A", "A Ring", 0.0, 0.0, 0.0),
                )
                _insert_run(
                    conn,
                    run_id="legacy-e",
                    subregion="E-inner",
                    created_utc="2026-02-16T10:00:00+00:00",
                    clusters=[(1, "legacy-c1", "legacy-m1", 2, (1000.0, 0.0, 0.0))],
                    members=[("legacy-c1", "legacy-m1", 0.0, 5.0), ("legacy-c1", "legacy-r1", 2.0, 4.0)],
                    runs_table="staged_cluster_runs",
                    clusters_table="staged_clusters",
                    members_table="staged_cluster_members",
                )
                _insert_run(
                    conn,
                    run_id="legacy-n",
                    subregion="N-inner",
                    created_utc="2026-02-16T10:01:00+00:00",
                    clusters=[(1, "legacy-c2", "legacy-m2", 2, (2000.0, 0.0, 0.0))],
                    members=[("legacy-c2", "legacy-m2", 0.0, 3.0), ("legacy-c2", "legacy-r2", 3.0, 2.0)],
                    runs_table="staged_cluster_runs",
                    clusters_table="staged_clusters",
                    members_table="staged_cluster_members",
                )
                conn.commit()

            result = self._build_quiet(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregions=[],
                all_subregions=True,
                start_system="Phraa Blao LU-Q C21-34",
                max_leg_ly=8000.0,
                outdir=out_dir,
            )
            self.assertEqual(result["cluster_rows"], 2)
            self.assertTrue((out_dir / "subregion_E-inner" / "sequence_clusters.csv").exists())
            self.assertTrue((out_dir / "subregion_E-inner" / "sequence_targets.csv").exists())
            self.assertTrue((out_dir / "subregion_E-inner" / "staging_recommendations.csv").exists())
            self.assertTrue((out_dir / "subregion_N-inner" / "sequence_clusters.csv").exists())
            self.assertTrue((out_dir / "subregion_N-inner" / "sequence_targets.csv").exists())
            self.assertTrue((out_dir / "subregion_N-inner" / "staging_recommendations.csv").exists())
            self.assertFalse((out_dir / "sequence_clusters.csv").exists())
            self.assertFalse((out_dir / "sequence_targets.csv").exists())
            self.assertFalse((out_dir / "staging_recommendations.csv").exists())

    def test_empty_staging_db_raises_clear_preflight_message(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "empty.sqlite"
            out_dir = tmp_path / "out"
            with closing(sqlite3.connect(db_path)) as conn:
                _setup_tables(conn)
                conn.execute(
                    "INSERT INTO rings_raw (ring_id, system_name, body_name, ring_name, x, y, z) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("start-anchor", "Phraa Blao LU-Q C21-34", "A", "A Ring", 0.0, 0.0, 0.0),
                )
                conn.commit()

            with self.assertRaises(ValueError) as ctx:
                self._build_quiet(
                    db_path=db_path,
                    score_version="moi_v1",
                    cohort_name="IcyCore",
                    subregions=[],
                    all_subregions=True,
                    start_system="Phraa Blao LU-Q C21-34",
                    max_leg_ly=8000.0,
                    outdir=out_dir,
                )
            self.assertEqual(
                str(ctx.exception),
                "No staged runs found. Run build_subregion_staging first (and ensure you are pointing at the populated rings_master_YYYY-MM-DD.sqlite).",
            )


if __name__ == "__main__":
    unittest.main()
