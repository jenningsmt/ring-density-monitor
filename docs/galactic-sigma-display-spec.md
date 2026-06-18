# Galactic Sigma Display — Implementation Spec

**Feature:** Add Galactic Sigma (σG) alongside Sector Sigma (σS) on ring rows
in the System Composition pane.

**Status:** Not yet implemented. Sector Sigma (σS) is live.

**Priority:** Near-term; identified in `docs/QA_PRIORITIZATION.md` Works in Progress.

---

## Player-Facing Goal

Both sigma values measure the same thing — how this ring's surface density
compares to the median density for its type — but against different reference
populations.

**σS (Sector Sigma):** Reference population = all rings of this type discovered
within the same sector. Tells the player how this ring ranks locally.

**σG (Galactic Sigma):** Reference population = all rings of this type discovered
across the entire galaxy. Tells the player how this ring ranks galaxy-wide.

The two values will almost never be equal; they are measuring against different
populations and the coincidence of matching is extremely unlikely. Together they
let the player answer two questions at once:

- "Is this ring good for this region?" (σS)
- "Is this ring good, full stop?" (σG)

This matters most when evaluating a newly-discovered ring in an unexplored sector,
where the sector reference class is small and σS alone gives an incomplete picture.
A ring with a high σS but mediocre σG is a local standout in a sparse area; a ring
with high both is genuinely exceptional.

---

## Display Specification

**Current format (live):**

```
[Ring name / type]  •  σ=2.83 (+183%)
```

**Target format:**

```
[Ring name / type]  •  σS 2.830 (+183%)  σG 1.247 (+25%)
```

Rules:

- Both sigma values appear on the same line, space-separated.
- Label: `σS` for Sector Sigma, `σG` for Galactic Sigma.
- Value: three decimal places (e.g., `2.830`, `1.247`).
- Percentage: sign-prefixed, no decimal, in parentheses (e.g., `(+183%)`, `(-12%)`).
- Both σS and σG percentages express the same thing: how this ring's surface
  density compares to the median density of the reference population for its type.
  The formula structure is identical for both — the only difference is the norm
  inputs (sector norms for σS, galactic norms for σG). Before writing any σG
  code, inspect `core/ring_analysis.py` to confirm the exact percentage formula
  used for σS, then apply the same formula with `global_norms` values as input.
  The two percentage outputs will rarely be equal — they measure against entirely
  different populations — but the calculation method must be consistent.
- If σG data is unavailable for a ring type (e.g., the `global_norms` table has
  no row for that type), suppress σG entirely rather than showing a placeholder.
  Never show `σG N/A` or `σG —` to the player.
- Use the same color/styling conventions as σS.

---

## Data Source

**Database:** `data/ring_hunter_library/rings_master_<date>.sqlite`

This file is read-only. Never write to it.

**Table:** `global_norms`

This table holds four rows — one per ring type — containing the galaxy-wide mean
and standard deviation of surface density for that ring type. These values are
the output of `scripts/compute_global_norms.py` and are static between Ring Hunter
pipeline runs.

**Pre-implementation task:** Before writing any code, inspect the `global_norms`
table schema:

```bash
# Substitute the actual dated filename
sqlite3 "data/ring_hunter_library/rings_master_<date>.sqlite" \
  ".schema global_norms"

sqlite3 "data/ring_hunter_library/rings_master_<date>.sqlite" \
  "SELECT * FROM global_norms LIMIT 10;"
```

Confirm: the column names for ring type, mean, and standard deviation before
writing any access code. Do not assume column names — pattern-anchor all queries
to actual schema.

**Expected ring types** (confirm against actual table values):

| Ring Type | Expected key |
|-----------|-------------|
| Icy | `Icy` (or `icy`) |
| Metallic | `Metallic` (or `metallic`) |
| Metal-Rich | `Metal Rich` or `MetalRich` |
| Rocky | `Rocky` (or `rocky`) |

These must match whatever keys appear in the `global_norms` table AND whatever
ring type labels are produced by the journal parser / stored in `BodyContext`.
A mismatch will silently suppress σG for that type.

---

## DB Filename Discovery

The master database uses a date-stamped filename
(`rings_master_2026-02-13.sqlite`). The code must not hard-code the date.

**Recommended approach:** At startup, glob `data/ring_hunter_library/rings_master_*.sqlite`
and select the most recently modified file. If no file is found, log a warning
and disable σG display silently (fall back to σS-only display — do not surface
an error to the player).

---

## Architecture Constraints

### Offline core boundary
`core/` and `mfi_io/` have no network dependencies. Reading from a **local**
SQLite file is permitted. However, the rings_master DB lookup must NOT be placed
in the System Aid's real-time event handler. Read the four `global_norms` values
once at startup and cache them in memory. Do not re-query per event.

### Where to make changes
Confine changes to the smallest responsible set of modules:

| Module | Expected change |
|--------|----------------|
| `core/ring_analysis.py` | Add `compute_galactic_sigma(surface_density, ring_type, global_norms)` function |
| `core/models.py` | Add `galactic_sigma` field to `RingContext` (or equivalent) if not already present |
| `app/ui_app.py` or startup path | Load `global_norms` from DB once; pass to ring analysis |
| `app/ui_adapters.py` | Include `galactic_sigma` in ring display dict |
| Relevant UI pane | Render `σG` alongside `σS` using the format above |

Do not modify `planner_strategic/`, `intel/`, `scripts/`, or any persistence
layer as part of this feature.

### Determinism
`compute_galactic_sigma()` must be a pure function: identical inputs must always
produce identical outputs. No randomness, no timestamps, no floating-point
non-determinism. If the input `global_norms` values are floats loaded from
SQLite, ensure they are loaded consistently (e.g., read as `REAL`, not as text).

### Roleplay-first UI
`σS` and `σG` are acceptable in-universe labels (sigma notation is natural to a
ship's scanner readout). Do not use `sector_sigma`, `galactic_sigma`, `norm`,
`global_mean`, or any internal variable names in the rendered output.

---

## Test Requirements

1. **Unit test for `compute_galactic_sigma`:** Cover at least:
   - Ring type with a known global norm → correct σG value and percentage
   - Ring type not present in global_norms → returns `None` (not an exception)
   - Surface density equal to global mean → σG of 0.0 (0%)
   - Surface density above mean → positive σG and positive percentage
   - Surface density below mean → negative σG and negative percentage

2. **Adapter test:** Verify that the display dict produced by `ui_adapters.py`
   includes the correctly formatted `σG` string (three decimal places,
   sign-prefixed percentage) when global norms are available.

3. **Fallback test:** Verify that if `global_norms` is empty or the DB file is
   absent, the ring row renders with σS only and does not raise an exception or
   show any error text.

4. **Run full test suite** (`python -m unittest -q`) before and after. No
   regressions permitted.

---

## Pre-Commit Checklist

- [ ] `global_norms` schema inspected; column names confirmed
- [ ] Ring type key mapping verified against journal parser output
- [ ] σG percentage convention confirmed to match σS percentage convention
- [ ] DB filename discovery implemented (glob, not hard-coded date)
- [ ] `global_norms` loaded once at startup, not per event
- [ ] `compute_galactic_sigma()` is a pure function
- [ ] Unit tests cover all cases above
- [ ] Full test suite green
- [ ] Ring row output reviewed visually: does it make sense to a player?
- [ ] No developer terminology visible in the UI

---

## Related Files

| File | Relevance |
|------|-----------|
| `core/ring_analysis.py` | Existing σS implementation; reference for σG |
| `core/models.py` | `RingContext` data type |
| `app/ui_adapters.py` | Ring row display dict assembly |
| `scripts/compute_global_norms.py` | Produces `global_norms` table |
| `ARCHITECTURE.md` §3 | Ring Hunter subsystem overview |
| `docs/RING_RANKER_SPEC.md` | Ring ranking design context |
| `docs/RING_HUNTER_SPEC.md` | Ring Hunter pipeline spec |
