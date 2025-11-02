-- 004_search_trgm.sql
-- 목적: notices 테이블의 title, body_text 칼럼에 trigram 기반 GIN 인덱스 추가
-- 효과: ILIKE '%키워드%' 검색 속도 대폭 개선

BEGIN;

-- 확장 설치
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- title 칼럼용 인덱스
CREATE INDEX IF NOT EXISTS idx_notices_title_trgm
ON notices USING GIN (title gin_trgm_ops);

-- [수정] summary_ai 대신 body_text 칼럼용 인덱스
CREATE INDEX IF NOT EXISTS idx_notices_body_text_trgm
ON notices USING GIN (body_text gin_trgm_ops);

-- [정리] summary_ai 인덱스가 존재하면 삭제 (컬럼이 제거되었으므로)
DROP INDEX IF EXISTS idx_notices_summary_ai_trgm;

COMMIT;

-- 롤백 시:
-- DROP INDEX IF EXISTS idx_notices_title_trgm;
-- DROP INDEX IF EXISTS idx_notices_body_text_trgm;
-- DROP EXTENSION IF EXISTS pg_trgm;