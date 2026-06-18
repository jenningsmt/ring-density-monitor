CREATE TABLE IF NOT EXISTS subregion_staging_clusters (
    run_id TEXT NOT NULL,
    score_version TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    subregion TEXT NOT NULL,
    k INTEGER NOT NULL,
    cluster_index INTEGER NOT NULL,
    cluster_id TEXT PRIMARY KEY NOT NULL,
    medoid_ring_id TEXT NOT NULL,
    size_n INTEGER NOT NULL,
    radius_max_ly REAL NOT NULL,
    radius_p90_ly REAL NOT NULL,
    centroid_x REAL NOT NULL,
    centroid_y REAL NOT NULL,
    centroid_z REAL NOT NULL,
    cost_sum REAL NOT NULL,
    created_utc TEXT NOT NULL,
    UNIQUE(run_id, cluster_index)
);

CREATE TABLE IF NOT EXISTS subregion_staging_members (
    run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    score_version TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    subregion TEXT NOT NULL,
    ring_id TEXT NOT NULL,
    medoid_ring_id TEXT NOT NULL,
    dist_to_medoid_ly REAL NOT NULL,
    dist2_to_medoid REAL NOT NULL,
    assign_rank INTEGER NOT NULL,
    PRIMARY KEY(run_id, ring_id),
    FOREIGN KEY(cluster_id) REFERENCES subregion_staging_clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_subregion_staging_members_cluster_id
ON subregion_staging_members(cluster_id);

CREATE INDEX IF NOT EXISTS idx_staging_clusters_run_clusterindex
ON subregion_staging_clusters(run_id, cluster_index);

CREATE INDEX IF NOT EXISTS idx_staging_clusters_run_medoid
ON subregion_staging_clusters(run_id, medoid_ring_id);

CREATE INDEX IF NOT EXISTS idx_staging_clusters_scope
ON subregion_staging_clusters(score_version, cohort_name, subregion, k);

CREATE INDEX IF NOT EXISTS idx_staging_members_scope
ON subregion_staging_members(score_version, cohort_name, subregion);

CREATE INDEX IF NOT EXISTS idx_staging_members_run_cluster
ON subregion_staging_members(run_id, cluster_id);

CREATE TABLE IF NOT EXISTS subregion_staging_runs (
    run_id TEXT PRIMARY KEY NOT NULL,
    score_version TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    subregion TEXT NOT NULL,
    mode TEXT NOT NULL,
    k_final INTEGER NOT NULL,
    policy_json TEXT NOT NULL,
    created_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_staging_runs_scope
ON subregion_staging_runs(score_version, cohort_name, subregion);
