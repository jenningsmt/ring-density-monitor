# Top Rings (Sector + Journal Merge)

## Purpose
`scripts/top_rings.py` builds the Top N (default 10) rings per category for a target sector by combining:
1. **Sector DB rings** (historical baseline).
2. **Journal rings** (expedition discoveries since a given date).

The output is a new SQLite DB that contains all system data for any system with a Top N ring and includes
ranked tables and flags for easy filtering.

## Flow (Two-Phase)
1. **Baseline pass (sector DB):**
   - Discover schema dynamically using `sqlite_master` + `PRAGMA table_info`.
   - Identify ring/system/body tables and columns.
   - Compute surface density when not stored.
   - Build initial Top N per category (ROCKY, METAL_RICH, METALLIC, ICY).
2. **Journal merge (since date):**
   - Stream journal files line-by-line.
   - Filter to systems present in the sector DB.
   - Compute surface density from `MassMT`, `InnerRad`, `OuterRad`.
   - Replace the lowest Top N entry when a journal ring exceeds it.

## Usage
Interactive:
```bash
python -m scripts.top_rings
```

Non-interactive:
```bash
python -m scripts.top_rings --sector eotchorts
```

## Flags and Defaults
- `--sector` (prompt if missing)
- `--sector-library-dir` (default: `data/sector_library`)
- `--journals-dir` (default: `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\`)
- `--since` (default: `2026-01-01`)
- `--limit` (default: `10`, must be >= 1)
- `--export-csv PATH` (optional CSV export of ranked results)
- `--anchor-system NAME` (default: `Eotchorts FG-X d1-318`)
- `--anchor-coords x,y,z` (override anchor coordinates directly, e.g. `100.5,-20.3,45.0`)
- `--verbose` (extra detail in output)
- `--quiet` (suppress tables + counters)

## Output DB
Path:
```
data/sector_library/top_rings_<sector>.sqlite
```

Tables / Views:
- `top_rings_ranked`
  - `sector`, `category`, `rank`
  - `system_name`, `body_name`, `ring_name`
  - `surface_density`, `distance_to_anchor_ly`
  - `mapped_journal`, `mapped_db`, `mapped_final`
  - `source` (`sector_db` or `journal`)
  - `updated_at`
- `top_ring_flags`
  - `(system_name, body_name, ring_name)` keyed flags with `category`, `rank`
- `run_metadata`
  - Key/value store recording: `sector`, `anchor_system`, `anchor_x/y/z`, `since`, `limit`, `run_timestamp`, counter summaries
- `rings_with_top_flags` (view, if ring table columns allow join)

## Mapping Logic
Primary mapping signal is **journal** `SAAScanComplete` (within the `--since` window).

Optional DB-derived signal is used when possible:
- The script looks for likely mapping columns on body or ring tables:
  - `mapped`, `is_mapped`, `was_mapped`, `dss`, `saas`, `mapping_state` (and variants)
- If found, it flags those bodies as `mapped_db = 1`.

Final mapping field:
```
mapped_final = mapped_journal OR mapped_db
```

## Troubleshooting
- **Missing columns / schema not found**
  - The script searches dynamically; if it cannot find system/body/ring or density fields, it will exit with
    a clear error. Confirm your sector DB schema and ensure ring data exists.
- **Density computed**
  - If no surface density column exists, it will compute:
    `density = mass / (pi * (outer_radius^2 - inner_radius^2))`
  - Missing mass/radii or invalid radii (outer <= inner) are skipped and counted in counters.
- **No journal matches**
  - The script only merges journal rings whose `StarSystem` is in the sector DB. If none match, only the
    sector DB baseline is used.
  - Check `--since` and `--journals-dir` if you expect matches.
  - Journal files predating `--since` are skipped by filename (no I/O wasted on old files).
- **Anchor system not found**
  - Use `--anchor-coords x,y,z` to provide coordinates directly when the default anchor is absent.
  - Use `--anchor-system` to change the anchor system name for a different sector.
- **Integer primary keys in sector DB**
  - System/body keys are handled as both TEXT and INTEGER; no schema changes needed.
