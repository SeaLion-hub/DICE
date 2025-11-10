-- 005_search_fts.sql
-- PostgreSQL Full-Text Search implementation for notices table
-- Uses title (A), hashtags (B), body_text (C), and COLLEGE SYNONYMS (D)

BEGIN;

-- 1. Add tsvector search column if not exists
ALTER TABLE notices ADD COLUMN IF NOT EXISTS search_vector tsvector;
COMMENT ON COLUMN notices.search_vector IS 'Full-text search vector (title A, hashtags B, body C, college D)';

-- 2. Create/Replace function to automatically update the search vector
CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
DECLARE
  college_synonyms TEXT;
BEGIN
  -- 1. Get college synonyms based on NEW.college_key
  CASE NEW.college_key
    WHEN 'main' THEN college_synonyms := '메인';
    WHEN 'liberal' THEN college_synonyms := '문과';
    WHEN 'business' THEN college_synonyms := '상경';
    WHEN 'management' THEN college_synonyms := '경영';
    WHEN 'engineering' THEN college_synonyms := '공과 공학 공대';
    WHEN 'life' THEN college_synonyms := '생명 생시 생명시스템';
    WHEN 'ai' THEN college_synonyms := '인공지능 ai';
    WHEN 'theology' THEN college_synonyms := '신학 신과';
    WHEN 'social' THEN college_synonyms := '사회과학 사과';
    WHEN 'music' THEN college_synonyms := '음악 음대';
    WHEN 'human' THEN college_synonyms := '생활과학 생과';
    WHEN 'education' THEN college_synonyms := '교육과학 교과';
    WHEN 'underwood' THEN college_synonyms := '언더우드 uic 국제';
    WHEN 'global' THEN college_synonyms := '글로벌인재 glc';
    WHEN 'medicine' THEN college_synonyms := '의과 의대';
    WHEN 'dentistry' THEN college_synonyms := '치과 치대';
    WHEN 'nursing' THEN college_synonyms := '간호';
    WHEN 'pharmacy' THEN college_synonyms := '약학 약대';
    ELSE college_synonyms := '';
  END CASE;

  -- 2. Assign weights: Title 'A', Hashtags 'B', Body 'C', College 'D'
  NEW.search_vector :=
    setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
    setweight(to_tsvector('simple',
      CASE
        WHEN NEW.hashtags_ai IS NOT NULL THEN array_to_string(NEW.hashtags_ai, ' ')
        ELSE ''
      END
    ), 'B') ||
    setweight(to_tsvector('simple', coalesce(NEW.body_text, '')), 'C') ||
    setweight(to_tsvector('simple', coalesce(college_synonyms, '')), 'D'); -- Added Weight D
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. Create trigger to automatically update search_vector on insert or update
DROP TRIGGER IF EXISTS trg_update_search_vector ON notices;
CREATE TRIGGER trg_update_search_vector
BEFORE INSERT OR UPDATE OF title, hashtags_ai, body_text, college_key ON notices
FOR EACH ROW EXECUTE FUNCTION update_search_vector();

-- 4. Create GIN index for high-performance search
CREATE INDEX IF NOT EXISTS idx_notices_search_vector
ON notices USING GIN(search_vector);

-- 5. Backfill existing data (Modified to join colleges and add synonyms)
--    This updates ALL rows that have a college_key to apply the new vector.
UPDATE notices n
SET search_vector =
  setweight(to_tsvector('simple', coalesce(n.title, '')), 'A') ||
  setweight(to_tsvector('simple',
    CASE
      WHEN n.hashtags_ai IS NOT NULL THEN array_to_string(n.hashtags_ai, ' ')
      ELSE ''
    END
  ), 'B') ||
  setweight(to_tsvector('simple', coalesce(n.body_text, '')), 'C') ||
  setweight(to_tsvector('simple', coalesce(c_syn.synonyms, '')), 'D') -- Added Weight D
FROM (
  -- This subquery generates the synonyms for all colleges
  SELECT key,
    CASE key
      WHEN 'main' THEN '메인'
      WHEN 'liberal' THEN '문과'
      WHEN 'business' THEN '상경'
      WHEN 'management' THEN '경영'
      WHEN 'engineering' THEN '공과 공학 공대'
      WHEN 'life' THEN '생명 생시 생명시스템'
      WHEN 'ai' THEN '인공지능 ai'
      WHEN 'theology' THEN '신학 신과'
      WHEN 'social' THEN '사회과학 사과'
      WHEN 'music' THEN '음악 음대'
      WHEN 'human' THEN '생활과학 생과'
      WHEN 'education' THEN '교육과학 교과'
      WHEN 'underwood' THEN '언더우드 uic 국제'
      WHEN 'global' THEN '글로벌인재 glc'
      WHEN 'medicine' THEN '의과 의대'
      WHEN 'dentistry' THEN '치과 치대'
      WHEN 'nursing' THEN '간호'
      WHEN 'pharmacy' THEN '약학 약대'
      ELSE ''
    END AS synonyms
  FROM colleges
) AS c_syn
WHERE n.college_key = c_syn.key;

COMMIT;

-- [Rollback Guide]
-- DROP INDEX IF EXISTS idx_notices_search_vector;
-- DROP TRIGGER IF EXISTS trg_update_search_vector ON notices;
-- DROP FUNCTION IF EXISTS update_search_vector();
-- ALTER TABLE notices DROP COLUMN IF EXISTS search_vector;