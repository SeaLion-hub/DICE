-- 005_search_fts.sql
-- 목적: notices의 제목/AI요약을 FTS 색인(tsvector)으로 관리하고 GIN 인덱스를 생성
-- 주: 한국어 완벽 형태소는 아님(간단 토크나이즈) → 보조 랭킹/AND·OR 질의용으로 충분

BEGIN;

-- 1) tsvector 컬럼 추가 (없으면 추가)
ALTER TABLE notices
ADD COLUMN IF NOT EXISTS ts_ko tsvector;

-- 2) 초기값 백필 (title + summary_ai)
UPDATE notices
SET ts_ko = to_tsvector(
  'simple',
  coalesce(title, '') || ' ' || coalesce(summary_ai, '')
);

-- 3) 변경 시 자동 갱신 함수
CREATE OR REPLACE FUNCTION notices_ts_ko_update()
RETURNS trigger AS $$
BEGIN
  NEW.ts_ko := to_tsvector(
    'simple',
    coalesce(NEW.title, '') || ' ' || coalesce(NEW.summary_ai, '')
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4) 트리거: INSERT/UPDATE 시 ts_ko 자동 생성/갱신
DROP TRIGGER IF EXISTS trg_notices_ts_ko_update ON notices;
CREATE TRIGGER trg_notices_ts_ko_update
BEFORE INSERT OR UPDATE OF title, summary_ai ON notices
FOR EACH ROW
EXECUTE FUNCTION notices_ts_ko_update();

-- 5) GIN 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_notices_ts_ko
ON notices USING GIN (ts_ko);

COMMIT;

-- [롤백 안내]
-- DROP INDEX IF EXISTS idx_notices_ts_ko;
-- DROP TRIGGER IF EXISTS trg_notices_ts_ko_update ON notices;
-- DROP FUNCTION IF EXISTS notices_ts_ko_update();
-- ALTER TABLE notices DROP COLUMN IF EXISTS ts_ko;
