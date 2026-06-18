import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rings.metric_resolver import resolve_moi_metric
from contextlib import closing


class MetricResolverTests(unittest.TestCase):
    def test_resolve_prefers_moi_final(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "metric_final.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE rings_scored (
                        score_version TEXT,
                        ring_type TEXT,
                        ring_id TEXT,
                        moi_final REAL
                    )
                    """
                )
                self.assertEqual(resolve_moi_metric(conn), "moi_final")

    def test_resolve_falls_back_to_moi0(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "metric_moi0.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE rings_scored (
                        score_version TEXT,
                        ring_type TEXT,
                        ring_id TEXT,
                        moi0 REAL
                    )
                    """
                )
                self.assertEqual(resolve_moi_metric(conn), "moi0")


if __name__ == "__main__":
    unittest.main()
