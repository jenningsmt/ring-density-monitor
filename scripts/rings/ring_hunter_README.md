# Ring Hunter -- Internal README

## Overview

**Ring Hunter** is a deterministic, large-scale planetary ring analytics
subsystem within the EDMFI project.

It ingests full-galaxy datasets, computes normalized ring quality
metrics, and produces statistically elite candidate cohorts for
long-duration exploration and empirical validation missions.

Primary current objective:

-   Identify extreme-tail **Icy** and **Metallic** rings under the MOI₀
    baseline model
-   Construct mission-feasible Tier 1 / Tier 2 cohorts
-   Support long-range Fleet Carrier expedition planning
-   Provide Galactic Sigma decision support during live exploration

Ring Hunter operates independently of sector-level baseline databases
and is designed to scale to full-galaxy ingestion (\~52M rings).

------------------------------------------------------------------------

## What This Subsystem Does

### 1. Full-Galaxy Ring Ingest

Script:

    python -m scripts.rings.ingest_rings_master

Outputs: - `rings_master_YYYY-MM-DD.sqlite`

Core tables: - `rings_raw` - `rings_scored` - `ring_survey`
(intentionally empty pre-expedition)

Derived metrics computed at ingest: - `ring_width` - `ring_area` -
`surface_density` - `linear_density` - deterministic `ring_id` (SHA1)

All work is isolated to:

    data/ring_hunter_library/

------------------------------------------------------------------------

### 2. MOI₀ Scoring

Script:

    python -m scripts.rings.recompute_moi --db <DB> --score-version moi_v1

Properties: - Fully deterministic - Versioned scoring
(`score_version`) - 52M+ rows supported - Writes to `rings_scored`

After scoring: - `moi_final` ∈ \[0,1\] - Galactic distribution available
for sigma calibration

------------------------------------------------------------------------

### 3. Indexing & Performance

Script:

    python -m scripts.rings.build_ring_indexes --analysis

Key analysis index:

    idx_rings_scored_ring_type_moi_final_desc_notnull

Optimizes:

    WHERE ring_type=?
    ORDER BY moi_final DESC

Database integrity checks:

    PRAGMA quick_check;
    ANALYZE;

------------------------------------------------------------------------

### 4. Report Generation

Script:

    python -m scripts.rings.ring_hunter_reports \
      --db <DB> \
      --out <DIR> \
      --top <N> \
      --score-version moi_v1 \
      export-top-metallic

and

    export-top-icy-ssd

Behavior: - Joins `rings_scored` and `rings_raw` - Enforces
deterministic ordering - Fails loudly if SSD score not computed - No
silent zero-row writes

------------------------------------------------------------------------

## Mission Framework (Current Design)

The expedition uses a two-tier cohort structure:

### Tier 1 -- Icy Core Cohort

Defined by:

    MOI₀ ≥ θ_Icy

θ_Icy is selected post-distribution analysis to: - Restrict to extreme
statistical tail - Yield operationally feasible cohort size
(\~500--1500)

This forms the expedition backbone.

------------------------------------------------------------------------

### Tier 2 -- Proximate Metallic Cohort

Defined by:

    MOI₀ ≥ θ_Met
    AND
    distance_to_IcyRoute ≤ R

Metallic rings are secondary objectives, included only if spatially
efficient relative to the Tier 1 route.

------------------------------------------------------------------------

## Integration with EDMFI

Ring Hunter integrates with EDMFI in three ways:

### 1. SysComp Pane -- Galactic Sigma Norm

During live exploration:

When FSS identifies an Icy or Metallic ring: - Compute surface density -
Normalize against Galactic SD distribution - Display:

    Gσ 1.4508 (+45%)

This supports real-time DSS decision-making outside prepared sectors.

If sector baseline exists: - Display both Sector Sigma and Galactic
Sigma.

------------------------------------------------------------------------

### 2. Expedition Planning Layer

Ring Hunter outputs: - Tier 1 Icy Core - Tier 2 Proximate Metallic

These feed: - Route clustering - Spatial ordering - Fleet Carrier
staging logic

------------------------------------------------------------------------

### 3. Empirical Validation Loop

`ring_survey` table is reserved for:

-   In-person DSS validation
-   Mining yield notes
-   Tritium empirical performance
-   Platinum validation metrics

It is intentionally empty prior to expedition execution.

------------------------------------------------------------------------

## Determinism & Governance

Ring Hunter adheres to the following rules:

-   No coupling to sector baseline databases
-   No merging Metallic and Metal Rich ring types
-   All scoring versioned
-   All exports deterministic
-   Schema stability required once published
-   Large-scale changes require SPEC update

Authoritative specification:

    See: Ring Hunter SPEC.md

------------------------------------------------------------------------

## Current Status

-   Full ingest complete (\~52M rings)
-   MOI₀ scoring complete
-   DB vacuumed and indexed
-   Integrity verified
-   Cohort calibration pending (Galactic Sigma Norm phase)

------------------------------------------------------------------------

## Next Phase

-   Compute Galactic Sigma Norm for surface density
-   Analyze MOI distribution tails
-   Empirically select θ_Icy and θ_Met
-   Implement cohort builder
-   Add SysComp Galactic Sigma display logic
