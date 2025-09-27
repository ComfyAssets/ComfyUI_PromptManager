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