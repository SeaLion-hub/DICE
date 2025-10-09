-- 001_add_ai_fields.sql
-- 목적: 공지사항 테이블에 AI 추출 결과 필드 추가

ALTER TABLE notices
  ADD COLUMN IF NOT EXISTS category_ai       TEXT,
  ADD COLUMN IF NOT EXISTS start_at_ai       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS end_at_ai         TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS qualification_ai  JSONB,
  ADD COLUMN IF NOT EXISTS hashtags_ai       TEXT[];

-- 성능 향상을 위한 인덱스들
CREATE INDEX IF NOT EXISTS idx_notices_category_ai ON notices (category_ai);
CREATE INDEX IF NOT EXISTS idx_notices_start_at_ai ON notices (start_at_ai);
CREATE INDEX IF NOT EXISTS idx_notices_end_at_ai   ON notices (end_at_ai);
CREATE INDEX IF NOT EXISTS idx_notices_hashtags_ai_gin ON notices USING GIN (hashtags_ai);
CREATE INDEX IF NOT EXISTS idx_notices_qualification_ai_gin ON notices USING GIN (qualification_ai);
