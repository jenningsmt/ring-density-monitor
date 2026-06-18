import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contextlib import closing

from scripts.rings.build_subregion_staging import apply_schema
from scripts.rings.export_subregion_candidates import CLUSTER_HEADERS, MEMBER_HEADERS, run_export


def _insert_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    created_utc: str,
    k_final: int,
    mode: str,
    clusters: list[tuple[int, str, str, int, float, float, float, float, float]],
    members: list[tuple[int, str, str, float]],
) -> None:
    for (
        cluster_index,
        cluster_id,
        medoid_ring_id,
        size_n,
        radius_max_ly,
        radius_p90_ly,
        centroid_x,
        centroid_y,
        centroid_z,
    ) in clusters:
        conn.execute(
            """
            INSERT INTO subregion_staging_clusters (
                run_id, score_version, cohort_name, subregion, k, cluster_index, cluster_id,
                medoid_ring_id, size_n, radius_max_ly, radius_p90_ly, centroid_x, centroid_y, centroid_z,
                cost_sum, created_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "moi_v1",
                "IcyCore",
                "E-inner",
                k_final,
                cluster_index,
                cluster_id,
                medoid_ring_id,
                size_n,
                radius_max_ly,
                radius_p90_ly,
                centroid_x,
                centroid_y,
                centroid_z,
                0.0,
                created_utc,
            ),
        )
    for cluster_index, cluster_id, ring_id, dist in members:
        medoid_ring_id = next(c[2] for c in clusters if c[1] == cluster_id)
        conn.execute(
            """
            INSERT INTO subregion_staging_members (
                run_id, cluster_id, score_version, cohort_name, subregion, ring_id,
                medoid_ring_id, dist_to_medoid_ly, dist2_to_medoid, assign_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                cluster_id,
                "moi_v1",
                "IcyCore",
                "E-inner",
                ring_id,
                medoid_ring_id,
                dist,
                dist * dist,
                cluster_index,
            ),
        )
    conn.execute(
        """
        INSERT INTO subregion_staging_runs (
            run_id, score_version, cohort_name, subregion, mode, k_final, policy_json, created_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            "moi_v1",
            "IcyCore",
            "E-inner",
            mode,
            k_final,
            "algorithm_version=test",
            created_utc,
        ),
    )


class ExportSubregionCandidatesTests(unittest.TestCase):
    def _seed_db(self, db_path: Path) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            apply_schema(conn)
            _insert_run(
                conn,
                run_id="old-run",
                created_utc="2026-02-14T09:00:00+00:00",
                k_final=1,
                mode="fixed-k",
                clusters=[(1, "c-old", "m-old", 1, 1.0, 1.0, 0.0, 0.0, 0.0)],
                members=[(1, "c-old", "old-member", 1.0)],
            )

            clusters = [
                # radius_p90_ly intentionally set to a mismatching value to verify member-based p90.
                (1, "c-1", "m-1", 9, 5.0, 999.0, 1.0, 0.0, 0.0),
                (2, "c-2", "m-2", 4, 3.0, 999.0, 0.0, 2.0, 0.0),
                (3, "c-3", "m-3", 16, 7.0, 999.0, 0.0, 0.0, 3.0),
            ]
            members: list[tuple[int, str, str, float]] = []
            for i in range(9):
                members.append((1, "c-1", f"r1-{8 - i}", 5.0))
            for i in range(4):
                members.append((2, "c-2", f"r2-{3 - i}", 3.0))
            for i in range(16):
                members.append((3, "c-3", f"r3-{15 - i}", 7.0))

            _insert_run(
                conn,
                run_id="new-run",
                created_utc="2026-02-14T10:00:00+00:00",
                k_final=3,
                mode="fixed-k",
                clusters=clusters,
                members=members,
            )
            conn.commit()

    def test_export_candidates_deterministic_order(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "rings.sqlite"
            self._seed_db(db_path)

            out_a = tmp_path / "out_a"
            out_b = tmp_path / "out_b"
            run_export(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregion="E-inner",
                outdir=out_a,
            )
            run_export(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregion="E-inner",
                outdir=out_b,
            )

            self.assertEqual(
                (out_a / "clusters.csv").read_text(encoding="utf-8"),
                (out_b / "clusters.csv").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (out_a / "members.csv").read_text(encoding="utf-8"),
                (out_b / "members.csv").read_text(encoding="utf-8"),
            )

            with (out_a / "clusters.csv").open("r", newline="", encoding="utf-8") as handle:
                clusters_rows = list(csv.DictReader(handle))
            self.assertEqual([r["cluster_index"] for r in clusters_rows], ["2", "1", "3"])
            self.assertTrue(all(r["run_id"] == "new-run" for r in clusters_rows))

            with (out_a / "members.csv").open("r", newline="", encoding="utf-8") as handle:
                member_rows = list(csv.DictReader(handle))
            self.assertEqual(member_rows[0]["cluster_index"], "2")
            self.assertEqual(member_rows[-1]["cluster_index"], "3")

    def test_priority_score_matches_expected_for_fixture(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "rings.sqlite"
            self._seed_db(db_path)
            out_dir = tmp_path / "out"

            run_export(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregion="E-inner",
                outdir=out_dir,
                run_id="new-run",
            )

            with (out_dir / "clusters.csv").open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            cluster1 = next(r for r in rows if r["cluster_id"] == "c-1")
            # cluster c-1 uses member distances [5, ...], so p90 = 5.
            expected = (9.0 ** 0.5) / (5.0 + 1.0)
            self.assertAlmostEqual(float(cluster1["p90_ly"]), 5.0, places=6)
            self.assertAlmostEqual(float(cluster1["priority_score"]), expected, places=9)
            self.assertIn("n=9", cluster1["priority_reason"])

    def test_csv_headers_and_row_counts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "rings.sqlite"
            self._seed_db(db_path)
            out_dir = tmp_path / "out"

            cluster_count, member_count = run_export(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                subregion="E-inner",
                outdir=out_dir,
            )
            self.assertEqual(cluster_count, 3)
            self.assertEqual(member_count, 29)

            with (out_dir / "clusters.csv").open("r", newline="", encoding="utf-8") as handle:
                c_reader = csv.DictReader(handle)
                c_rows = list(c_reader)
            with (out_dir / "members.csv").open("r", newline="", encoding="utf-8") as handle:
                m_reader = csv.DictReader(handle)
                m_rows = list(m_reader)

            self.assertEqual(list(c_reader.fieldnames or []), CLUSTER_HEADERS)
            self.assertEqual(list(m_reader.fieldnames or []), MEMBER_HEADERS)
            self.assertEqual(len(c_rows), 3)
            self.assertEqual(len(m_rows), 29)


if __name__ == "__main__":
    unittest.main()
