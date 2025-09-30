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