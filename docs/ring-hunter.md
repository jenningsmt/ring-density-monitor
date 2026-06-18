# Ring Hunter (Internal EDMFI Specification)

## 1. Purpose

Ring Hunter is a large-scale analytical subsystem within the EDMFI architecture designed to:

- Ingest and normalize galactic-scale planetary ring data (~52M rings)
- Compute deterministic ring-level ranking metrics (MOI₀ baseline)
- Identify statistically extreme Icy and Metallic rings
- Construct expedition-ready cohorts aligned with operational constraints
- Provide global statistical baselines (Galactic Sigma Norm)
- Enable empirical validation and scoring refinement

This document defines the internal architecture, governance rules, data contracts, and operational procedures for the Ring Hunter subsystem.

---

# 2. Operational Problem

Deep-space Fleet Carrier operations depend on reliable Tritium supply, primarily mined from high-quality Icy rings. Metallic rings (Platinum) are economically significant but operationally secondary.

Explorers operating outside prepared sector baselines require galactic-scale statistical context to determine:

- Whether a newly discovered ring is statistically exceptional
- Whether it merits DSS mapping
- Whether it belongs to expedition-relevant cohorts

Ring Hunter provides that context via deterministic ranking and global normalization.

---

# 3. Data Provenance

Primary Source:
- Spansh galaxy dump (`galaxy.json.gz`)

Ingest Snapshot:
- Systems scanned: 180,014,762
- Rings ingested: 52,411,250
- Ingest date: 2026-02-13

Inclusion Criteria:
- ring_type ∈ {Icy, Metallic, Metal Rich, Rocky}
- surface_density IS NOT NULL
- Deterministic ring_id = SHA1(system_name + body_name + ring_index)

Exclusions:
- No sector-level curated subsets
- No post-ingest mutation of raw data

All ingest operations are isolated to:

```
data/ring_hunter_library/rings_master.sqlite
```

---

# 4. Database Architecture

## 4.1 Core Tables

### rings_raw (Immutable Post-Ingest)

Contains extracted and derived physical metrics.

| Column | Type | Source | Required for MOI₀ |
|--------|------|--------|-------------------|
| ring_id | TEXT | Deterministic SHA1 | Yes |
| ring_type | TEXT | Raw | Yes |
| surface_density | REAL | Derived | Yes |
| linear_density | REAL | Derived | Yes |
| arrival_distance_ls | REAL | Raw | Optional |
| parent_body_gravity | REAL | Raw | Optional |
| inner_radius | REAL | Raw | No |
| outer_radius | REAL | Raw | No |
| ring_width | REAL | Derived | No |
| ring_area | REAL | Derived | No |

Policy:
- No scoring fields allowed in this table.
- No mutation after ingest.

---

### rings_scored (Versioned Scoring Layer)

Stores computed ranking metrics.

Key Fields:
- score_version (TEXT)
- moi_raw (REAL)
- moi_normalized (REAL)
- moi_final (REAL)
- ssd_score (REAL, reserved)
- normalization metadata
- flags

Policy:
- All ranking logic writes here.
- score_version must increment for any scoring change.
- Historical versions must not be deleted.

---

### ring_survey (Empirical Validation Layer)

Reserved for expedition-ground-truth observations.

Planned Use:
- DSS mapping confirmation
- Tritium yield notes
- Platinum yield notes
- Anomaly tracking

Policy:
- ring_survey never feeds back into rings_raw directly.
- Scoring refinements occur via new score_version.

---

# 5. MOI₀ Baseline Model

MOI₀ combines:
- surface_density (70%)
- linear_density (25%)
- arrival-distance term (5%)

Normalization:
- Deterministic ordering: ORDER BY moi_raw ASC, ring_id ASC
- Percentile normalization across full eligible population

MOI₀ is designed to identify statistical outliers for empirical validation — not guarantee yield.

---

# 6. Tiered Cohort Framework

## 6.1 Tier 1 – Icy Core Cohort

Primary expedition targets.

Definition:

IcyCore = { r ∈ Icy | MOI₀(r) ≥ θ_Icy }

Calibration Rule:

θ_Icy must satisfy:

1. Cohort size ∈ [500, 1500]
2. Percentile ≥ 99.99
3. Deterministic ordering: ORDER BY moi_raw DESC, ring_id ASC

Selected θ_Icy and resulting cohort size must be logged.

---

## 6.2 Tier 2 – Proximate Metallic Cohort

Secondary targets.

MetProx = { m ∈ Metallic | MOI₀(m) ≥ θ_Met AND d(m, IcyRoute) ≤ R }

Where:
- θ_Met restricts to extreme tail
- R ∈ [250, 500] LY

Metallic rings are included only when spatially efficient.

---

# 7. Galactic Sigma Norm

Global statistical baselines are computed per ring_type and metric.

Example:

Gσ = (surface_density − μ) / σ

Purpose:
- In-flight DSS triage
- Context outside prepared sectors

Global norms must:
- Record population size
- Record filtering rules
- Be versioned
- Be reproducible

---

# 8. Indexing Strategy

rings_raw:
- idx_rings_raw_ring_type_sd_desc_ring_id
  Supports deterministic SD cutoff queries

rings_scored:
- idx_rings_scored_version_type_moi_raw_desc_ring_id
  Supports cohort tail selection

Dropped:
- idx_rings_raw_ring_id (redundant with PK)

Index policy:
- No index created without documented query justification
- Remove redundant indexes after validation

---

# 9. Determinism Requirements

All ranking and cutoff operations must use:

ORDER BY value DESC, ring_id ASC

No implicit ordering.
No reliance on undefined SQLite behavior.

---

# 10. Performance Profile

- ~52M rows in rings_raw
- ~52M rows in rings_scored
- WAL ingest mode
- Deterministic recompute ~92 minutes
- Vacuumed, freelist_count = 0
- DB size ~36GB

SQLite is acceptable at this scale with disciplined indexing.

---

# 11. Integration with EDMFI

## 11.1 SysComp Pane

Displays:
- Galactic Sigma Norm
- Percentile context
- Expedition-relevance flags

No sector baseline required.

---

## 11.2 Expedition Planner

Produces:
- IcyCore manifest
- Spatial clustering
- Route sequencing
- Metallic proximity set

Outputs are deterministic and reproducible.

---

## 11.3 Empirical Feedback Loop

Post-expedition:
- Populate ring_survey
- Compare observed yield vs MOI ranking
- Refine SSD heuristics
- Publish new score_version

Ring Hunter is a closed validation loop.

---

# 12. Governance & Guardrails

Forbidden:
- Writing scoring fields into rings_raw
- Silent 0-row exports
- Non-deterministic ORDER BY
- Coupling to sector baseline DB
- Deleting historical score_version rows

All changes must preserve:
- Determinism
- Reproducibility
- Version traceability

---

# 13. Rebuild / Execution Guide

1. Ingest

python -m scripts.rings.ingest_rings_master ...

2. Recompute MOI

python -m scripts.rings.recompute_moi ...

3. Build indexes

python -m scripts.rings.build_ring_indexes ...

4. Compute global norms

python -m scripts.rings.compute_global_norms ...

---

# 14. Roadmap

Phase 2 – Baseline ingest + MOI (Complete)
Phase 3 – Global norms + cohort calibration (Next)
Phase 4 – Route sequencing engine
Phase 5 – Metallic proximity selection
Phase 6 – Empirical validation & SSD refinement

---

# 15. Summary

Ring Hunter transforms galactic-scale raw ring data into a deterministic expedition architecture.

It provides:
- A ranking engine
- A statistical baseline
- A cohort construction system
- A validation feedback loop

This subsystem bridges large-scale statistical modeling with long-duration operational exploration logistics inside EDMFI.

