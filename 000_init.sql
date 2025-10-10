-- 1) 확장
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- 선택: 추후 추천/유사 검색 시 벡터 사용
-- CREATE EXTENSION IF NOT EXISTS "vector";

-- 2) updated_at 트리거 함수
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3) 테이블

-- 3.1 colleges
CREATE TABLE IF NOT EXISTS colleges (
  key        TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  url        TEXT,
  color      TEXT,
  icon       TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP TRIGGER IF EXISTS trg_colleges_updated_at ON colleges;
CREATE TRIGGER trg_colleges_updated_at
BEFORE UPDATE ON colleges
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- 3.2 notices
CREATE TABLE IF NOT EXISTS notices (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  college_key   TEXT REFERENCES colleges(key) ON DELETE SET NULL,
  title         TEXT NOT NULL,
  url           TEXT NOT NULL,
  summary_raw   TEXT,
  summary_ai    TEXT,
  body_html     TEXT,
  body_text     TEXT,
  published_at  TIMESTAMPTZ,
  source_site   TEXT,
  content_hash  TEXT NOT NULL UNIQUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP TRIGGER IF EXISTS trg_notices_updated_at ON notices;
CREATE TRIGGER trg_notices_updated_at
BEFORE UPDATE ON notices
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- 4) 인덱스
CREATE INDEX IF NOT EXISTS idx_notices_college_pub ON notices (college_key, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_notices_url        ON notices (url);
