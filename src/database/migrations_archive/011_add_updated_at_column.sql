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