-- 004_search_trgm.sql
-- 목적: notices 테이블의 title, summary_ai 칼럼에 trigram 기반 GIN 인덱스 추가
-- 효과: ILIKE '%키워드%' 검색 속도 대폭 개선

BEGIN;

-- 확장 설치
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- title 칼럼용 인덱스
CREATE INDEX IF NOT EXISTS idx_notices_title_trgm
ON notices USING GIN (title gin_trgm_ops);

-- summary_ai 칼럼용 인덱스
CREATE INDEX IF NOT EXISTS idx_notices_summary_ai_trgm
ON notices USING GIN (summary_ai gin_trgm_ops);

-- (선택) summary_raw까지 인덱싱하고 싶다면 아래 주석 해제
-- CREATE INDEX IF NOT EXISTS idx_notices_summary_raw_trgm
-- ON notices USING GIN (summary_raw gin_trgm_ops);

COMMIT;

-- 롤백 시:
-- DROP INDEX IF EXISTS idx_notices_title_trgm;
-- DROP INDEX IF EXISTS idx_notices_summary_ai_trgm;
-- DROP EXTENSION IF EXISTS pg_trgm;
