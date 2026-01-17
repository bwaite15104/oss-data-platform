-- Initialize PostgreSQL database for OSS Data Platform

-- Note: Metabase will create its own database (metabase) when it starts
-- The postgres user has CREATEDB permissions, so Metabase can create it automatically

-- Create schemas
CREATE SCHEMA IF NOT EXISTS public;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS nba;

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Tables will be created by dlt pipelines and other ingestion tools

