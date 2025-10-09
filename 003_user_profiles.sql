-- 003_user_profiles.sql
-- 목적: 사용자 프로필 정보(학년, 전공, GPA, TOEIC, 관심 키워드) 저장 테이블 생성
-- 특징: idempotent, 키워드 화이트리스트 제약, 자동 updated_at 갱신

-- ============================================================================
-- 1) 트랜잭션 시작
-- ============================================================================
BEGIN;

-- ============================================================================
-- 2) 공용 트리거 함수 재선언 (idempotent)
-- ============================================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 3) user_profiles 테이블 생성
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  grade      INT CHECK (grade BETWEEN 1 AND 6),
  major      TEXT,
  gpa        NUMERIC(3,2) CHECK (gpa BETWEEN 0 AND 4.50),
  toeic      INT CHECK (toeic BETWEEN 0 AND 990),
  keywords   TEXT[] CHECK (
    keywords <@ ARRAY['학사','장학','행사','취업','국제교류','공모전/대회','일반']::text[]
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 4) 인덱스
-- ============================================================================
-- 학년별 필터링
CREATE INDEX IF NOT EXISTS idx_user_profiles_grade 
  ON user_profiles (grade);

-- TOEIC 점수 범위 검색
CREATE INDEX IF NOT EXISTS idx_user_profiles_toeic 
  ON user_profiles (toeic);

-- 키워드 배열 검색 (GIN 인덱스)
CREATE INDEX IF NOT EXISTS idx_user_profiles_keywords 
  ON user_profiles USING GIN (keywords);

-- 전공 검색 (선택적)
CREATE INDEX IF NOT EXISTS idx_user_profiles_major 
  ON user_profiles (major);

-- ============================================================================
-- 5) 트리거 생성 (조건부)
-- ============================================================================
-- updated_at 자동 갱신 트리거
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger 
    WHERE tgname = 'trg_user_profiles_updated_at'
  ) THEN
    CREATE TRIGGER trg_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
END $$;

-- ============================================================================
-- 6) 테이블/컬럼 주석
-- ============================================================================
COMMENT ON TABLE user_profiles IS 
  '사용자 프로필 정보. user_id를 PK로 사용하여 1:1 관계 보장';

COMMENT ON COLUMN user_profiles.user_id IS 
  '사용자 ID (users 테이블 참조, CASCADE 삭제)';

COMMENT ON COLUMN user_profiles.grade IS 
  '학년 (1~6학년, 대학원생 포함)';

COMMENT ON COLUMN user_profiles.major IS 
  '전공명 (자유 입력)';

COMMENT ON COLUMN user_profiles.gpa IS 
  '학점 (0.00~4.50 스케일)';

COMMENT ON COLUMN user_profiles.toeic IS 
  'TOEIC 점수 (0~990점)';

COMMENT ON COLUMN user_profiles.keywords IS 
  '관심 카테고리 키워드 배열 (화이트리스트: 학사, 장학, 행사, 취업, 국제교류, 공모전/대회, 일반)';

COMMENT ON COLUMN user_profiles.created_at IS 
  '프로필 생성 시각';

COMMENT ON COLUMN user_profiles.updated_at IS 
  '마지막 수정 시각 (자동 갱신)';

-- ============================================================================
-- 7) 트랜잭션 커밋
-- ============================================================================
COMMIT;

-- ============================================================================
-- 검증 쿼리 (주석 해제 후 수동 실행 가능)
-- ============================================================================
-- -- 테이블 구조 확인
-- \d user_profiles
--
-- -- 인덱스 확인
-- \di idx_user_profiles_keywords
-- \di idx_user_profiles_grade
-- \di idx_user_profiles_toeic
--
-- -- 트리거 확인
-- SELECT tgname FROM pg_trigger WHERE tgrelid = 'user_profiles'::regclass;
--
-- -- 샘플 데이터 삽입 테스트 (users 테이블에 데이터 필요)
-- -- INSERT INTO user_profiles (user_id, grade, major, gpa, toeic, keywords)
-- -- VALUES (
-- --   (SELECT id FROM users LIMIT 1),
-- --   3,
-- --   '컴퓨터공학',
-- --   3.75,
-- --   850,
-- --   ARRAY['장학','취업','공모전/대회']
-- -- );
--
-- -- 데이터 조회
-- SELECT * FROM user_profiles LIMIT 5;
--
-- -- 키워드 검색 테스트 (GIN 인덱스 사용)
-- -- SELECT * FROM user_profiles WHERE keywords @> ARRAY['취업'];
--
-- -- 학년별 필터링 테스트
-- -- SELECT grade, COUNT(*) FROM user_profiles GROUP BY grade;
--
-- -- 제약 위반 테스트 (에러 발생 예상)
-- -- 1) 잘못된 키워드
-- -- INSERT INTO user_profiles (user_id, keywords)
-- -- VALUES ((SELECT id FROM users LIMIT 1), ARRAY['잘못된키워드']);
-- -- 예상: ERROR: new row violates check constraint
--
-- -- 2) 범위 밖 GPA
-- -- UPDATE user_profiles SET gpa = 5.0 WHERE user_id = (SELECT user_id FROM user_profiles LIMIT 1);
-- -- 예상: ERROR: new row violates check constraint "user_profiles_gpa_check"
--
-- -- 3) 범위 밖 TOEIC
-- -- UPDATE user_profiles SET toeic = 1000 WHERE user_id = (SELECT user_id FROM user_profiles LIMIT 1);
-- -- 예상: ERROR: new row violates check constraint "user_profiles_toeic_check"
--
-- -- 4) 범위 밖 학년
-- -- UPDATE user_profiles SET grade = 7 WHERE user_id = (SELECT user_id FROM user_profiles LIMIT 1);
-- -- 예상: ERROR: new row violates check constraint "user_profiles_grade_check"