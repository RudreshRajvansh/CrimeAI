-- ==============================================================================
-- CrimeAI – Database Initialisation Script
-- scripts/db/init.sql
--
-- Run once when the database is first created.
-- Docker Compose mounts this as /docker-entrypoint-initdb.d/01_init.sql
-- In Supabase/production: run this manually via the SQL editor.
--
-- Extensions must be created before Alembic migrations run.
-- ==============================================================================

-- Enable PostGIS (spatial data)
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Enable pgvector (embedding/similarity search)
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pg_trgm (fuzzy text search for FIR analysis)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Enable btree_gist (for exclusion constraints on date ranges)
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Enable unaccent (for normalised text search)
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Verify extensions
SELECT
    name,
    default_version,
    installed_version
FROM pg_available_extensions
WHERE name IN ('postgis', 'vector', 'uuid-ossp', 'pg_trgm', 'btree_gist', 'unaccent')
ORDER BY name;
