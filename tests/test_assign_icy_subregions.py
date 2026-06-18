import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.assign_icy_subregions import assign_subregions


class AssignIcySubregionsTests(unittest.TestCase):
    def _create_fixture(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE icy_quadrants (
                    score_version TEXT NOT NULL,
                    cohort_name TEXT NOT NULL,
                    ring_id TEXT NOT NULL,
                    quadrant TEXT NOT NULL,
                    theta_deg REAL NOT NULL,
                    dx REAL NOT NULL,
                    dz REAL NOT NULL,
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
                CREATE TABLE quadrant_summaries (
                    score_version TEXT NOT NULL,
                    cohort_name TEXT NOT NULL,
                    quadrant TEXT NOT NULL,
                    n INTEGER NOT NULL,
                    centroid_x REAL NOT NULL,
                    centroid_y REAL NOT NULL,
                    centroid_z REAL NOT NULL,
                    radius_max_ly REAL NOT NULL,
                    moi_max REAL,
                    moi_median REAL,
                    min_ring_id TEXT NOT NULL,
                    PRIMARY KEY(score_version, cohort_name, quadrant)
                );
                """
            )
            # 6 E-quadrant rings with deterministic rho ordering against saga=(0,0,0): 1..6
            for i, rho in enumerate([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], start=1):
                conn.execute(
                    """
                    INSERT INTO icy_quadrants (
                        score_version, cohort_name, ring_id, quadrant, theta_deg, dx, dz,
                        x, y, z, system_name, body_name, ring_name, moi_metric, rank
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "moi_v1",
                        "IcyCore",
                        f"e{i}",
                        "E",
                        0.0,
                        rho,
                        0.0,
                        rho,
                        0.0,
                        0.0,
                        f"Se{i}",
                        f"Be{i}",
                        f"Re{i}",
                        float(100 - i),
                        i,
                    ),
                )
            conn.execute(
                """
                INSERT INTO quadrant_summaries (
                    score_version, cohort_name, quadrant, n, centroid_x, centroid_y, centroid_z,
                    radius_max_ly, moi_max, moi_median, min_ring_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("moi_v1", "IcyCore", "E", 6, 3.5, 0.0, 0.0, 2.5, 99.0, 96.5, "e1"),
            )
            conn.commit()
        finally:
            conn.close()

    def test_deterministic_band_assignment(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "subregions.sqlite"
            self._create_fixture(db_path)

            # For n=6 and floor offsets:
            # p33 index=floor(5*0.3333333333)=1 => 2.0
            # p66 index=floor(5*0.6666666667)=3 => 4.0
            # inner: <=2.0 => e1,e2 ; mid: (2.0,4.0] => e3,e4 ; outer: >4.0 => e5,e6
            first = assign_subregions(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                saga_x=0.0,
                saga_y=0.0,
                saga_z=0.0,
                dry_run=False,
            )
            self.assertEqual(first["counts"], {"E-inner": 2, "E-mid": 2, "E-outer": 2})

            conn = sqlite3.connect(db_path)
            try:
                rows1 = conn.execute(
                    """
                    SELECT ring_id, band, subregion, rho_ly
                    FROM icy_subregions
                    WHERE score_version='moi_v1' AND cohort_name='IcyCore'
                    ORDER BY ring_id ASC
                    """
                ).fetchall()
                self.assertEqual(
                    rows1,
                    [
                        ("e1", "inner", "E-inner", 1.0),
                        ("e2", "inner", "E-inner", 2.0),
                        ("e3", "mid", "E-mid", 3.0),
                        ("e4", "mid", "E-mid", 4.0),
                        ("e5", "outer", "E-outer", 5.0),
                        ("e6", "outer", "E-outer", 6.0),
                    ],
                )
                sums1 = conn.execute(
                    """
                    SELECT subregion, quadrant, band, n, rho_min, rho_median, rho_max
                    FROM subregion_summaries
                    WHERE score_version='moi_v1' AND cohort_name='IcyCore'
                    ORDER BY subregion ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            second = assign_subregions(
                db_path=db_path,
                score_version="moi_v1",
                cohort_name="IcyCore",
                saga_x=0.0,
                saga_y=0.0,
                saga_z=0.0,
                dry_run=False,
            )
            self.assertEqual(first["counts"], second["counts"])

            conn = sqlite3.connect(db_path)
            try:
                rows2 = conn.execute(
                    """
                    SELECT ring_id, band, subregion, rho_ly
                    FROM icy_subregions
                    WHERE score_version='moi_v1' AND cohort_name='IcyCore'
                    ORDER BY ring_id ASC
                    """
                ).fetchall()
                sums2 = conn.execute(
                    """
                    SELECT subregion, quadrant, band, n, rho_min, rho_median, rho_max
                    FROM subregion_summaries
                    WHERE score_version='moi_v1' AND cohort_name='IcyCore'
                    ORDER BY subregion ASC
                    """
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(rows1, rows2)
            self.assertEqual(sums1, sums2)


if __name__ == "__main__":
    unittest.main()
