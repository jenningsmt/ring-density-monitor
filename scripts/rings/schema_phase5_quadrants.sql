CREATE TABLE IF NOT EXISTS icy_quadrants (
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

CREATE TABLE IF NOT EXISTS quadrant_summaries (
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
