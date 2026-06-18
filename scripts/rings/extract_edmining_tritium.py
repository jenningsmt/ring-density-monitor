from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from contextlib import closing


DEFAULT_LISTING_URL = "https://edmining.com/location-material-type/tritium/"
DEFAULT_OUT_CSV = Path("data/ring_hunter_library/edmining_tritium_locations.csv")
DEFAULT_MAX_PAGES = 50
DEFAULT_SLEEP = 1.0
DEFAULT_TIMEOUT = 20.0
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; RingHunterBot/1.0; +https://edmfi.local)"
CSV_HEADERS = [
    "source_url",
    "source_category",
    "system_name",
    "planets",
    "estimated_ly_from_bubble",
    "mining_type",
    "overlap",
    "thanks_to",
    "tritium_explicit",
    "tritium_inferred",
    "parse_warnings",
    "material_type_raw",
    "materials_json",
    "description",
    "extracted_at_utc",
]
RETRYABLE_STATUS = {406, 429, 500, 502, 503, 504}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ws(text: str | None) -> str:
    if text is None:
        return ""
    return " ".join(text.split()).strip()


def safe_float(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_materials_list(material_type_value: str) -> list[str]:
    if not material_type_value:
        return []
    parts = [normalize_ws(part) for part in re.split(r"[,;/|]+", material_type_value)]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return out


def parse_materials(raw: str | None) -> list[str]:
    return parse_materials_list(raw or "")


def canonicalize_url(url: str) -> str:
    split = urlsplit(url)
    return urlunsplit((split.scheme, split.netloc, split.path, "", ""))


def _sanitize_filename(text: str) -> str:
    out = re.sub(r"[^0-9A-Za-z._-]+", "_", text).strip("._-")
    return out or "page"


def parse_listing(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        absolute = canonicalize_url(urljoin(base_url, href))
        if "/mining-location/" not in absolute:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


def find_next_listing_url(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        rel = anchor.get("rel") or []
        rel_values = [str(item).lower() for item in rel]
        if "next" in rel_values:
            return canonicalize_url(urljoin(current_url, anchor["href"]))
    for anchor in soup.find_all("a", href=True):
        text = normalize_ws(anchor.get_text(" ", strip=True)) or ""
        if "next" in text.lower():
            return canonicalize_url(urljoin(current_url, anchor["href"]))
    return None


def _canonical_label(label: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (label or "").lower())


def _extract_label_value_map(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = normalize_ws(cells[0].get_text(" ", strip=True))
        value = normalize_ws(cells[1].get_text(" ", strip=True))
        if label and value:
            pairs.setdefault(_canonical_label(label.rstrip(":")), value)

    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        label = normalize_ws(dt.get_text(" ", strip=True))
        value = normalize_ws(dd.get_text(" ", strip=True))
        if label and value:
            pairs.setdefault(_canonical_label(label.rstrip(":")), value)

    for node in soup.find_all(["p", "li", "div"]):
        text = normalize_ws(node.get_text(" ", strip=True))
        if not text or ":" not in text:
            continue
        label_raw, value_raw = text.split(":", 1)
        label = normalize_ws(label_raw)
        value = normalize_ws(value_raw)
        if not label or not value or len(label) > 64:
            continue
        pairs.setdefault(_canonical_label(label), value)

    return pairs


def _first_label_value(label_map: dict[str, str], key_snippets: list[str]) -> str | None:
    for key, value in label_map.items():
        if any(snippet in key for snippet in key_snippets):
            return value
    return None


def _extract_description(soup: BeautifulSoup) -> str | None:
    paragraphs: list[str] = []
    seen: set[str] = set()
    for p in soup.find_all("p"):
        text = normalize_ws(p.get_text(" ", strip=True))
        if not text:
            continue
        if ":" in text and len(text.split(":", 1)[0]) < 40:
            continue
        if text in seen:
            continue
        seen.add(text)
        paragraphs.append(text)
        if len(paragraphs) >= 2:
            break
    if not paragraphs:
        return None
    return "\n\n".join(paragraphs)


def extract_field_from_text(block_text: str, label: str, stop_labels: list[str]) -> str:
    text = normalize_ws(block_text)
    if not text:
        return ""
    lower_text = text.lower()
    label_lower = label.lower()
    start = lower_text.find(label_lower)
    if start < 0:
        return ""
    value_start = start + len(label)
    value = text[value_start:]
    lower_value = lower_text[value_start:]
    end_idx = len(value)
    for stop in stop_labels:
        stop_lower = stop.lower()
        pos = lower_value.find(stop_lower)
        if pos >= 0 and pos < end_idx:
            end_idx = pos
    cleaned = normalize_ws(value[:end_idx]).strip(" :,-#")
    return cleaned


def _find_metadata_block_text(soup: BeautifulSoup) -> str:
    labels = [
        "Planet(s)",
        "Estimated light years from the bubble",
        "Material Type",
        "Mining Type",
        "Overlap #",
        "Thanks to",
    ]
    for node in soup.find_all(True):
        text = normalize_ws(node.get_text(" ", strip=True))
        if not text:
            continue
        if "mining location" in text.lower() and any(label.lower() in text.lower() for label in labels):
            return text
    return normalize_ws(soup.get_text(" ", strip=True))


def collect_label_inventory(html: str, max_lines: int = 50) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    tokens = ("planet", "estimated", "light years", "material", "mining", "overlap", "thanks")
    lines: list[str] = []
    seen: set[str] = set()
    for node in soup.find_all(["th", "td", "dt", "dd", "li", "p", "div", "span", "strong", "b"]):
        text = normalize_ws(node.get_text(" ", strip=True))
        if not text:
            continue
        lowered = text.lower()
        if not any(token in lowered for token in tokens):
            continue
        parent = node.parent.name if getattr(node, "parent", None) is not None else "?"
        snippet = text[:140]
        line = f"{parent}>{node.name}: {snippet}"
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return lines


def parse_location(html: str, source_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    system_name = normalize_ws(h1.get_text(" ", strip=True) if h1 else "")
    if not system_name:
        title = soup.find("title")
        title_text = normalize_ws(title.get_text(" ", strip=True) if title else "")
        if title_text:
            system_name = title_text.split("|")[0].strip()

    label_map = _extract_label_value_map(soup)
    block_text = _find_metadata_block_text(soup)
    stop_labels = [
        "Planet(s)",
        "Estimated light years from the bubble",
        "Material Type",
        "Mining Type",
        "Overlap #",
        "Thanks to:",
    ]
    planets = extract_field_from_text(block_text, "Planet(s)", stop_labels) or _first_label_value(label_map, ["planet"]) or ""
    estimated_raw = extract_field_from_text(
        block_text,
        "Estimated light years from the bubble",
        stop_labels,
    ) or _first_label_value(label_map, ["estimatedlightyears", "estimatedly", "lightyearsfromthebubble"]) or ""
    parse_warning_tags: list[str] = []
    material_type_raw = extract_field_from_text(block_text, "Material Type", stop_labels)
    if not material_type_raw:
        material_type_raw = _first_label_value(label_map, ["materialtype", "materials"]) or ""
    if not material_type_raw:
        parse_warning_tags.append("materials_missing")
    mining_type = extract_field_from_text(block_text, "Mining Type", stop_labels) or _first_label_value(label_map, ["miningtype"]) or None
    overlap = extract_field_from_text(block_text, "Overlap #", stop_labels) or _first_label_value(label_map, ["overlap"]) or None
    thanks_to = _first_label_value(label_map, ["thanksto", "thanks"]) or extract_field_from_text(
        block_text,
        "Thanks to:",
        stop_labels,
    ) or None
    description = _extract_description(soup)
    materials = parse_materials_list(material_type_raw)
    tritium_explicit = 1 if (
        "tritium" in material_type_raw.casefold()
        or any("tritium" in material.casefold() for material in materials)
    ) else 0
    tritium_inferred = 0 if tritium_explicit else 1

    return {
        "source_url": canonicalize_url(source_url),
        "source_category": "tritium",
        "system_name": system_name or "",
        "planets": planets or None,
        "estimated_ly_from_bubble": safe_float(estimated_raw),
        "mining_type": mining_type,
        "overlap": overlap,
        "thanks_to": thanks_to,
        "tritium_explicit": tritium_explicit,
        "tritium_inferred": tritium_inferred,
        "parse_warnings": ",".join(parse_warning_tags) if parse_warning_tags else None,
        "material_type_raw": material_type_raw,
        "materials": materials,
        "description": description,
        "extracted_at_utc": utc_now_iso(),
    }


def is_tritium_explicit(row: dict[str, Any]) -> bool:
    for material in row.get("materials", []):
        if "tritium" in str(material).casefold():
            return True
    if "tritium" in (row.get("material_type_raw") or "").casefold():
        return True
    if "tritium" in (row.get("description") or "").casefold():
        return True
    return False


def apply_tritium_flags(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("tritium_explicit") in (0, 1) and row.get("tritium_inferred") in (0, 1):
        return row
    explicit = 1 if is_tritium_explicit(row) else 0
    row["tritium_explicit"] = explicit
    row["tritium_inferred"] = 0 if explicit else 1
    return row


def save_debug_artifacts(
    out_dir: Path,
    source_url: str,
    html: str,
    parsed_row: dict[str, Any],
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    split = urlsplit(source_url)
    path_parts = [part for part in split.path.split("/") if part]
    slug = path_parts[-1] if path_parts else ""
    stem_hint = slug or parsed_row.get("system_name") or "location"
    stem = _sanitize_filename(str(stem_hint))
    short_hash = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:8]
    base = f"{stem}_{short_hash}"
    html_path = out_dir / f"{base}.html"
    json_path = out_dir / f"{base}.json"
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps(parsed_row, indent=2, ensure_ascii=False), encoding="utf-8")
    return html_path, json_path


def fetch_text(url: str, timeout_s: float = 30.0, user_agent: str = "EDMFI/1.0") -> str:
    req = urllib_request.Request(url, headers={"User-Agent": user_agent})
    with urllib_request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() if resp.headers is not None else None
        encoding = charset or "utf-8"
        return raw.decode(encoding, errors="replace")


def make_session(user_agent: str) -> dict[str, str]:
    return {"user_agent": user_agent}


def fetch_with_retries(
    session: dict[str, str] | object,
    url: str,
    timeout: float,
    sleep_seconds: float,
    verbose: bool,
    max_attempts: int = 5,
) -> str:
    user_agent = (
        session.get("user_agent", DEFAULT_USER_AGENT)
        if isinstance(session, dict)
        else DEFAULT_USER_AGENT
    )
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_text(url=url, timeout_s=timeout, user_agent=user_agent)
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            if status in RETRYABLE_STATUS and attempt < max_attempts:
                backoff = sleep_seconds * (2 ** (attempt - 1))
                if verbose:
                    print(
                        f"retry status={status} attempt={attempt} url={url} sleep={backoff:.2f}s"
                    )
                time.sleep(backoff)
                continue
            raise RuntimeError(f"HTTP {status} for {url}") from exc
        except urllib_error.URLError as exc:
            if attempt >= max_attempts:
                raise RuntimeError(f"Request failed for {url}: {exc}") from exc
            backoff = sleep_seconds * (2 ** (attempt - 1))
            if verbose:
                print(f"retry request error attempt={attempt} url={url} sleep={backoff:.2f}s")
            time.sleep(backoff)
            continue
        except Exception as exc:
            if attempt >= max_attempts:
                raise RuntimeError(f"Request failed for {url}: {exc}") from exc
            backoff = sleep_seconds * (2 ** (attempt - 1))
            if verbose:
                print(f"retry request error attempt={attempt} url={url} sleep={backoff:.2f}s")
            time.sleep(backoff)
            continue

    raise RuntimeError(f"Failed to fetch {url}")


def crawl_listing_urls(
    session: dict[str, str] | object,
    start_url: str,
    max_pages: int,
    sleep_seconds: float,
    timeout: float,
    verbose: bool,
) -> tuple[list[str], int]:
    location_urls: list[str] = []
    seen_locations: set[str] = set()
    seen_listing: set[str] = set()
    base_listing = start_url if start_url.endswith("/") else f"{start_url}/"
    current_url = canonicalize_url(start_url)
    pages_scraped = 0

    for page_num in range(1, max_pages + 1):
        if current_url in seen_listing:
            break
        try:
            html = fetch_with_retries(session, current_url, timeout, sleep_seconds, verbose)
        except RuntimeError as exc:
            msg = str(exc)
            if "HTTP 404" in msg and current_url in msg:
                if verbose:
                    print(f"Reached end of pagination (404): {current_url}")
                break
            raise
        pages_scraped += 1
        seen_listing.add(current_url)

        found = parse_listing(html, current_url)
        for url in found:
            if url in seen_locations:
                continue
            seen_locations.add(url)
            location_urls.append(url)

        next_url = find_next_listing_url(html, current_url)
        if next_url and next_url not in seen_listing:
            current_url = next_url
        else:
            if not found:
                break
            current_url = canonicalize_url(urljoin(base_listing, f"page/{page_num + 1}/"))
            if current_url in seen_listing:
                break

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return location_urls, pages_scraped


def read_existing_source_urls(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    out: set[str] = set()
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            url = row.get("source_url")
            if url:
                out.add(canonicalize_url(url))
    return out


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: ((r.get("system_name") or ""), (r.get("planets") or ""), r["source_url"]))


def write_rows_csv(rows: list[dict[str, Any]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in sort_rows(rows):
            writer.writerow(
                {
                    "source_url": row.get("source_url"),
                    "source_category": row.get("source_category"),
                    "system_name": row.get("system_name"),
                    "planets": row.get("planets"),
                    "estimated_ly_from_bubble": row.get("estimated_ly_from_bubble"),
                    "mining_type": row.get("mining_type"),
                    "overlap": row.get("overlap"),
                    "thanks_to": row.get("thanks_to"),
                    "tritium_explicit": row.get("tritium_explicit"),
                    "tritium_inferred": row.get("tritium_inferred"),
                    "parse_warnings": row.get("parse_warnings"),
                    "material_type_raw": row.get("material_type_raw"),
                    "materials_json": json.dumps(row.get("materials", []), ensure_ascii=False),
                    "description": row.get("description"),
                    "extracted_at_utc": row.get("extracted_at_utc"),
                }
            )


def _ensure_known_sites_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS known_sites_edmining (
            source_url TEXT PRIMARY KEY,
            source_category TEXT,
            system_name TEXT NOT NULL,
            planets TEXT,
            estimated_ly_from_bubble REAL,
            mining_type TEXT,
            overlap TEXT,
            thanks_to TEXT,
            tritium_explicit INTEGER NOT NULL DEFAULT 0,
            tritium_inferred INTEGER NOT NULL DEFAULT 0,
            parse_warnings TEXT,
            material_type_raw TEXT,
            materials_json TEXT,
            description TEXT,
            extracted_at_utc TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(known_sites_edmining)").fetchall()}
    alters: list[str] = []
    if "source_category" not in cols:
        alters.append("ALTER TABLE known_sites_edmining ADD COLUMN source_category TEXT")
    if "tritium_explicit" not in cols:
        alters.append("ALTER TABLE known_sites_edmining ADD COLUMN tritium_explicit INTEGER NOT NULL DEFAULT 0")
    if "tritium_inferred" not in cols:
        alters.append("ALTER TABLE known_sites_edmining ADD COLUMN tritium_inferred INTEGER NOT NULL DEFAULT 0")
    if "parse_warnings" not in cols:
        alters.append("ALTER TABLE known_sites_edmining ADD COLUMN parse_warnings TEXT")
    for ddl in alters:
        conn.execute(ddl)


def load_into_db(db_path: Path, rows: list[dict[str, Any]]) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        _ensure_known_sites_table(conn)
        payload = [
            (
                row.get("source_url"),
                row.get("source_category"),
                row.get("system_name") or "",
                row.get("planets"),
                row.get("estimated_ly_from_bubble"),
                row.get("mining_type"),
                row.get("overlap"),
                row.get("thanks_to"),
                row.get("tritium_explicit", 0),
                row.get("tritium_inferred", 0),
                row.get("parse_warnings"),
                row.get("material_type_raw"),
                json.dumps(row.get("materials", []), ensure_ascii=False),
                row.get("description"),
                row.get("extracted_at_utc"),
            )
            for row in sort_rows(rows)
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO known_sites_edmining (
                source_url,
                source_category,
                system_name,
                planets,
                estimated_ly_from_bubble,
                mining_type,
                overlap,
                thanks_to,
                tritium_explicit,
                tritium_inferred,
                parse_warnings,
                material_type_raw,
                materials_json,
                description,
                extracted_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.commit()
    return len(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract EDMining Tritium locations for Ring Hunter.")
    parser.add_argument("--out-csv", default=str(DEFAULT_OUT_CSV))
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--db", default=None, help="Optional SQLite DB path for upsert.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--debug-save-html", default=None, help="Optional directory to save raw HTML + parsed JSON.")
    parser.add_argument("--debug-save-n", type=int, default=2, help="Max number of pages to save in debug mode.")
    parser.add_argument("--debug-one-url", default=None, help="Scrape only this location URL (skip listing crawl).")
    parser.add_argument("--debug-dump-labels", action="store_true", help="Print compact label inventory per page.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_csv = Path(args.out_csv)
    existing = read_existing_source_urls(out_csv) if args.resume else set()
    session = make_session(args.user_agent)

    if args.debug_one_url:
        urls = [canonicalize_url(args.debug_one_url)]
        pages_scraped = 0
    else:
        urls, pages_scraped = crawl_listing_urls(
            session=session,
            start_url=DEFAULT_LISTING_URL,
            max_pages=args.max_pages,
            sleep_seconds=args.sleep,
            timeout=args.timeout,
            verbose=args.verbose,
        )
        if args.limit is not None:
            urls = urls[: args.limit]

    rows: list[dict[str, Any]] = []
    pages_attempted = 0
    explicit_count = 0
    inferred_count = 0
    missing_materials_count = 0
    debug_saved = 0
    debug_dir = Path(args.debug_save_html) if args.debug_save_html else None
    for url in urls:
        canonical = canonicalize_url(url)
        if canonical in existing:
            if args.verbose:
                print(f"resume skip source_url={canonical}")
            continue
        html = fetch_with_retries(
            session=session,
            url=canonical,
            timeout=args.timeout,
            sleep_seconds=args.sleep,
            verbose=args.verbose,
        )
        pages_attempted += 1
        row = apply_tritium_flags(parse_location(html, canonical))
        if args.debug_dump_labels:
            print(f"Label inventory for {canonical}:")
            for line in collect_label_inventory(html, max_lines=50):
                print(f"  {line}")
        if debug_dir is not None and debug_saved < max(args.debug_save_n, 0):
            html_path, json_path = save_debug_artifacts(debug_dir, canonical, html, row)
            debug_saved += 1
            if args.verbose:
                print(f"debug saved html={html_path} json={json_path}")
        rows.append(row)
        if row.get("tritium_explicit") == 1:
            explicit_count += 1
        else:
            inferred_count += 1
        warnings = row.get("parse_warnings") or ""
        if "materials_missing" in warnings:
            missing_materials_count += 1
        if args.sleep > 0:
            time.sleep(args.sleep)

    loaded = 0
    if not args.dry_run:
        write_rows_csv(rows, out_csv)
        if args.db:
            loaded = load_into_db(Path(args.db), rows)

    print(
        f"urls_found={len(urls)} pages_scraped={pages_scraped} "
        f"location_pages_scraped={pages_attempted} rows_kept={len(rows)} rows_loaded={loaded}"
    )
    print(
        f"rows_total_scraped={pages_attempted} rows_kept={len(rows)} "
        f"explicit_count={explicit_count} inferred_count={inferred_count} "
        f"missing_materials_count={missing_materials_count}"
    )
    if args.dry_run:
        print("dry-run enabled: no CSV/DB writes performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
