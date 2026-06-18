CREATE TABLE IF NOT EXISTS edmining_best_matches (
    source_url TEXT PRIMARY KEY,
    system_name TEXT,
    planets TEXT,
    status TEXT,
    candidate_count INTEGER NOT NULL,
    best_ring_id TEXT,
    best_ring_type TEXT,
    best_moi_final REAL,
    best_percentile REAL,
    best_in_icycore INTEGER NOT NULL,
    top_candidates TEXT,
    computed_at TEXT NOT NULL,
    score_version TEXT NOT NULL
);
