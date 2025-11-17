-- 017_notification_settings.sql
-- 알림 설정 테이블 및 검색어 제안을 위한 테이블 생성

BEGIN;

-- 1) 알림 설정 테이블
CREATE TABLE IF NOT EXISTS user_notification_settings (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  enabled BOOLEAN NOT NULL DEFAULT true,
  deadline_days INT[] NOT NULL DEFAULT ARRAY[3, 7], -- 마감 3일, 7일 전 알림
  categories TEXT[] NOT NULL DEFAULT ARRAY[]::text[], -- 알림 받을 카테고리 (빈 배열이면 전체)
  email_notifications BOOLEAN NOT NULL DEFAULT false,
  push_notifications BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE user_notification_settings IS '사용자별 알림 설정';
COMMENT ON COLUMN user_notification_settings.deadline_days IS '마감 N일 전 알림 (예: [1, 3, 7])';
COMMENT ON COLUMN user_notification_settings.categories IS '알림 받을 카테고리 (빈 배열이면 전체)';

-- 2) updated_at 트리거
DROP TRIGGER IF EXISTS trg_user_notification_settings_updated_at ON user_notification_settings;
CREATE TRIGGER trg_user_notification_settings_updated_at
BEFORE UPDATE ON user_notification_settings
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- 3) 검색어 제안을 위한 인기 검색어 테이블 (선택사항, 초기 데이터는 백엔드에서 관리)
CREATE TABLE IF NOT EXISTS popular_keywords (
  keyword TEXT PRIMARY KEY,
  search_count INT NOT NULL DEFAULT 1,
  last_searched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE popular_keywords IS '인기 검색어 (검색어 제안 및 연관 검색어용)';

-- 4) 인기 검색어 인덱스 (pg_trgm 확장 사용)
CREATE INDEX IF NOT EXISTS idx_popular_keywords_trgm ON popular_keywords USING gin (keyword gin_trgm_ops);

-- 5) 검색 로그 테이블 (연관 검색어 분석용)
CREATE TABLE IF NOT EXISTS search_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  query TEXT NOT NULL,
  results_count INT,
  clicked_notice_id UUID REFERENCES notices(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE search_logs IS '검색 로그 (연관 검색어 분석용)';

-- 6) 검색 로그 인덱스
CREATE INDEX IF NOT EXISTS idx_search_logs_user_id ON search_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_search_logs_query ON search_logs(query);
CREATE INDEX IF NOT EXISTS idx_search_logs_created_at ON search_logs(created_at DESC);

-- 7) 초기 인기 검색어 데이터 삽입 (이미 존재하면 무시)
INSERT INTO popular_keywords (keyword, search_count) VALUES
  ('장학금', 100),
  ('인턴십', 80),
  ('공모전', 70),
  ('취업', 60),
  ('장학', 50),
  ('인턴', 45),
  ('채용', 40),
  ('국가장학', 35),
  ('성적우수', 30),
  ('해외교환', 25),
  ('교환학생', 20),
  ('행사', 15),
  ('특강', 10)
ON CONFLICT (keyword) DO NOTHING;

COMMIT;

