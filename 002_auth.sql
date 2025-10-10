-- 002_auth.sql
-- 목적: 로그인/회원가입을 위한 users 테이블 생성 및 이메일 정규화 자동화
-- 특징: idempotent(여러 번 실행 가능), 이메일 중복 방지, 자동 updated_at 갱신

-- ============================================================================
-- 0) 확장 보장 (트랜잭션 밖에서 실행)
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 1) 트랜잭션 시작
-- ============================================================================
BEGIN;

-- ============================================================================
-- 2) 공용 트리거 함수: set_updated_at()
-- ============================================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 3) 이메일 정규화 트리거 함수
-- ============================================================================
CREATE OR REPLACE FUNCTION normalize_email_before_write()
RETURNS TRIGGER AS $$
BEGIN
  -- 앞뒤 공백 제거 + 소문자 변환
  NEW.email := lower(btrim(NEW.email));
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 4) users 테이블 생성
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email         TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 5) 인덱스
-- ============================================================================
-- 고유 인덱스: lower(email) 기준으로 중복 방지
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_uidx 
  ON users (lower(email));

-- 일반 인덱스: 이메일 조회 성능 향상
CREATE INDEX IF NOT EXISTS idx_users_email 
  ON users (email);

-- ============================================================================
-- 6) 트리거 생성 (조건부)
-- ============================================================================
-- 6.1) 이메일 정규화 트리거
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger 
    WHERE tgname = 'trg_users_email_normalize'
  ) THEN
    CREATE TRIGGER trg_users_email_normalize
    BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION normalize_email_before_write();
  END IF;
END $$;

-- 6.2) updated_at 자동 갱신 트리거
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger 
    WHERE tgname = 'trg_users_updated_at'
  ) THEN
    CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
END $$;

-- ============================================================================
-- 7) 테이블/컬럼 주석
-- ============================================================================
COMMENT ON TABLE users IS 
  '사용자 인증 정보. 이메일은 자동으로 trim+lower 정규화됨';

COMMENT ON COLUMN users.id IS 
  '사용자 고유 식별자 (UUID)';

COMMENT ON COLUMN users.email IS 
  '사용자 이메일 (자동 정규화: trim + lowercase)';

COMMENT ON COLUMN users.password_hash IS 
  '비밀번호 해시 (bcrypt 등)';

COMMENT ON COLUMN users.created_at IS 
  '계정 생성 시각';

COMMENT ON COLUMN users.updated_at IS 
  '마지막 수정 시각 (자동 갱신)';

-- ============================================================================
-- 8) 트랜잭션 커밋
-- ============================================================================
COMMIT;

-- ============================================================================
-- 검증 쿼리 (주석 해제 후 수동 실행 가능)
-- ============================================================================
-- -- 테이블 존재 확인
-- SELECT 1 WHERE EXISTS (SELECT 1 FROM pg_class WHERE relname='users');
--
-- -- 테이블 구조 확인
-- \d users
--
-- -- 고유 인덱스 확인
-- \di+ users_email_lower_uidx
--
-- -- 트리거 확인
-- SELECT tgname FROM pg_trigger WHERE tgrelid = 'users'::regclass;
--
-- -- 이메일 정규화 테스트
-- INSERT INTO users (email, password_hash) 
-- VALUES ('  Test@Example.COM  ', 'dummy_hash');
-- SELECT email FROM users WHERE id = (SELECT id FROM users ORDER BY created_at DESC LIMIT 1);
-- -- 예상 결과: 'test@example.com'
--
-- -- 중복 방지 테스트 (에러 발생 예상)
-- INSERT INTO users (email, password_hash) 
-- VALUES ('Test@Example.COM', 'another_hash');
-- -- 예상: ERROR: duplicate key value violates unique constraint "users_email_lower_uidx"