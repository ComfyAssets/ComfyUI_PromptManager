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