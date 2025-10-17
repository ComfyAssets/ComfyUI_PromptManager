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