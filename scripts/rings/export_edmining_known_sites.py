from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path
from contextlib import closing


DEFAULT_OUT_DIR = Path("data/ring_hunter_library/edmining_exports")
KNOWN_HEADERS = [
    "site_key",
    "system_name",
    "planets",
    "mining_type",
    "overlap",
    "materials_json",
    "thanks_to",
    "estimated_ly_from_bubble",
    "source_url",
    "extracted_at_utc",
]
SUBMISSION_HEADERS = [
    "system_name",
    "planets",
    "mining_type",
    "overlap",
    "materials",
    "thanks_to",
    "description",
    "ring_hunter_moi",
    "ring_hunter_percentile",
    "ring_hunter_cohort",
    "ring_hunter_notes",
    "evidence_screenshots_url",
    "submitted_by",
    "submitted_at_utc",
]


def _norm_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().split())


def normalize_planets(planets: str | None) -> str:
    text = _norm_text(planets).lower()
    text = text.replace("rings", "")
    text = " ".join(text.split())
    text = ",".join(part.strip() for part in text.split(","))
    return text


def build_site_key(system_name: str | None, planets: str | None) -> str:
    system_key = _norm_text(system_name).lower()
    planets_norm = normalize_planets(planets)
    return f"{system_key}|{planets_norm}"


def fetch_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT
            system_name,
            planets,
            mining_type,
            overlap,
            materials_json,
            thanks_to,
            estimated_ly_from_bubble,
            source_url,
            extracted_at_utc,
            description
        FROM known_sites_edmining
        ORDER BY system_name ASC, planets ASC, source_url ASC
        """
    ).fetchall()


def export_known_sites(rows: list[sqlite3.Row], out_path: Path) -> int:
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=KNOWN_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "site_key": build_site_key(row["system_name"], row["planets"]),
                    "system_name": row["system_name"],
                    "planets": row["planets"],
                    "mining_type": row["mining_type"],
                    "overlap": row["overlap"],
                    "materials_json": row["materials_json"],
                    "thanks_to": row["thanks_to"],
                    "estimated_ly_from_bubble": row["estimated_ly_from_bubble"],
                    "source_url": row["source_url"],
                    "extracted_at_utc": row["extracted_at_utc"],
                }
            )
    return len(rows)


def export_submission_template(rows: list[sqlite3.Row], out_path: Path) -> int:
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUBMISSION_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "system_name": row["system_name"],
                    "planets": row["planets"],
                    "mining_type": row["mining_type"],
                    "overlap": row["overlap"],
                    "materials": row["materials_json"] or "",
                    "thanks_to": row["thanks_to"],
                    "description": row["description"],
                    "ring_hunter_moi": "",
                    "ring_hunter_percentile": "",
                    "ring_hunter_cohort": "",
                    "ring_hunter_notes": "",
                    "evidence_screenshots_url": "",
                    "submitted_by": "",
                    "submitted_at_utc": "",
                }
            )
    return len(rows)


def run_export(db_path: Path, out_dir: Path) -> tuple[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = fetch_rows(conn)
    known_count = export_known_sites(rows, out_dir / "edmining_tritium_known_sites.csv")
    submission_count = export_submission_template(rows, out_dir / "edmining_submission_template.csv")
    return known_count, submission_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export normalized EDMining known-site reference CSVs.")
    parser.add_argument("--db", required=True, help="Path to SQLite DB containing known_sites_edmining.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for export CSVs.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    known_count, submission_count = run_export(Path(args.db), Path(args.out_dir))
    print(f"edmining_tritium_known_sites.csv rows={known_count}")
    print(f"edmining_submission_template.csv rows={submission_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
