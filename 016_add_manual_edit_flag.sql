BEGIN;

ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS body_edited_manually BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN notices.body_edited_manually IS 
  'True if body_text (or raw_text) was manually edited via the admin panel';

CREATE INDEX IF NOT EXISTS idx_notices_body_edited_manually
ON notices (body_edited_manually);

COMMIT;