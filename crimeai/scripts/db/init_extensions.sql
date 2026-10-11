-- Run on first PostgreSQL start to enable all required extensions
-- pgvector image includes vector extension already
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Verify
SELECT extname, extversion FROM pg_extension ORDER BY extname;
