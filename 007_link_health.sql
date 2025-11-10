-- 007_link_health.sql
-- Link health columns added for URL validity check

BEGIN;

-- Add link health tracking columns to notices table
ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS url_ok BOOLEAN;

ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS url_status_code INTEGER;

ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS url_checked_at TIMESTAMPTZ;

ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS url_final TEXT;

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_notices_url_ok 
ON notices (url_ok);

CREATE INDEX IF NOT EXISTS idx_notices_url_checked_at 
ON notices (url_checked_at DESC);

COMMIT;