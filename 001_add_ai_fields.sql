-- Migration: Add AI-related fields if they don't exist
-- Run this after 000_init.sql

BEGIN;

-- Add category_ai column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'notices' AND column_name = 'category_ai'
    ) THEN
        ALTER TABLE notices ADD COLUMN category_ai TEXT;
        COMMENT ON COLUMN notices.category_ai IS 'AI-extracted category (e.g., 장학, 채용, 행사)';
    END IF;
END $$;

-- Add start_at_ai column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'notices' AND column_name = 'start_at_ai'
    ) THEN
        ALTER TABLE notices ADD COLUMN start_at_ai TIMESTAMPTZ;
        COMMENT ON COLUMN notices.start_at_ai IS 'AI-extracted event/application start date';
    END IF;
END $$;

-- Add end_at_ai column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'notices' AND column_name = 'end_at_ai'
    ) THEN
        ALTER TABLE notices ADD COLUMN end_at_ai TIMESTAMPTZ;
        COMMENT ON COLUMN notices.end_at_ai IS 'AI-extracted event/application end date or deadline';
    END IF;
END $$;

-- Add qualification_ai column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'notices' AND column_name = 'qualification_ai'
    ) THEN
        ALTER TABLE notices ADD COLUMN qualification_ai JSONB;
        COMMENT ON COLUMN notices.qualification_ai IS 'AI-extracted qualification details (JSONB)';
    END IF;
END $$;

-- Add hashtags_ai column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'notices' AND column_name = 'hashtags_ai'
    ) THEN
        ALTER TABLE notices ADD COLUMN hashtags_ai TEXT[];
        COMMENT ON COLUMN notices.hashtags_ai IS 'AI-generated hashtags based on title';
    END IF;
END $$;

-- Remove summary_ai column if it exists (Backward compatibility for removal)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'notices' AND column_name = 'summary_ai'
    ) THEN
        ALTER TABLE notices DROP COLUMN summary_ai;
    END IF;
END $$;


-- Indexes for AI fields
CREATE INDEX IF NOT EXISTS idx_notices_category_ai ON notices (category_ai);
CREATE INDEX IF NOT EXISTS idx_notices_end_at_ai ON notices (end_at_ai DESC);
CREATE INDEX IF NOT EXISTS idx_notices_hashtags_ai_gin ON notices USING GIN (hashtags_ai); -- 006에서 이동

-- Statistics view updated (removed summary_ai fields)
CREATE OR REPLACE VIEW notice_stats AS
SELECT
    COUNT(*) as total_notices,
    COUNT(category_ai) as notices_with_category,
    COUNT(hashtags_ai) as notices_with_hashtags,
    COUNT(qualification_ai) as notices_with_qualification,
    COUNT(end_at_ai) as notices_with_deadline,
    ROUND(COUNT(category_ai)::numeric / COUNT(*)::numeric * 100, 2) as category_coverage_pct,
    ROUND(COUNT(hashtags_ai)::numeric / COUNT(*)::numeric * 100, 2) as hashtag_coverage_pct
FROM notices;

COMMIT;