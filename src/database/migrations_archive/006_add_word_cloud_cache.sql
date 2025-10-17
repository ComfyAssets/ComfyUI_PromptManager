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