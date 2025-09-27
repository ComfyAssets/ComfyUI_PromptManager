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