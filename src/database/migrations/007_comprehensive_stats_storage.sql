-- Migration 007: Comprehensive Stats Storage System
-- Store all calculated statistics for instant retrieval

-- Drop existing tables if they exist for clean migration
DROP TABLE IF EXISTS stats_hourly_activity;
DROP TABLE IF EXISTS stats_resolution_distribution;
DROP TABLE IF EXISTS stats_aspect_ratios;
DROP TABLE IF EXISTS stats_model_usage;
DROP TABLE IF EXISTS stats_rating_trends;
DROP TABLE IF EXISTS stats_workflow_complexity;
DROP TABLE IF EXISTS stats_time_patterns;
DROP TABLE IF EXISTS stats_prompt_patterns;
DROP TABLE IF EXISTS stats_generation_metrics;
DROP TABLE IF EXISTS stats_metadata;

-- Main stats metadata table
CREATE TABLE stats_metadata (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    last_full_calculation TIMESTAMP,
    last_incremental_update TIMESTAMP,
    total_prompts_analyzed INTEGER DEFAULT 0,
    total_images_analyzed INTEGER DEFAULT 0,
    total_workflows_analyzed INTEGER DEFAULT 0,
    calculation_time_ms INTEGER DEFAULT 0,
    stats_version INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Hourly activity patterns
CREATE TABLE stats_hourly_activity (
    hour INTEGER PRIMARY KEY CHECK(hour >= 0 AND hour <= 23),
    prompt_count INTEGER DEFAULT 0,
    image_count INTEGER DEFAULT 0,
    avg_generation_time REAL DEFAULT 0,
    peak_activity_day TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Resolution distribution
CREATE TABLE stats_resolution_distribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    count INTEGER DEFAULT 0,
    percentage REAL DEFAULT 0,
    category TEXT, -- 'portrait', 'landscape', 'square', 'ultrawide'
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(width, height)
);

-- Aspect ratio analysis
CREATE TABLE stats_aspect_ratios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ratio TEXT NOT NULL UNIQUE, -- '16:9', '4:3', '1:1', etc
    display_name TEXT,
    count INTEGER DEFAULT 0,
    percentage REAL DEFAULT 0,
    common_resolutions TEXT, -- JSON array of common resolutions
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Model usage statistics
CREATE TABLE stats_model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL UNIQUE,
    model_hash TEXT,
    usage_count INTEGER DEFAULT 0,
    avg_rating REAL DEFAULT 0,
    total_generation_time REAL DEFAULT 0,
    avg_generation_time REAL DEFAULT 0,
    first_used TIMESTAMP,
    last_used TIMESTAMP,
    peak_usage_date DATE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Rating trends over time
CREATE TABLE stats_rating_trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_type TEXT CHECK(period_type IN ('daily', 'weekly', 'monthly')),
    period_date DATE NOT NULL,
    avg_rating REAL DEFAULT 0,
    total_ratings INTEGER DEFAULT 0,
    five_star_count INTEGER DEFAULT 0,
    four_star_count INTEGER DEFAULT 0,
    three_star_count INTEGER DEFAULT 0,
    two_star_count INTEGER DEFAULT 0,
    one_star_count INTEGER DEFAULT 0,
    unrated_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(period_type, period_date)
);

-- Workflow complexity analysis
CREATE TABLE stats_workflow_complexity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    complexity_level TEXT CHECK(complexity_level IN ('simple', 'moderate', 'complex', 'advanced')),
    node_count_min INTEGER,
    node_count_max INTEGER,
    workflow_count INTEGER DEFAULT 0,
    avg_generation_time REAL DEFAULT 0,
    avg_rating REAL DEFAULT 0,
    common_nodes TEXT, -- JSON array of most common nodes
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(complexity_level)
);

-- Time-based patterns
CREATE TABLE stats_time_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL, -- 'daily_peak', 'weekly_peak', 'monthly_trend'
    pattern_value TEXT NOT NULL, -- JSON data
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pattern_type)
);

-- Prompt pattern analysis
CREATE TABLE stats_prompt_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL, -- 'avg_length', 'complexity_dist', 'vocabulary_size'
    pattern_value TEXT NOT NULL, -- JSON or simple value
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pattern_type)
);

-- Generation metrics summary
CREATE TABLE stats_generation_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_type TEXT NOT NULL, -- 'total_time', 'avg_time', 'success_rate', etc
    metric_value REAL DEFAULT 0,
    metric_unit TEXT, -- 'seconds', 'percentage', 'count'
    period TEXT, -- 'all_time', 'last_30_days', 'last_7_days'
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(metric_type, period)
);

-- Sampler usage statistics
CREATE TABLE stats_sampler_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sampler_name TEXT NOT NULL UNIQUE,
    usage_count INTEGER DEFAULT 0,
    avg_steps REAL DEFAULT 0,
    avg_cfg REAL DEFAULT 0,
    avg_generation_time REAL DEFAULT 0,
    most_common_model TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX idx_resolution_count ON stats_resolution_distribution(count DESC);
CREATE INDEX idx_aspect_ratio_count ON stats_aspect_ratios(count DESC);
CREATE INDEX idx_model_usage_count ON stats_model_usage(usage_count DESC);
CREATE INDEX idx_rating_trends_date ON stats_rating_trends(period_date DESC);
CREATE INDEX idx_workflow_complexity ON stats_workflow_complexity(workflow_count DESC);

-- Initialize metadata
INSERT OR IGNORE INTO stats_metadata (id) VALUES (1);

-- Initialize hourly activity (24 hours)
INSERT OR IGNORE INTO stats_hourly_activity (hour) VALUES
(0), (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11),
(12), (13), (14), (15), (16), (17), (18), (19), (20), (21), (22), (23);

-- Initialize workflow complexity levels
INSERT OR IGNORE INTO stats_workflow_complexity (complexity_level, node_count_min, node_count_max)
VALUES
    ('simple', 1, 10),
    ('moderate', 11, 25),
    ('complex', 26, 50),
    ('advanced', 51, 999);