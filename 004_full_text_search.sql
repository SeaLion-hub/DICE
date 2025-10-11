-- 004_full_text_search.sql
-- PostgreSQL Full-Text Search implementation for notices table
-- 제목과 해시태그를 사용하는 검색 구현 (해시태그 선택적)

BEGIN;

-- 1. tsvector 검색 컬럼 추가
ALTER TABLE notices ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- 2. 검색 벡터 자동 업데이트 함수 생성 (제목 + 해시태그)
CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
BEGIN
  -- 제목은 필수, 해시태그는 있으면 추가
  NEW.search_vector :=
    setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
    setweight(to_tsvector('simple', 
      CASE 
        WHEN NEW.hashtags_ai IS NOT NULL THEN array_to_string(NEW.hashtags_ai, ' ')
        ELSE ''
      END
    ), 'B');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. 자동 업데이트 트리거 생성
DROP TRIGGER IF EXISTS trg_update_search_vector ON notices;
CREATE TRIGGER trg_update_search_vector
BEFORE INSERT OR UPDATE OF title, hashtags_ai ON notices
FOR EACH ROW EXECUTE FUNCTION update_search_vector();

-- 4. 고성능 검색을 위한 GIN 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_notices_search_vector 
ON notices USING GIN(search_vector);

-- 5. 해시태그 배열 검색을 위한 GIN 인덱스 추가
CREATE INDEX IF NOT EXISTS idx_notices_hashtags_gin 
ON notices USING GIN(hashtags_ai);

-- 6. 기존 데이터 검색 벡터 업데이트
UPDATE notices 
SET search_vector = 
  setweight(to_tsvector('simple', coalesce(title, '')), 'A') || 
  setweight(to_tsvector('simple', 
    CASE 
      WHEN hashtags_ai IS NOT NULL THEN array_to_string(hashtags_ai, ' ')
      ELSE ''
    END
  ), 'B');

COMMIT;