-- ============================================================================
-- Migration: 001_initial_schema.sql
-- Description: Canonical schema for Acdyon Job Ingestion Subsystem
-- Target Database: PostgreSQL 14+ / Supabase
-- ============================================================================

-- 1. Jobs Table
-- Enforces canonical identity uniqueness and idempotent updates.
CREATE TABLE IF NOT EXISTS jobs (
    canonical_id VARCHAR(128) PRIMARY KEY,
    source_name VARCHAR(64) NOT NULL,
    source_id VARCHAR(256),
    source_url VARCHAR(2048) NOT NULL,
    title VARCHAR(512) NOT NULL,
    company VARCHAR(256) NOT NULL,
    location VARCHAR(256) NOT NULL,
    description TEXT NOT NULL,
    employment_type VARCHAR(32) NOT NULL,
    salary JSONB,
    requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
    published_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_jobs_source_name ON jobs (source_name);
CREATE INDEX IF NOT EXISTS idx_jobs_published_at ON jobs (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs (company);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);


-- 2. Ingestion Runs Table
-- Operational execution statistics and audit history for each run.
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    source_name VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    duration_ms INTEGER NOT NULL,
    records_received INTEGER NOT NULL DEFAULT 0,
    records_accepted INTEGER NOT NULL DEFAULT 0,
    records_rejected INTEGER NOT NULL DEFAULT 0,
    duplicates_detected INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    failed_requests INTEGER NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_info JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source_name ON ingestion_runs (source_name);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_created_at ON ingestion_runs (created_at DESC);


-- 3. Source Health Table
-- State tracking for source provider uptime, consecutive failures, and degradation.
CREATE TABLE IF NOT EXISTS source_health (
    source_name VARCHAR(64) PRIMARY KEY,
    health_status VARCHAR(32) NOT NULL,
    endpoint VARCHAR(2048) NOT NULL,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error_details JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- 4. Ingestion Snapshots Table
-- Lightweight references to successful run job IDs for stale fallback serving.
CREATE TABLE IF NOT EXISTS ingestion_snapshots (
    source_name VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    canonical_ids JSONB NOT NULL,
    job_count INTEGER NOT NULL,
    snapshot_timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
