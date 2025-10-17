# Archived Migrations

These migration files were consolidated into `000_consolidated_schema.sql` on 2025-10-16.

They are kept here for historical reference but are no longer used by the migration system.

## Original Migrations

- 001_add_thumbnail_support.sql
- 002_add_collections_table.sql
- 003_optimize_stats_indexes.sql
- 004_create_stats_snapshot.sql
- 005_enable_wal_mode.sql
- 006_add_word_cloud_cache.sql
- 007_comprehensive_stats_storage.sql
- 008_add_app_settings.sql
- 009_add_hero_stats.sql
- 010_add_thumbnail_columns_if_missing.sql
- 011_add_updated_at_column.sql
- 012_fix_stats_triggers.sql

## Why Consolidated?

All migrations have been applied to production databases. For fresh installations,
running a single consolidated migration is faster and simpler than running 12 individual
migrations sequentially.

Existing installations already have these migrations marked as applied in the
migration_history table.
