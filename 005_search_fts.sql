-- 005_search_fts.sql
-- PostgreSQL Full-Text Search implementation for notices table
-- Uses title (A), hashtags (B), and body_text (C) for searching

BEGIN;

-- 1. Add tsvector search column if not exists
ALTER TABLE notices ADD COLUMN IF NOT EXISTS search_vector tsvector;
COMMENT ON COLUMN notices.search_vector IS 'Full-text search vector (title A, hashtags B, body C)';

-- 2. Create/Replace function to automatically update the search vector
CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
BEGIN
  -- Assign weights: Title 'A', Hashtags 'B', Body 'C'
  NEW.search_vector :=
    setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
    setweight(to_tsvector('simple',
      CASE
        WHEN NEW.hashtags_ai IS NOT NULL THEN array_to_string(NEW.hashtags_ai, ' ')
        ELSE ''
      END
    ), 'B') ||
    setweight(to_tsvector('simple', coalesce(NEW.body_text, '')), 'C');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. Create trigger to automatically update search_vector on insert or update
DROP TRIGGER IF EXISTS trg_update_search_vector ON notices;
CREATE TRIGGER trg_update_search_vector
BEFORE INSERT OR UPDATE OF title, hashtags_ai, body_text ON notices
FOR EACH ROW EXECUTE FUNCTION update_search_vector();

-- 4. Create GIN index for high-performance search
CREATE INDEX IF NOT EXISTS idx_notices_search_vector
ON notices USING GIN(search_vector);

-- 5. Backfill existing data (Important: Run this to index old notices)
--    Run this separately if you have a large table.
UPDATE notices
SET search_vector =
  setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('simple',
    CASE
      WHEN hashtags_ai IS NOT NULL THEN array_to_string(hashtags_ai, ' ')
      ELSE ''
    END
  ), 'B') ||
  setweight(to_tsvector('simple', coalesce(body_text, '')), 'C')
WHERE search_vector IS NULL; -- Only update rows that haven't been indexed

COMMIT;

-- [Rollback Guide]
-- DROP INDEX IF EXISTS idx_notices_search_vector;
-- DROP TRIGGER IF EXISTS trg_update_search_vector ON notices;
-- DROP FUNCTION IF EXISTS update_search_vector();
-- ALTER TABLE notices DROP COLUMN IF EXISTS search_vector;