-- Initialize PostgreSQL database for NBA Analytics Platform
-- Database: nba_analytics

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Create all schemas for each environment (dev, staging, prod)
DO $$
DECLARE
    env TEXT;
    layer TEXT;
BEGIN
    FOREACH env IN ARRAY ARRAY['dev', 'staging', 'prod'] LOOP
        FOREACH layer IN ARRAY ARRAY['raw', 'staging', 'marts', 'features', 'ml'] LOOP
            EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', layer || '_' || env);
        END LOOP;
    END LOOP;
END $$;

-- Legacy schema for backward compatibility
CREATE SCHEMA IF NOT EXISTS nba;

-- Schema comments
COMMENT ON SCHEMA raw_dev IS 'Development: Raw data as ingested (Bronze)';
COMMENT ON SCHEMA staging_dev IS 'Development: Cleaned and validated (Silver)';
COMMENT ON SCHEMA marts_dev IS 'Development: Business aggregations (Gold)';
COMMENT ON SCHEMA features_dev IS 'Development: ML feature store';
COMMENT ON SCHEMA ml_dev IS 'Development: Model training and predictions';

-- Helper function to set search path for environment
CREATE OR REPLACE FUNCTION set_env_search_path(env TEXT DEFAULT 'dev')
RETURNS VOID AS $$
BEGIN
    EXECUTE format(
        'SET search_path TO raw_%s, staging_%s, marts_%s, features_%s, ml_%s, public',
        env, env, env, env, env
    );
END;
$$ LANGUAGE plpgsql;

-- Feature registry table in each environment
DO $$
DECLARE
    env TEXT;
BEGIN
    FOREACH env IN ARRAY ARRAY['dev', 'staging', 'prod'] LOOP
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS features_%s.feature_registry (
                feature_id SERIAL PRIMARY KEY,
                feature_name VARCHAR(255) NOT NULL UNIQUE,
                feature_group VARCHAR(100),
                description TEXT,
                data_type VARCHAR(50),
                source_table VARCHAR(255),
                is_active BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )', env);
    END LOOP;
END $$;

-- ML metadata tables in each environment
DO $$
DECLARE
    env TEXT;
BEGIN
    FOREACH env IN ARRAY ARRAY['dev', 'staging', 'prod'] LOOP
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS ml_%s.model_registry (
                model_id SERIAL PRIMARY KEY,
                model_name VARCHAR(255) NOT NULL,
                model_version VARCHAR(50) NOT NULL,
                model_type VARCHAR(100),
                hyperparameters JSONB,
                is_active BOOLEAN DEFAULT false,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(model_name, model_version)
            )', env);
        
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS ml_%s.predictions (
                prediction_id SERIAL PRIMARY KEY,
                model_id INTEGER,
                game_id VARCHAR(50),
                prediction_type VARCHAR(50),
                predicted_value FLOAT,
                confidence FLOAT,
                predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )', env);
        
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS ml_%s.prediction_results (
                result_id SERIAL PRIMARY KEY,
                prediction_id INTEGER UNIQUE,
                actual_value FLOAT,
                is_correct BOOLEAN,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (prediction_id) REFERENCES ml_%s.predictions(prediction_id)
            )', env, env);
        
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS ml_%s.betting_results (
                bet_id SERIAL PRIMARY KEY,
                prediction_id INTEGER,
                bet_amount FLOAT,
                odds FLOAT,
                profit_loss FLOAT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )', env);
        
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS ml_%s.feature_importances (
                importance_id SERIAL PRIMARY KEY,
                model_id INTEGER NOT NULL,
                feature_name VARCHAR(255) NOT NULL,
                importance_score NUMERIC(10,6),
                importance_rank INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES ml_%s.model_registry(model_id),
                UNIQUE(model_id, feature_name)
            )', env, env);
        
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS ml_%s.feature_shap_values (
                shap_id SERIAL PRIMARY KEY,
                model_id INTEGER NOT NULL,
                game_id VARCHAR(50),
                feature_name VARCHAR(255) NOT NULL,
                shap_value NUMERIC(10,6),
                base_value NUMERIC(10,6),
                prediction_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES ml_%s.model_registry(model_id),
                FOREIGN KEY (prediction_id) REFERENCES ml_%s.predictions(prediction_id)
            )', env, env, env);
    END LOOP;
END $$;

-- Grant permissions
DO $$
DECLARE
    schema_name TEXT;
BEGIN
    FOR schema_name IN 
        SELECT nspname FROM pg_namespace 
        WHERE nspname LIKE 'raw_%' 
           OR nspname LIKE 'staging_%' 
           OR nspname LIKE 'marts_%' 
           OR nspname LIKE 'features_%' 
           OR nspname LIKE 'ml_%'
           OR nspname = 'nba'
    LOOP
        EXECUTE format('GRANT ALL ON SCHEMA %I TO postgres', schema_name);
    END LOOP;
END $$;
