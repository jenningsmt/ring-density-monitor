import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.build_subregion_staging import apply_schema, build_staging


def _setup_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE subregion_summaries (
            score_version TEXT,
            cohort_name TEXT,
            subregion TEXT,
            quadrant TEXT,
            band TEXT,
            n INTEGER,
            centroid_x REAL,
            centroid_y REAL,
            centroid_z REAL,
            radius_max_ly REAL,
            rho_min REAL,
            rho_median REAL,
            rho_max REAL,
            moi_max REAL,
            moi_median REAL,
            min_ring_id TEXT,
            PRIMARY KEY(score_version, cohort_name, subregion)
        );
        CREATE TABLE icy_subregions (
            score_version TEXT,
            cohort_name TEXT,
            ring_id TEXT,
            quadrant TEXT,
            band TEXT,
            subregion TEXT,
            rho_ly REAL,
            x REAL,
            y REAL,
            z REAL,
            system_name TEXT,
            body_name TEXT,
            ring_name TEXT,
            moi_metric REAL,
            rank INTEGER,
            PRIMARY KEY(score_version, cohort_name, ring_id)
        );
        """
    )


def _insert_subregion(conn: sqlite3.Connection, subregion: str, band: str, radius: float, points: list[tuple[str, float, float, float, float]]) -> None:
    n = len(points)
    cx = sum(p[1] for p in points) / n
    cy = sum(p[2] for p in points) / n
    cz = sum(p[3] for p in points) / n
    conn.execute(
        """
        INSERT INTO subregion_summaries (
            score_version, cohort_name, subregion, quadrant, band, n,
            centroid_x, centroid_y, centroid_z, radius_max_ly,
            rho_min, rho_median, rho_max, moi_max, moi_median, min_ring_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("moi_v1", "IcyCore", subregion, subregion.split("-")[0], band, n, cx, cy, cz, radius, 0.0, 0.0, 0.0, 0.0, 0.0, min(p[0] for p in points)),
    )
    for idx, (rid, x, y, z, moi) in enumerate(points, start=1):
        conn.execute(
            """
            INSERT INTO icy_subregions (
                score_version, cohort_name, ring_id, quadrant, band, subregion, rho_ly,
                x, y, z, system_name, body_name, ring_name, moi_metric, rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("moi_v1", "IcyCore", rid, subregion.split("-")[0], band, subregion, 1.0, x, y, z, f"S{rid}", f"B{rid}", f"R{rid}", moi, idx),
        )


def _build_staging_quiet(*args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return build_staging(*args, **kwargs)


class BuildSubregionStagingTests(unittest.TestCase):
    def test_schema_indexes_exist(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "schema.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                apply_schema(conn)
                conn.commit()
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
                required = {
                    "idx_staging_clusters_run_clusterindex",
                    "idx_staging_clusters_run_medoid",
                    "idx_staging_clusters_scope",
                    "idx_staging_members_scope",
                    "idx_staging_members_run_cluster",
                    "idx_staging_runs_scope",
                }
                self.assertTrue(required.issubset(names))
            finally:
                conn.close()

    def test_deterministic_medoids_with_shuffled_insert_order(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db1 = Path(tmp) / "a.sqlite"
            db2 = Path(tmp) / "b.sqlite"
            points = [
                ("a", 0.0, 0.0, 0.0, 7.0),
                ("b", 1.0, 0.0, 0.0, 5.0),
                ("c", 0.0, 1.0, 0.0, 9.0),
                ("d", -1.0, 0.0, 0.0, 4.0),
                ("e", 0.0, -1.0, 0.0, 8.0),
            ]
            for db_path, seq in ((db1, points), (db2, list(reversed(points)))):
                conn = sqlite3.connect(db_path)
                try:
                    _setup_tables(conn)
                    _insert_subregion(conn, "E-inner", "inner", 1000.0, seq)
                    conn.commit()
                finally:
                    conn.close()

            r1 = _build_staging_quiet(db1, "moi_v1", "IcyCore", "E-inner", 2, False, False)
            r2 = _build_staging_quiet(db2, "moi_v1", "IcyCore", "E-inner", 2, False, False)
            self.assertEqual(r1[0]["medoids"], r2[0]["medoids"])
            self.assertEqual(r1[0]["medoids"], ["a", "c"])

    def test_assignment_tie_break_uses_medoid_ring_id_asc(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "tie.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                # two medoids likely at m1 and m2, point p is equidistant.
                _insert_subregion(
                    conn,
                    "E-inner",
                    "inner",
                    1000.0,
                    [
                        ("m1", -1.0, 0.0, 0.0, 10.0),
                        ("m2", 1.0, 0.0, 0.0, 9.0),
                        ("p", 0.0, 0.0, 0.0, 1.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 2, False, False)
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT medoid_ring_id
                    FROM subregion_staging_members
                    WHERE ring_id='p'
                    """
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "m1")
            finally:
                conn.close()

    def test_idempotent_rerun_same_contents(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "idem.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                _insert_subregion(conn, "E-inner", "inner", 1000.0, [("a", 0, 0, 0, 5), ("b", 10, 0, 0, 4)])
                conn.commit()
            finally:
                conn.close()

            first = _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 1, False, False)
            second = _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 1, False, False)
            self.assertEqual(first, second)

            conn = sqlite3.connect(db_path)
            try:
                c1 = conn.execute("SELECT COUNT(*) FROM subregion_staging_clusters").fetchone()[0]
                m1 = conn.execute("SELECT COUNT(*) FROM subregion_staging_members").fetchone()[0]
                self.assertEqual(c1, 1)
                self.assertEqual(m1, 2)
            finally:
                conn.close()

    def test_rerun_replaces_scope_rows_when_k_changes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "replace.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                _insert_subregion(
                    conn,
                    "E-inner",
                    "inner",
                    1000.0,
                    [
                        ("a", 0.0, 0.0, 0.0, 9.0),
                        ("b", 10.0, 0.0, 0.0, 8.0),
                        ("c", 20.0, 0.0, 0.0, 7.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 1, False, False)
            _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 2, False, False)

            conn = sqlite3.connect(db_path)
            try:
                cluster_count = conn.execute("SELECT COUNT(*) FROM subregion_staging_clusters").fetchone()[0]
                member_count = conn.execute("SELECT COUNT(*) FROM subregion_staging_members").fetchone()[0]
                k_values = [row[0] for row in conn.execute("SELECT DISTINCT k FROM subregion_staging_clusters")]
                self.assertEqual(cluster_count, 2)
                self.assertEqual(member_count, 3)
                self.assertEqual(k_values, [2])
            finally:
                conn.close()

    def test_dry_run_performs_no_db_writes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "dryrun.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                _insert_subregion(conn, "E-inner", "inner", 1000.0, [("a", 0, 0, 0, 5), ("b", 10, 0, 0, 4)])
                conn.commit()
            finally:
                conn.close()

            _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 1, False, True)

            conn = sqlite3.connect(db_path)
            try:
                cluster_table = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='subregion_staging_clusters'"
                ).fetchone()[0]
                member_table = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='subregion_staging_members'"
                ).fetchone()[0]
                self.assertEqual(cluster_table, 0)
                self.assertEqual(member_table, 0)
            finally:
                conn.close()

    def test_dist2_is_persisted_and_matches_distance_square(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "dist2.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                _insert_subregion(
                    conn,
                    "E-inner",
                    "inner",
                    1000.0,
                    [
                        ("a", 0.0, 0.0, 0.0, 10.0),
                        ("b", 3.0, 4.0, 0.0, 8.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 1, False, False)

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT dist_to_medoid_ly, dist2_to_medoid
                    FROM subregion_staging_members
                    ORDER BY ring_id
                    """
                ).fetchall()
                self.assertEqual(len(rows), 2)
                for dist, dist2 in rows:
                    self.assertIsNotNone(dist2)
                    self.assertAlmostEqual(dist2, float(dist) * float(dist), places=9)
            finally:
                conn.close()

    def test_run_metadata_fixed_mode(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "runs_fixed.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                _insert_subregion(conn, "E-inner", "inner", 1000.0, [("a", 0, 0, 0, 5), ("b", 10, 0, 0, 4)])
                conn.commit()
            finally:
                conn.close()

            rows = _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 1, False, False)
            self.assertEqual(rows[0]["k"], 1)

            conn = sqlite3.connect(db_path)
            try:
                run = conn.execute(
                    """
                    SELECT run_id, mode, k_final, policy_json
                    FROM subregion_staging_runs
                    WHERE score_version='moi_v1' AND cohort_name='IcyCore' AND subregion='E-inner'
                    """
                ).fetchone()
                self.assertIsNotNone(run)
                self.assertEqual(run[0], rows[0]["run_id"])
                self.assertEqual(run[1], "fixed-k")
                self.assertEqual(run[2], 1)
                self.assertIn("algorithm_version=", run[3])
                self.assertIn("fixed_k=1", run[3])
            finally:
                conn.close()

    def test_run_metadata_auto_mode_and_kmap_order_determinism(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "runs_auto.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                _insert_subregion(
                    conn,
                    "E-inner",
                    "inner",
                    1000.0,
                    [
                        ("a", 0.0, 0.0, 0.0, 10.0),
                        ("b", 20000.0, 0.0, 0.0, 9.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            r1 = _build_staging_quiet(
                db_path,
                "moi_v1",
                "IcyCore",
                "E-inner",
                None,
                True,
                False,
                k_map="Z-outer=3,E-inner=2",
            )
            conn = sqlite3.connect(db_path)
            try:
                run1 = conn.execute(
                    """
                    SELECT run_id, mode, k_final, policy_json
                    FROM subregion_staging_runs
                    WHERE score_version='moi_v1' AND cohort_name='IcyCore' AND subregion='E-inner'
                    """
                ).fetchone()
            finally:
                conn.close()

            r2 = _build_staging_quiet(
                db_path,
                "moi_v1",
                "IcyCore",
                "E-inner",
                None,
                True,
                False,
                k_map="E-inner=2,Z-outer=3",
            )
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT run_id, mode, k_final, policy_json
                    FROM subregion_staging_runs
                    WHERE score_version='moi_v1' AND cohort_name='IcyCore' AND subregion='E-inner'
                    """
                ).fetchall()
                self.assertEqual(len(rows), 1)
                run2 = rows[0]
                self.assertIsNotNone(run1)
                self.assertEqual(run1[0], run2[0])
                self.assertEqual(run1[3], run2[3])
                self.assertEqual(run2[0], r1[0]["run_id"])
                self.assertEqual(run2[0], r2[0]["run_id"])
                self.assertEqual(run2[1], "auto-k")
                self.assertEqual(run2[2], 2)
                self.assertIn("algorithm_version=", run2[3])
                self.assertIn("threshold_ly=", run2[3])
                self.assertIn("kmax=", run2[3])
            finally:
                conn.close()

    def test_medoid_update_changes_from_initial_seed(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "update.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                # centroid near ring 'c' (x=2), but true medoid tie is b/c; tie-break higher moi picks b after update.
                _insert_subregion(
                    conn,
                    "E-inner",
                    "inner",
                    1000.0,
                    [
                        ("a", 0.0, 0.0, 0.0, 1.0),
                        ("b", 1.0, 0.0, 0.0, 10.0),
                        ("c", 2.0, 0.0, 0.0, 1.0),
                        ("d", 100.0, 0.0, 0.0, 1.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", 1, False, False)
            conn = sqlite3.connect(db_path)
            try:
                medoid = conn.execute("SELECT medoid_ring_id FROM subregion_staging_clusters").fetchone()[0]
                self.assertEqual(medoid, "b")
            finally:
                conn.close()

    def test_auto_k_threshold_behavior(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "autok.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                _setup_tables(conn)
                # inner threshold 8000, two far points force k to increment to 2.
                _insert_subregion(
                    conn,
                    "E-inner",
                    "inner",
                    50000.0,
                    [
                        ("a", 0.0, 0.0, 0.0, 10.0),
                        ("b", 20000.0, 0.0, 0.0, 9.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            rows = _build_staging_quiet(db_path, "moi_v1", "IcyCore", "E-inner", None, True, False)
            self.assertEqual(rows[0]["k"], 2)


if __name__ == "__main__":
    unittest.main()
