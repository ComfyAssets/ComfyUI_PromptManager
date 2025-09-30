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