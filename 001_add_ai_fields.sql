-- Migration: Add summary_ai field if not exists
-- Run this after 000_init.sql

-- Check and add summary_ai column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'notices' 
        AND column_name = 'summary_ai'
    ) THEN
        ALTER TABLE notices 
        ADD COLUMN summary_ai TEXT;
        
        COMMENT ON COLUMN notices.summary_ai IS 'AI-generated brief summary (max 180 chars, 3 sentences)';
    END IF;
END $$;

-- Optional: Add index for performance if filtering/searching by summary
-- CREATE INDEX IF NOT EXISTS idx_notices_summary_ai_not_null 
-- ON notices(id) 
-- WHERE summary_ai IS NOT NULL;

-- Statistics view including summary coverage
CREATE OR REPLACE VIEW notice_stats AS
SELECT 
    COUNT(*) as total_notices,
    COUNT(summary_ai) as notices_with_summary,
    COUNT(category_ai) as notices_with_category,
    COUNT(hashtags_ai) as notices_with_hashtags,
    ROUND(COUNT(summary_ai)::numeric / COUNT(*)::numeric * 100, 2) as summary_coverage_pct,
    ROUND(AVG(LENGTH(summary_ai)), 0) as avg_summary_length,
    MAX(LENGTH(summary_ai)) as max_summary_length
FROM notices;