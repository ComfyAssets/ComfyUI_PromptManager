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