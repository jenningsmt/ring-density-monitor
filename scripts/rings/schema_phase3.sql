CREATE TABLE IF NOT EXISTS global_norms (
    score_version TEXT NOT NULL,
    ring_type TEXT NOT NULL,
    metric TEXT NOT NULL,
    n INTEGER NOT NULL,
    mean REAL NOT NULL,
    stddev REAL NOT NULL,
    median REAL NULL,
    mad REAL NULL,
    p95 REAL NULL,
    p99 REAL NULL,
    p99_5 REAL NULL,
    p99_9 REAL NULL,
    min_value REAL NULL,
    max_value REAL NULL,
    computed_at TEXT NOT NULL,
    algo_version TEXT NOT NULL,
    notes TEXT NULL,
    PRIMARY KEY(score_version, ring_type, metric)
);

CREATE TABLE IF NOT EXISTS cohort_cutoffs (
    score_version TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    ring_type TEXT NOT NULL,
    target_n INTEGER NOT NULL,
    theta_value REAL NOT NULL,
    theta_ring_id TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    algo_version TEXT NOT NULL,
    notes TEXT NULL,
    PRIMARY KEY(score_version, cohort_name, ring_type)
);

CREATE TABLE IF NOT EXISTS cohort_members (
    score_version TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    ring_id TEXT NOT NULL,
    rank_in_cohort INTEGER NOT NULL,
    moi0 REAL NOT NULL,
    PRIMARY KEY(score_version, cohort_name, ring_id)
);
