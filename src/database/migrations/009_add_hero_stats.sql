-- Migration 009: Add Hero Stats and Generation Analytics Tables
-- These tables store aggregated stats for the dashboard

-- Hero stats for dashboard display
CREATE TABLE IF NOT EXISTS stats_hero_stats (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    total_images INTEGER DEFAULT 0,
    total_prompts INTEGER DEFAULT 0,
    rated_count INTEGER DEFAULT 0,
    avg_rating REAL DEFAULT 0,
    five_star_count INTEGER DEFAULT 0,
    total_collections INTEGER DEFAULT 0,
    images_per_prompt REAL DEFAULT 0,
    generation_streak INTEGER DEFAULT 0,  -- Days in a row with generation
    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Generation analytics summary
CREATE TABLE IF NOT EXISTS stats_generation_analytics (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    total_generations INTEGER DEFAULT 0,
    unique_prompts INTEGER DEFAULT 0,
    avg_per_day REAL DEFAULT 0,
    peak_day_count INTEGER DEFAULT 0,
    peak_day DATE,
    total_generation_time REAL DEFAULT 0,
    avg_generation_time REAL DEFAULT 0,
    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Quality metrics summary
CREATE TABLE IF NOT EXISTS stats_quality_metrics (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    avg_quality_score REAL DEFAULT 0,
    high_quality_count INTEGER DEFAULT 0,  -- 4-5 star ratings
    low_quality_count INTEGER DEFAULT 0,   -- 1-2 star ratings
    quality_trend TEXT DEFAULT 'stable',   -- 'improving', 'declining', 'stable'
    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Add generation_count and percentage columns to model usage if not exists
-- SQLite doesn't have ALTER TABLE ADD COLUMN IF NOT EXISTS, so we need to check first
CREATE TABLE IF NOT EXISTS stats_model_usage_temp AS
SELECT * FROM stats_model_usage;

DROP TABLE IF EXISTS stats_model_usage;

CREATE TABLE stats_model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL UNIQUE,
    model_hash TEXT,
    generation_count INTEGER DEFAULT 0,  -- Added for API compatibility
    percentage REAL DEFAULT 0,           -- Added for API compatibility
    usage_count INTEGER DEFAULT 0,
    avg_rating REAL DEFAULT 0,
    total_generation_time REAL DEFAULT 0,
    avg_generation_time REAL DEFAULT 0,
    first_used TIMESTAMP,
    last_used TIMESTAMP,
    peak_usage_date DATE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Copy data back from temp table
INSERT OR IGNORE INTO stats_model_usage (
    model_name, model_hash, usage_count, generation_count,
    avg_rating, total_generation_time, avg_generation_time,
    first_used, last_used, peak_usage_date, updated_at
)
SELECT
    model_name, model_hash, usage_count, usage_count as generation_count,
    avg_rating, total_generation_time, avg_generation_time,
    first_used, last_used, peak_usage_date, updated_at
FROM stats_model_usage_temp;

DROP TABLE IF EXISTS stats_model_usage_temp;

-- Add time patterns table with the correct structure
CREATE TABLE IF NOT EXISTS stats_time_patterns_detailed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour_of_day INTEGER CHECK(hour_of_day >= 0 AND hour_of_day <= 23),
    total_generations INTEGER DEFAULT 0,
    avg_quality_score REAL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(hour_of_day)
);