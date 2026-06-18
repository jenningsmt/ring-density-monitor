CREATE TABLE IF NOT EXISTS expedition_routes (
    route_id TEXT PRIMARY KEY,
    score_version TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    algo_version TEXT NOT NULL,
    moi_metric TEXT NOT NULL,
    created_at TEXT NOT NULL,
    anchor_mode TEXT NOT NULL,
    anchor_x REAL,
    anchor_y REAL,
    anchor_z REAL,
    anchor_ring_id TEXT,
    waypoint_count INTEGER NOT NULL,
    total_distance_ly REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS expedition_waypoints (
    route_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ring_id TEXT NOT NULL,
    system_name TEXT,
    body_name TEXT,
    ring_name TEXT,
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL NOT NULL,
    step_distance_ly REAL NOT NULL,
    cumulative_distance_ly REAL NOT NULL,
    PRIMARY KEY(route_id, seq),
    UNIQUE(route_id, ring_id)
);

CREATE TABLE IF NOT EXISTS metprox_members (
    route_id TEXT NOT NULL,
    ring_id TEXT NOT NULL,
    system_name TEXT,
    body_name TEXT,
    ring_name TEXT,
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL NOT NULL,
    moi_metric REAL NOT NULL,
    distance_to_route_ly REAL NOT NULL,
    source_waypoint_seq INTEGER NOT NULL,
    PRIMARY KEY(route_id, ring_id)
);
