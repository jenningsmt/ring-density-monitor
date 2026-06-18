CREATE TABLE IF NOT EXISTS icy_subregions (
    score_version TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    ring_id TEXT NOT NULL,
    quadrant TEXT NOT NULL,
    band TEXT NOT NULL,
    subregion TEXT NOT NULL,
    rho_ly REAL NOT NULL,
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

CREATE TABLE IF NOT EXISTS subregion_summaries (
    score_version TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    subregion TEXT NOT NULL,
    quadrant TEXT NOT NULL,
    band TEXT NOT NULL,
    n INTEGER NOT NULL,
    centroid_x REAL NOT NULL,
    centroid_y REAL NOT NULL,
    centroid_z REAL NOT NULL,
    radius_max_ly REAL NOT NULL,
    rho_min REAL NOT NULL,
    rho_median REAL NOT NULL,
    rho_max REAL NOT NULL,
    moi_max REAL,
    moi_median REAL,
    min_ring_id TEXT NOT NULL,
    PRIMARY KEY(score_version, cohort_name, subregion)
);
