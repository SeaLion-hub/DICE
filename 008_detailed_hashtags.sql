-- 008_detailed_hashtags.sql
-- 세부 해시태그 저장을 위한 'detailed_hashtags' 컬럼 추가

BEGIN;

-- 1. detailed_hashtags 컬럼 추가 (TEXT 배열)
ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS detailed_hashtags TEXT[];

COMMENT ON COLUMN notices.detailed_hashtags IS 
  'AI가 2단계로 추출한 세부 해시태그 (관리자 페이지 등에서 수동 실행)';

-- 2. 세부 해시태그 검색 성능을 위한 GIN 인덱스 추가
CREATE INDEX IF NOT EXISTS idx_notices_detailed_hashtags_gin
ON notices USING GIN (detailed_hashtags);

COMMIT;