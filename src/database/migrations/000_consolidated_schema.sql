-- Consolidated Database Schema
-- Generated: 2025-10-16T17:01:01.917021
-- Combines migrations: 001-012
--
-- This migration represents the complete schema state after applying
-- all individual migrations. For fresh installations only.
--
-- If you have an existing database, these migrations have already been applied.

-- ============================================================================
-- CONSOLIDATED SCHEMA - DO NOT EDIT MANUALLY
-- ============================================================================


-- ============================================================================
-- 001_add_thumbnail_support.sql
-- ============================================================================

-- Migration: Add thumbnail tracking support
-- Version: 001
-- Description: Adds thumbnail tracking columns, cache table, and configuration settings

-- Add thumbnail columns to generated_images table if it exists
-- Otherwise create it with thumbnail support
CREATE TABLE IF NOT EXISTS generated_images (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    prompt_id INTEGER,
    model TEXT,
    sampler TEXT,
    steps INTEGER,
    cfg_scale REAL,
    seed INTEGER,
    width INTEGER,
    height INTEGER,
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- New thumbnail columns
    thumbnail_small_path TEXT,
    thumbnail_medium_path TEXT,
    thumbnail_large_path TEXT,
    thumbnail_xlarge_path TEXT,
    thumbnails_generated_at TIMESTAMP,
    thumbnail_generation_error TEXT,
    FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- Add thumbnail columns if table already exists (safe to run multiple times)
-- SQLite doesn't support IF NOT EXISTS for ALTER TABLE, so we handle this in the migration system

-- Create thumbnail cache table for performance
CREATE TABLE IF NOT EXISTS thumbnail_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id TEXT NOT NULL,
    size VARCHAR(20) NOT NULL,
    path TEXT NOT NULL,
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    format VARCHAR(10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 1,
    UNIQUE(image_id, size),
    FOREIGN KEY (image_id) REFERENCES generated_images(id) ON DELETE CASCADE
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_thumbnail_cache_image_id ON thumbnail_cache(image_id);
CREATE INDEX IF NOT EXISTS idx_thumbnail_cache_size ON thumbnail_cache(size);
CREATE INDEX IF NOT EXISTS idx_thumbnail_cache_accessed ON thumbnail_cache(accessed_at);

-- Create thumbnail generation queue table
CREATE TABLE IF NOT EXISTS thumbnail_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    priority INTEGER DEFAULT 5,
    sizes TEXT NOT NULL DEFAULT '["small","medium","large"]',
    status VARCHAR(20) DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (image_id) REFERENCES generated_images(id) ON DELETE CASCADE
);

-- Create index for queue processing
CREATE INDEX IF NOT EXISTS idx_thumbnail_queue_status ON thumbnail_queue(status, priority);
CREATE INDEX IF NOT EXISTS idx_thumbnail_queue_created ON thumbnail_queue(created_at);

-- Create system_settings table if not exists
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Add thumbnail settings to system_settings table
INSERT OR IGNORE INTO system_settings (key, value, description, category)
VALUES
    ('thumbnail.cache_dir', 'comfyui/user/default/PromptManager/thumbnails', 'Thumbnail cache directory', 'storage'),
    ('thumbnail.auto_generate', 'true', 'Automatically generate thumbnails for new images', 'performance'),
    ('thumbnail.max_parallel', '4', 'Maximum parallel thumbnail generation tasks', 'performance'),
    ('thumbnail.ffmpeg_path', '', 'Custom path to ffmpeg executable', 'external'),
    ('thumbnail.video_enabled', 'true', 'Enable video thumbnail generation', 'features'),
    ('thumbnail.video_timestamp', '1.0', 'Default timestamp (seconds) for video thumbnails', 'features'),
    ('thumbnail.jpeg_quality', '85', 'JPEG thumbnail quality (1-100)', 'quality'),
    ('thumbnail.webp_quality', '85', 'WebP thumbnail quality (1-100)', 'quality'),
    ('thumbnail.cache_ttl', '604800', 'Thumbnail cache TTL in seconds (7 days)', 'performance'),
    ('thumbnail.max_cache_size_gb', '10', 'Maximum thumbnail cache size in GB', 'storage');

-- DOWN
-- Rollback migration by dropping thumbnail-specific tables and columns
-- Note: This is destructive and should be used carefully

DROP TABLE IF EXISTS thumbnail_queue;
DROP TABLE IF EXISTS thumbnail_cache;

-- Remove thumbnail settings
DELETE FROM system_settings WHERE key LIKE 'thumbnail.%';

-- Note: We cannot easily remove columns from generated_images in SQLite
-- Would need to recreate the table without those columns


-- ============================================================================
-- 002_add_collections_table.sql
-- ============================================================================

-- Migration: Add Collections Table
-- Version: 002
-- Date: 2024-09-24
-- Description: Create collections table for organizing prompts

-- Create collections table
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT  -- JSON field for additional data
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_collections_name ON collections(name);
CREATE INDEX IF NOT EXISTS idx_collections_created_at ON collections(created_at);

-- Create collection_prompts junction table for many-to-many relationship
CREATE TABLE IF NOT EXISTS collection_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    prompt_id INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    position INTEGER DEFAULT 0,  -- For ordering prompts within collection
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
    FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
    UNIQUE(collection_id, prompt_id)
);

-- Create indexes for the junction table
CREATE INDEX IF NOT EXISTS idx_collection_prompts_collection_id ON collection_prompts(collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_prompts_prompt_id ON collection_prompts(prompt_id);

-- Add trigger to update the updated_at timestamp
CREATE TRIGGER IF NOT EXISTS update_collections_timestamp
AFTER UPDATE ON collections
BEGIN
    UPDATE collections SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;


-- ============================================================================
-- 003_optimize_stats_indexes.sql
-- ============================================================================

-- Migration 003: Optimize database indexes for statistics performance
-- This migration adds indexes specifically designed to speed up stats queries
-- Expected performance improvement: 10-30 seconds → <1 second for stats loading

-- Indexes for prompts table (if not already exist)
CREATE INDEX IF NOT EXISTS idx_prompts_created_at ON prompts(created_at);
CREATE INDEX IF NOT EXISTS idx_prompts_category ON prompts(category);
CREATE INDEX IF NOT EXISTS idx_prompts_rating ON prompts(rating);
CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash);

-- Composite indexes for common stats queries
CREATE INDEX IF NOT EXISTS idx_prompts_category_created
    ON prompts(category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prompts_rating_created
    ON prompts(rating, created_at DESC);

-- Indexes for generated_images table
CREATE INDEX IF NOT EXISTS idx_images_prompt_id
    ON generated_images(prompt_id);
CREATE INDEX IF NOT EXISTS idx_images_generation_time
    ON generated_images(generation_time);
CREATE INDEX IF NOT EXISTS idx_images_created_at
    ON generated_images(created_at);
CREATE INDEX IF NOT EXISTS idx_images_file_size
    ON generated_images(file_size);

-- Composite index for image stats
CREATE INDEX IF NOT EXISTS idx_images_prompt_generation
    ON generated_images(prompt_id, generation_time DESC);

-- Indexes for prompt_tracking table
CREATE INDEX IF NOT EXISTS idx_tracking_session_id
    ON prompt_tracking(session_id);
CREATE INDEX IF NOT EXISTS idx_tracking_created_at
    ON prompt_tracking(created_at);
CREATE INDEX IF NOT EXISTS idx_tracking_prompt_text
    ON prompt_tracking(prompt_text);

-- Composite index for session analysis
CREATE INDEX IF NOT EXISTS idx_tracking_session_created
    ON prompt_tracking(session_id, created_at DESC);

-- Create materialized view for category stats (if supported)
-- This view pre-calculates category statistics
CREATE VIEW IF NOT EXISTS category_stats AS
SELECT
    category,
    COUNT(*) as prompt_count,
    AVG(rating) as avg_rating,
    MAX(created_at) as last_created,
    COUNT(DISTINCT DATE(created_at)) as active_days
FROM prompts
WHERE category IS NOT NULL
GROUP BY category;

-- Create materialized view for daily stats
CREATE VIEW IF NOT EXISTS daily_stats AS
SELECT
    DATE(created_at) as date,
    COUNT(*) as prompts_created,
    COUNT(DISTINCT category) as categories_used,
    AVG(rating) as avg_rating
FROM prompts
GROUP BY DATE(created_at);

-- Create materialized view for tag stats
-- Note: This assumes tags are stored as JSON array
CREATE VIEW IF NOT EXISTS tag_stats AS
SELECT
    json_each.value as tag,
    COUNT(*) as usage_count
FROM prompts, json_each(prompts.tags)
WHERE prompts.tags IS NOT NULL
GROUP BY json_each.value;

-- Analyze tables to update query planner statistics
ANALYZE prompts;
ANALYZE generated_images;
ANALYZE prompt_tracking;

-- Add migration record
INSERT INTO migrations (version, applied_at, description)
VALUES (
    '003',
    datetime('now'),
    'Added optimized indexes for statistics queries'
);


-- ============================================================================
-- 004_create_stats_snapshot.sql
-- ============================================================================

-- Migration 004: Create persistent stats snapshot table
-- This eliminates the 2-minute startup delay by maintaining pre-calculated stats
-- Stats are updated incrementally via triggers, never recalculated from scratch

-- Drop existing stats snapshot table if it exists
DROP TABLE IF EXISTS stats_snapshot;

-- Create the stats snapshot table (single row design)
CREATE TABLE stats_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Ensures only one row
    -- Basic counts (instant access)
    total_prompts INTEGER DEFAULT 0,
    total_images INTEGER DEFAULT 0,
    total_sessions INTEGER DEFAULT 0,
    total_collections INTEGER DEFAULT 0,

    -- Category breakdown (JSON for flexibility)
    category_counts TEXT DEFAULT '{}',  -- {"category_name": count, ...}

    -- Tag frequency (top 100 tags)
    tag_counts TEXT DEFAULT '{}',  -- {"tag_name": count, ...}

    -- Recent activity
    prompts_last_24h INTEGER DEFAULT 0,
    prompts_last_7d INTEGER DEFAULT 0,
    prompts_last_30d INTEGER DEFAULT 0,
    images_last_24h INTEGER DEFAULT 0,
    images_last_7d INTEGER DEFAULT 0,
    images_last_30d INTEGER DEFAULT 0,

    -- Popular items
    most_used_prompts TEXT DEFAULT '[]',  -- JSON array of top 10
    most_recent_prompts TEXT DEFAULT '[]',  -- JSON array of last 10

    -- Performance metrics
    avg_generation_time REAL DEFAULT 0.0,
    total_generation_time REAL DEFAULT 0.0,

    -- Metadata
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_full_rebuild TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    version INTEGER DEFAULT 1
);

-- Insert the single stats row
INSERT INTO stats_snapshot (id) VALUES (1);

-- ============================================
-- TRIGGERS FOR AUTOMATIC INCREMENTAL UPDATES
-- ============================================

-- Trigger: Increment prompt count on INSERT
CREATE TRIGGER stats_increment_prompt_insert
AFTER INSERT ON prompts
BEGIN
    UPDATE stats_snapshot
    SET
        total_prompts = total_prompts + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Decrement prompt count on DELETE
CREATE TRIGGER stats_decrement_prompt_delete
AFTER DELETE ON prompts
BEGIN
    UPDATE stats_snapshot
    SET
        total_prompts = MAX(0, total_prompts - 1),
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Increment image count on INSERT
CREATE TRIGGER stats_increment_image_insert
AFTER INSERT ON generated_images
BEGIN
    UPDATE stats_snapshot
    SET
        total_images = total_images + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Decrement image count on DELETE
CREATE TRIGGER stats_decrement_image_delete
AFTER DELETE ON generated_images
BEGIN
    UPDATE stats_snapshot
    SET
        total_images = MAX(0, total_images - 1),
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Update session count on tracking INSERT
CREATE TRIGGER stats_update_session_insert
AFTER INSERT ON prompt_tracking
BEGIN
    UPDATE stats_snapshot
    SET
        total_sessions = (
            SELECT COUNT(DISTINCT session_id)
            FROM prompt_tracking
        ),
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Update collection count
CREATE TRIGGER IF NOT EXISTS stats_increment_collection_insert
AFTER INSERT ON collections
BEGIN
    UPDATE stats_snapshot
    SET
        total_collections = total_collections + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

CREATE TRIGGER IF NOT EXISTS stats_decrement_collection_delete
AFTER DELETE ON collections
BEGIN
    UPDATE stats_snapshot
    SET
        total_collections = MAX(0, total_collections - 1),
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- ============================================
-- HELPER FUNCTIONS (Stored Procedures)
-- ============================================

-- Create a view for quick category counts
CREATE VIEW IF NOT EXISTS category_counts_view AS
SELECT
    category,
    COUNT(*) as count
FROM prompts
WHERE category IS NOT NULL
GROUP BY category
ORDER BY count DESC;

-- Create a view for quick tag counts
CREATE VIEW IF NOT EXISTS tag_counts_view AS
SELECT
    json_each.value as tag,
    COUNT(*) as count
FROM prompts, json_each(prompts.tags)
WHERE prompts.tags IS NOT NULL
GROUP BY json_each.value
ORDER BY count DESC
LIMIT 100;

-- ============================================
-- ONE-TIME POPULATION FROM EXISTING DATA
-- ============================================

-- Populate initial stats from existing data
-- This runs ONCE to populate the stats table
UPDATE stats_snapshot
SET
    total_prompts = (SELECT COUNT(*) FROM prompts),
    total_images = (SELECT COUNT(*) FROM generated_images),
    total_sessions = (SELECT COUNT(DISTINCT session_id) FROM prompt_tracking),
    total_collections = (SELECT COUNT(*) FROM collections),
    prompts_last_24h = (
        SELECT COUNT(*) FROM prompts
        WHERE datetime(created_at) > datetime('now', '-1 day')
    ),
    prompts_last_7d = (
        SELECT COUNT(*) FROM prompts
        WHERE datetime(created_at) > datetime('now', '-7 days')
    ),
    prompts_last_30d = (
        SELECT COUNT(*) FROM prompts
        WHERE datetime(created_at) > datetime('now', '-30 days')
    ),
    images_last_24h = (
        SELECT COUNT(*) FROM generated_images
        WHERE datetime(created_at) > datetime('now', '-1 day')
    ),
    images_last_7d = (
        SELECT COUNT(*) FROM generated_images
        WHERE datetime(created_at) > datetime('now', '-7 days')
    ),
    images_last_30d = (
        SELECT COUNT(*) FROM generated_images
        WHERE datetime(created_at) > datetime('now', '-30 days')
    ),
    category_counts = (
        SELECT json_group_object(category, cnt)
        FROM (
            SELECT category, COUNT(*) as cnt
            FROM prompts
            WHERE category IS NOT NULL
            GROUP BY category
        )
    ),
    tag_counts = (
        SELECT json_group_object(tag, cnt)
        FROM (
            SELECT json_each.value as tag, COUNT(*) as cnt
            FROM prompts, json_each(prompts.tags)
            WHERE prompts.tags IS NOT NULL
            GROUP BY json_each.value
            ORDER BY cnt DESC
            LIMIT 100
        )
    ),
    last_updated = CURRENT_TIMESTAMP,
    last_full_rebuild = CURRENT_TIMESTAMP
WHERE id = 1;

-- Create index for faster stats queries
CREATE INDEX IF NOT EXISTS idx_prompts_created_at_stats ON prompts(created_at);
CREATE INDEX IF NOT EXISTS idx_images_created_at_stats ON generated_images(created_at);

-- Add migration record
INSERT INTO migrations (version, applied_at, description)
VALUES (
    '004',
    datetime('now'),
    'Created persistent stats snapshot table with triggers for instant access'
);


-- ============================================================================
-- 005_enable_wal_mode.sql
-- ============================================================================

-- Migration: Enable WAL mode for better concurrency
-- WAL (Write-Ahead Logging) allows concurrent reads and writes
-- This solves "database is locked" errors when multiple processes access the database

-- Enable WAL mode
PRAGMA journal_mode=WAL;

-- Set WAL auto-checkpoint (default is 1000 pages)
PRAGMA wal_autocheckpoint=1000;

-- Optimize for concurrent access
PRAGMA synchronous=NORMAL;

-- Enable memory-mapped I/O for better performance
PRAGMA mmap_size=268435456;  -- 256MB

-- Set busy timeout to 5 seconds (wait before returning "database is locked")
PRAGMA busy_timeout=5000;

-- Note: WAL mode persists across connections once set
-- The database will remain in WAL mode until explicitly changed back


-- ============================================================================
-- 006_add_word_cloud_cache.sql
-- ============================================================================

-- Migration 006: Add word cloud cache table for pre-calculated data
-- This stores pre-calculated word frequency data for instant retrieval

-- Drop table if it exists (for clean migration)
DROP TABLE IF EXISTS word_cloud_cache;

-- Create word cloud cache table
CREATE TABLE word_cloud_cache (
    id INTEGER PRIMARY KEY,
    word TEXT NOT NULL UNIQUE,
    frequency INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast word lookups
CREATE INDEX idx_word_cloud_frequency ON word_cloud_cache(frequency DESC);

-- Metadata table for tracking when word cloud was last calculated
CREATE TABLE IF NOT EXISTS word_cloud_metadata (
    id INTEGER PRIMARY KEY CHECK(id = 1),  -- Only one row allowed
    last_calculated TIMESTAMP,
    total_prompts_analyzed INTEGER DEFAULT 0,
    total_words_processed INTEGER DEFAULT 0,
    calculation_time_ms INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert initial metadata row
INSERT OR IGNORE INTO word_cloud_metadata (id) VALUES (1);


-- ============================================================================
-- 007_comprehensive_stats_storage.sql
-- ============================================================================

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


-- ============================================================================
-- 008_add_app_settings.sql
-- ============================================================================

-- Migration 008: Add application settings table for key-value configuration storage
-- This table stores app-wide settings like API keys, UUIDs, and other configuration

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    category TEXT DEFAULT 'general',
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index for category lookups
CREATE INDEX IF NOT EXISTS idx_app_settings_category ON app_settings(category);

-- Insert default settings
INSERT OR IGNORE INTO app_settings (key, value, category, description) VALUES
    ('civitai_api_key', '', 'community', 'CivitAI API key for model downloads and metadata'),
    ('promptmanager_uuid', '', 'community', 'Unique identifier for PromptManager instance'),
    ('maintenance_auto_backup', 'false', 'system', 'Automatically backup database before maintenance'),
    ('maintenance_last_run', '', 'system', 'Timestamp of last maintenance operation'),
    ('ui_theme', 'dark', 'appearance', 'UI theme preference'),
    ('stats_cache_duration', '3600', 'performance', 'Stats cache duration in seconds');

-- Add trigger to update the updated_at timestamp
CREATE TRIGGER IF NOT EXISTS update_app_settings_timestamp
AFTER UPDATE ON app_settings
BEGIN
    UPDATE app_settings
    SET updated_at = CURRENT_TIMESTAMP
    WHERE key = NEW.key;
END;


-- ============================================================================
-- 009_add_hero_stats.sql
-- ============================================================================

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


-- ============================================================================
-- 010_add_thumbnail_columns_if_missing.sql
-- ============================================================================

-- Migration: Add thumbnail columns if missing
-- Version: 010
-- Description: Adds thumbnail tracking columns to existing generated_images table
-- This handles cases where the table exists but doesn't have thumbnail columns

-- This migration uses a workaround for SQLite's lack of "ADD COLUMN IF NOT EXISTS"
-- We check column existence first before attempting to add

-- Get current columns (this is just for documentation, actual check happens in Python migration runner)

-- Add thumbnail columns one by one
-- The migration runner should check PRAGMA table_info(generated_images) first
-- and only add columns that don't exist

ALTER TABLE generated_images ADD COLUMN thumbnail_small_path TEXT;
ALTER TABLE generated_images ADD COLUMN thumbnail_medium_path TEXT;
ALTER TABLE generated_images ADD COLUMN thumbnail_large_path TEXT;
ALTER TABLE generated_images ADD COLUMN thumbnail_xlarge_path TEXT;
ALTER TABLE generated_images ADD COLUMN thumbnails_generated_at TIMESTAMP;
ALTER TABLE generated_images ADD COLUMN thumbnail_generation_error TEXT;


-- ============================================================================
-- 011_add_updated_at_column.sql
-- ============================================================================

-- Migration: Add updated_at column to prompts table
-- This migration adds the updated_at timestamp column if it doesn't exist

-- Check if the column exists and add it if missing
-- SQLite doesn't have ALTER TABLE IF COLUMN NOT EXISTS, so we use a workaround

-- Create a new table with the correct schema
CREATE TABLE IF NOT EXISTS prompts_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    positive_prompt TEXT NOT NULL,
    negative_prompt TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    category TEXT,
    tags TEXT,
    rating INTEGER,
    notes TEXT,
    hash TEXT,
    model_hash TEXT,
    sampler_settings TEXT,
    generation_params TEXT
);

-- Copy data from old table if it exists
INSERT INTO prompts_new (
    id, positive_prompt, negative_prompt, created_at, category, tags,
    rating, notes, hash, model_hash, sampler_settings, generation_params, updated_at
)
SELECT
    id, positive_prompt, negative_prompt, created_at, category, tags,
    rating, notes, hash, model_hash, sampler_settings, generation_params,
    COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) as updated_at
FROM prompts
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompts');

-- Drop old table
DROP TABLE IF EXISTS prompts;

-- Rename new table
ALTER TABLE prompts_new RENAME TO prompts;

-- Recreate indexes
CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash);
CREATE INDEX IF NOT EXISTS idx_prompts_created ON prompts(created_at);
CREATE INDEX IF NOT EXISTS idx_prompts_updated ON prompts(updated_at);
CREATE INDEX IF NOT EXISTS idx_prompts_category ON prompts(category);
CREATE INDEX IF NOT EXISTS idx_prompts_rating ON prompts(rating);


-- ============================================================================
-- 012_fix_stats_triggers.sql
-- ============================================================================

-- Migration 012: Fix stats triggers that reference wrong column name
-- Some databases have triggers that reference 'updated_at' instead of 'last_updated'
-- This migration fixes those triggers

-- Drop and recreate all stats triggers with correct column names

-- Drop old triggers if they exist
DROP TRIGGER IF EXISTS stats_increment_prompt_insert;
DROP TRIGGER IF EXISTS stats_decrement_prompt_delete;
DROP TRIGGER IF EXISTS stats_increment_image_insert;
DROP TRIGGER IF EXISTS stats_decrement_image_delete;
DROP TRIGGER IF EXISTS stats_update_session_insert;
DROP TRIGGER IF EXISTS stats_increment_collection_insert;
DROP TRIGGER IF EXISTS stats_decrement_collection_delete;

-- Recreate triggers with correct column name (last_updated, not updated_at)

-- Trigger: Increment prompt count on INSERT
CREATE TRIGGER stats_increment_prompt_insert
AFTER INSERT ON prompts
BEGIN
    UPDATE stats_snapshot
    SET
        total_prompts = total_prompts + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Decrement prompt count on DELETE
CREATE TRIGGER stats_decrement_prompt_delete
AFTER DELETE ON prompts
BEGIN
    UPDATE stats_snapshot
    SET
        total_prompts = MAX(0, total_prompts - 1),
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Increment image count on INSERT
CREATE TRIGGER stats_increment_image_insert
AFTER INSERT ON generated_images
BEGIN
    UPDATE stats_snapshot
    SET
        total_images = total_images + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Decrement image count on DELETE
CREATE TRIGGER stats_decrement_image_delete
AFTER DELETE ON generated_images
BEGIN
    UPDATE stats_snapshot
    SET
        total_images = MAX(0, total_images - 1),
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Update session count on tracking INSERT
CREATE TRIGGER stats_update_session_insert
AFTER INSERT ON prompt_tracking
BEGIN
    UPDATE stats_snapshot
    SET
        total_sessions = (
            SELECT COUNT(DISTINCT session_id)
            FROM prompt_tracking
        ),
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Update collection count on INSERT
CREATE TRIGGER stats_increment_collection_insert
AFTER INSERT ON collections
BEGIN
    UPDATE stats_snapshot
    SET
        total_collections = total_collections + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;

-- Trigger: Update collection count on DELETE
CREATE TRIGGER stats_decrement_collection_delete
AFTER DELETE ON collections
BEGIN
    UPDATE stats_snapshot
    SET
        total_collections = MAX(0, total_collections - 1),
        last_updated = CURRENT_TIMESTAMP
    WHERE id = 1;
END;


-- ============================================================================
-- END OF CONSOLIDATED SCHEMA
-- ============================================================================
