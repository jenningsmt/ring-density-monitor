# Ring Ranker Methodology — v1.0 Technical Description

This document specifies the **ring ranking and DSS tasking methodology** used in Project Galtea to identify, score, and prioritize planetary rings for resource exploitation (fuel, industry, and luxury) in Elite Dangerous.

The method is designed to be:

- **Explainable** (no opaque heuristics)
- **Repeatable** (deterministic scoring)
- **Extensible** (supports new signals like hotspots and reserve levels)
- **Operational** (outputs are directly usable for DSS flight planning)

---

## 1. Design Principles

### 1.1 Goals

The ring ranker is intended to:
- Identify the **best candidate rings** for:
  - Fuel production (Tritium class)
  - Industrial metals (Platinum/Osmium/Painite class)
  - Luxury commodities (LTD/Alexandrite/Monazite class)
- Provide a **priority-ordered DSS tasking list**
- Support human-in-the-loop validation (visual hotspot overlap checks)

### 1.2 What this system does NOT assume

The ranker does **not** assume:
- Hotspots exist (they require DSS)
- Reserve levels are known
- Mining yield directly equals mass

Instead, it operates on:
- **Physical ring properties**
- **Proximity**
- **Known exploration state**

And upgrades itself when new data appears.

---

## 2. Data Dependencies

### 2.1 Authoritative source

The ring ranker operates on the **working SQLite database**, built from:

- Read-only sector extract (e.g., `sector_eotchorts.sqlite`)
- Journal enrichment (optional)

Minimum required fields:

From `rings` or derived table:
- `system_address`
- `system_name`
- `body_name`
- `ring_name`
- `ring_class`
- `mass_mt` (ring mass)
- `inner_radius_m`
- `outer_radius_m`

From `systems`:
- `x`, `y`, `z` (coordinates)

From capital config:
- `capital_system_name`

Optional:
- hotspot presence (later)
- reserve level (later)

---

## 3. Core Physical Metric: Surface Density (RMOI)

### 3.1 Definition

The fundamental physical metric is **surface density**, used as a proxy for resource richness:

\[
\text{surface_density}_{kg/m^2} = \frac{mass_{kg}}{\pi \cdot (r_{outer}^2 - r_{inner}^2)}
\]

Where:
- `mass_kg = mass_mt * 1e6`
- radii are in meters

### 3.2 Rationale

Surface density approximates:
- mass per unit area
- likelihood of dense asteroid populations
- relative mining potential independent of ring size

This metric is:
- dimensionally meaningful
- consistent across ring classes
- computable from known data

---

## 4. Derived Quantities

### 4.1 Log scaling

Because surface density varies across orders of magnitude:

\[
sd\_log10 = \log_{10}(\text{surface_density})
\]

### 4.2 Normalization

To allow combination with other scores:

\[
sd\_norm = \frac{sd\_log10 - \min(sd\_log10)}{\max(sd\_log10) - \min(sd\_log10)}
\]

Where min/max are computed over all candidate rings in-scope.

---

## 5. Distance Metric

### 5.1 Definition

Distance is computed from the capital system:

\[
distance\_ly = \sqrt{(x-x_0)^2 + (y-y_0)^2 + (z-z_0)^2}
\]

Where `(x0, y0, z0)` are capital coordinates.

### 5.2 Proximity score

Distance is inverted into a normalized proximity score:

\[
prox = 1 - \frac{distance\_ly}{radius\_ly}
\]

Clamped to `[0,1]`.

This ensures:
- closer rings rank higher
- distant rings are penalized but not excluded

---

## 6. Ring Role Classification

Each ring is assigned **role tags** based on ring class:

| Role      | Eligible Ring Classes             |
|-----------|----------------------------------|
| fuel      | Icy                              |
| industry  | Metal Rich, Metallic             |
| luxury    | Icy, Rocky                       |

This mapping is deterministic and configurable.

Rings may belong to multiple roles (e.g., Icy rings = fuel + luxury).

---

## 7. Pristine / Reserve Scoring

### 7.1 Input

Reserve level is extracted if present:
- from `rings.reserve_level`
- or from raw JSON
- or from journal enrichment

Possible values:
- `Pristine`
- `Major`
- `Common`
- `Depleted`
- `None` / unknown

### 7.2 Mapping

| Reserve Level | pristine_score |
|---------------|----------------|
| Pristine      | 1.0            |
| Major         | 0.7            |
| Common        | 0.4            |
| Depleted      | 0.1            |
| Unknown       | 0.5            |

This ensures:
- unknown is neutral, not punitive
- system automatically improves when reserve data appears

---

## 8. Hotspot Signal (Future Signal)

Hotspot data is binary at this stage:
- `hotspot_known = 1` if DSS reveals any hotspot
- `0` otherwise

Hotspot score:
- `hotspot_score = 1` if known
- `0` if unknown

(Overlaps are handled via manual logging and later enrichment.)

---

## 9. Composite Scoring Model

Each ring receives a composite score:

\[
score\_total =
w_{sd} \cdot sd\_norm
+ w_{pristine} \cdot pristine\_score
+ w_{hotspot} \cdot hotspot\_score
+ w_{prox} \cdot prox
\]

Default weights (v1.0):

| Component        | Weight |
|------------------|--------|
| sd_norm          | 0.40   |
| pristine_score   | 0.25   |
| hotspot_score    | 0.15   |
| prox             | 0.20   |

Sum = 1.0

These weights are configurable.

---

## 10. DSS Tasking Logic

### 10.1 Task eligibility

A ring is tasked if:
- `hotspot_known == 0`
- ring_kind == 'ring' (belts excluded)
- ring_class is eligible for at least one role

### 10.2 Task priority

Task priority is defined as:

\[
task\_priority = score\_total \cdot (1 + sd\_norm)
\]

This biases toward:
- dense rings
- close rings
- pristine rings

### 10.3 Why-tasked explanation

Each task includes a human-readable reason:

Example:
sd_norm=0.94, pristine=0.50, prox=0.75

This makes ranking auditable.

---

## 11. Outputs

### 11.1 Ranked role lists

Generated CSVs:
- `ranked_fuel_rings.csv`
- `ranked_industry_rings.csv`
- `ranked_luxury_rings.csv`

Fields:
- ring_key
- system_name
- body_name
- ring_name
- ring_class
- distance_ly
- surface_density_kg_m2
- sd_log10
- sd_norm
- reserve_level
- pristine_score
- hotspot_known
- hotspot_score
- prox
- score_total

Sorted:
- `score_total DESC`

---

### 11.2 DSS Tasking List

`dss_tasking_list.csv`

Fields:
- task_priority_rank
- system_name
- system_address
- distance_ly
- body_name
- ring_name
- ring_class
- role_tags
- score_total
- task_priority
- why_tasked

Sorted:
- `task_priority DESC`

Secondary grouping is applied later by operator.

---

## 12. Belt Handling

Asteroid belts and belt clusters:
- appear in journal data
- are NOT included in `ring_features`
- are handled separately via `belt_features` and `belt_clusters`

Reason:
- belts do not produce DSS hotspots
- mining mechanics differ

---

## 13. Reproducibility

The pipeline is deterministic:
- fixed normalization
- no random sampling
- all thresholds configurable

Given:
- same source DB
- same config
- same capital system

It will produce identical rankings.

---

## 14. Known Limitations

- Surface density is a proxy, not a yield simulator
- Hotspot overlap must be human-observed
- Reserve levels may be missing
- Mining meta may change

Mitigations:
- manual hotspot overlap log
- journal enrichment
- weight tuning
- pipeline modularity

---

## 15. Implementation Checklist

- [ ] Build ring_features table
- [ ] Compute surface density
- [ ] Compute distance to capital
- [ ] Assign role tags
- [ ] Compute pristine_score
- [ ] Compute composite score
- [ ] Filter belts
- [ ] Generate ranked role CSVs
- [ ] Generate DSS tasking CSV
- [ ] Document config

---

## 16. Summary

The Galtea ring ranker converts:
raw astrophysical data → normalized metrics → weighted composite score → operational DSS tasking

It provides:
- scientific grounding (density, distance)
- gameplay grounding (roles, hotspots, reserves)
- operator usability (CSV tasking)

And is designed to evolve as:
- you collect hotspot overlap truth
- reserve data becomes available
- mining strategies change
