-- 003_user_profiles.sql (final, idempotent, production-safe)
-- ---------------------------------------------------------
-- 주요 내용
-- 1) 확장: pg_trgm (부분검색용)   [필요시 활성화]
-- 2) ENUM: gender_t(+'prefer_not_to_say'), military_service_t
-- 3) 테이블: user_profiles (gender/military_service는 우선 TEXT로 생성 후 2단계 ENUM 마이그레이션)
-- 4) 제약: age 15~100, keywords(비어있음 금지, #프리픽스, 중복 금지, 화이트리스트),
--          language_scores는 object, toeic은 정수 0~990
-- 5) 인덱스: toeic 표현식(::int), major GIN TRGM, 기타 자주 조회 필드
-- 6) 트리거: updated_at 자동 갱신
-- 7) 주석: 마이그레이션 이후 상태 기준으로 최신화

BEGIN;

-- 1) 확장 (선택)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- 이 파일에서는 UUID 생성 안 쓰므로 선택

-- 2) ENUM 타입 (idempotent) + gender_t 값 보강
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'gender_t') THEN
    CREATE TYPE gender_t AS ENUM ('male','female','prefer_not_to_say');
  END IF;

  -- 이미 gender_t가 있는데 prefer_not_to_say가 없다면 추가
  IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'gender_t')
     AND NOT EXISTS (
       SELECT 1 FROM pg_enum e
       JOIN pg_type t ON t.oid = e.enumtypid
       WHERE t.typname = 'gender_t' AND e.enumlabel = 'prefer_not_to_say'
     )
  THEN
    ALTER TYPE gender_t ADD VALUE 'prefer_not_to_say';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'military_service_t') THEN
    CREATE TYPE military_service_t AS ENUM ('completed','pending','exempt','n/a');
  END IF;
END$$;

-- 3) 공용 트리거 함수
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4) user_profiles (gender/military_service는 TEXT로 우선 생성)
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id           UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

  -- 필수
  gender            TEXT NOT NULL,                 -- 이후 ENUM으로 마이그레이션
  age               INT  NOT NULL,
  major             TEXT NOT NULL,
  grade             INT  NOT NULL CHECK (grade BETWEEN 1 AND 6),
  keywords          TEXT[] NOT NULL DEFAULT ARRAY[]::text[],

  -- 선택
  military_service  TEXT,                          -- 이후 ENUM으로 마이그레이션
  income_bracket    INT CHECK (income_bracket BETWEEN 0 AND 10),
  gpa               NUMERIC(3,2) CHECK (gpa BETWEEN 0 AND 4.50),
  language_scores   JSONB DEFAULT '{}'::jsonb,

  -- 타임스탬프
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5) CHECK 제약 (순서 중요, 모두 재실행 안전)
-- 5.1 age
ALTER TABLE user_profiles
  DROP CONSTRAINT IF EXISTS user_profiles_age_check;
ALTER TABLE user_profiles
  ADD  CONSTRAINT user_profiles_age_check CHECK (age BETWEEN 15 AND 100);

-- 5.2 keywords 품질 + 화이트리스트
ALTER TABLE user_profiles
  DROP CONSTRAINT IF EXISTS chk_user_profiles_keywords_nonempty,
  DROP CONSTRAINT IF EXISTS chk_user_profiles_keywords_hashprefix,
  DROP CONSTRAINT IF EXISTS chk_user_profiles_keywords_nodup,
  DROP CONSTRAINT IF EXISTS chk_user_profiles_keywords_whitelist;

-- 5.2.1 비어있는 배열 금지
ALTER TABLE user_profiles
  ADD CONSTRAINT chk_user_profiles_keywords_nonempty
  CHECK (cardinality(keywords) >= 1);

-- 5.2.2 모든 요소가 '#'로 시작
ALTER TABLE user_profiles
  ADD CONSTRAINT chk_user_profiles_keywords_hashprefix
  CHECK ((SELECT bool_and(k LIKE '#%') FROM unnest(keywords) AS t(k)));

-- 5.2.3 중복 금지
ALTER TABLE user_profiles
  ADD CONSTRAINT chk_user_profiles_keywords_nodup
  CHECK (
    cardinality(keywords) =
    (SELECT count(DISTINCT k) FROM unnest(keywords) AS t(k))
  );

-- 5.2.4 화이트리스트 강제
ALTER TABLE user_profiles
  ADD CONSTRAINT chk_user_profiles_keywords_whitelist
  CHECK (
    keywords <@ ARRAY[
      -- 대분류
      '#학사', '#장학', '#취업', '#행사', '#공모전/대회', '#국제교류', '#일반',
      -- 학사 소분류
      '#소속변경', '#캠퍼스내소속변경', '#휴학', '#복학', '#수강신청', '#졸업', '#등록금', '#교과목', '#전공과목', '#다전공',
      -- 장학 소분류
      '#장학금', '#장학생', '#장학생선발', '#블루버터플라이', '#fellowship', '#가계곤란', '#needbased', '#성적우수', '#신입생', '#생활비', '#재단명',
      -- 취업 소분류
      '#채용', '#공개채용', '#임용', '#인턴십', '#현장실습', '#강사', '#비전임교원', '#조교', '#채용설명회', '#취업특강', '#지원서', '#기업명', '#직무',
      -- 행사 소분류
      '#특강', '#워크숍', '#세미나', '#설명회', '#포럼', '#개최', '#교육', '#프로그램', '#AI', '#리더십', '#창업',
      -- 공모전/대회 소분류
      '#공모전', '#경진대회', '#숏폼', '#영상', '#아이디어', '#논문', '#학생설계전공', '#마이크로전공',
      -- 국제교류 소분류
      '#교환학생', '#파견', '#선발', '#campusasia', '#글로벌', '#단기', '#하계', '#동계', '#어학연수', '#일본', '#미국'
    ]::text[]
  );

-- 5.3 language_scores 품질 + toeic 정수 일관성
ALTER TABLE user_profiles
  DROP CONSTRAINT IF EXISTS chk_user_profiles_langscores_object,
  DROP CONSTRAINT IF EXISTS chk_user_profiles_toeic_range;

-- object 타입 강제
ALTER TABLE user_profiles
  ADD CONSTRAINT chk_user_profiles_langscores_object
  CHECK (language_scores IS NULL OR jsonb_typeof(language_scores) = 'object');

-- toeic: 정수 0~990만 허용(정규식 + 범위)
ALTER TABLE user_profiles
  ADD CONSTRAINT chk_user_profiles_toeic_range
  CHECK (
    language_scores IS NULL
    OR NOT (language_scores ? 'toeic')
    OR (
      (language_scores->>'toeic') ~ '^[0-9]+$'
      AND (language_scores->>'toeic')::int BETWEEN 0 AND 990
    )
  );

-- 6) 인덱스 (검증 이후 생성, 재실행 안전)
DROP INDEX IF EXISTS idx_user_profiles_toeic;
CREATE INDEX IF NOT EXISTS idx_user_profiles_toeic
  ON user_profiles (((language_scores->>'toeic')::int))
  WHERE language_scores ? 'toeic';

CREATE INDEX IF NOT EXISTS idx_user_profiles_major_trgm
  ON user_profiles USING GIN (major gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_user_profiles_grade       ON user_profiles (grade);
CREATE INDEX IF NOT EXISTS idx_user_profiles_gender      ON user_profiles (gender);
CREATE INDEX IF NOT EXISTS idx_user_profiles_income      ON user_profiles (income_bracket);
CREATE INDEX IF NOT EXISTS idx_user_profiles_lang_scores ON user_profiles USING GIN (language_scores);
CREATE INDEX IF NOT EXISTS idx_user_profiles_keywords    ON user_profiles USING GIN (keywords);

-- 7) ENUM 마이그레이션 (안전 2단계, idempotent)
-- 7.1 gender → gender_t
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='user_profiles' AND column_name='gender' AND data_type='text'
  ) THEN
    ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS gender_new gender_t;

    UPDATE user_profiles
    SET gender_new =
      CASE lower(gender)
        WHEN 'male'   THEN 'male'::gender_t
        WHEN 'female' THEN 'female'::gender_t
        ELSE 'prefer_not_to_say'::gender_t
      END
    WHERE gender_new IS NULL;

    ALTER TABLE user_profiles DROP COLUMN gender;
    ALTER TABLE user_profiles RENAME COLUMN gender_new TO gender;

    -- 필요 시 NOT NULL 강제
    ALTER TABLE user_profiles ALTER COLUMN gender SET NOT NULL;

    RAISE NOTICE 'Column "gender" migrated to ENUM.';
  END IF;
END$$;

-- 7.2 military_service → military_service_t
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='user_profiles' AND column_name='military_service' AND data_type='text'
  ) THEN
    ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS military_service_new military_service_t;

    UPDATE user_profiles
    SET military_service_new =
      CASE lower(military_service)
        WHEN 'completed' THEN 'completed'::military_service_t
        WHEN 'pending'   THEN 'pending'::military_service_t
        WHEN 'exempt'    THEN 'exempt'::military_service_t
        WHEN 'n/a'       THEN 'n/a'::military_service_t
        ELSE 'n/a'::military_service_t
      END
    WHERE military_service_new IS NULL;

    ALTER TABLE user_profiles DROP COLUMN military_service;
    ALTER TABLE user_profiles RENAME COLUMN military_service_new TO military_service;

    RAISE NOTICE 'Column "military_service" migrated to ENUM.';
  END IF;
END$$;

-- 8) updated_at 트리거
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgname = 'trg_user_profiles_updated_at' AND tgrelid = 'user_profiles'::regclass
  ) THEN
    CREATE TRIGGER trg_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;

-- 9) 주석 (마이그레이션 이후 상태 기준)
COMMENT ON TABLE  user_profiles                        IS '사용자 프로필 정보 (ENUM, JSONB, CHECK 강화, idempotent migration)';
COMMENT ON COLUMN user_profiles.gender                  IS '성별 (gender_t: male/female/prefer_not_to_say)';
COMMENT ON COLUMN user_profiles.age                     IS '나이 (15~100)';
COMMENT ON COLUMN user_profiles.major                   IS '전공명 (자유 입력, 부분검색 지원은 선택)';
COMMENT ON COLUMN user_profiles.grade                   IS '학년 (1~6)';
COMMENT ON COLUMN user_profiles.keywords                IS '관심 해시태그 배열 (non-empty, # prefix, no-duplicates, whitelist enforced)';
COMMENT ON COLUMN user_profiles.military_service        IS '병역 여부 (military_service_t: completed/pending/exempt/n/a)';
COMMENT ON COLUMN user_profiles.income_bracket          IS '소득 분위 (0~10)';
COMMENT ON COLUMN user_profiles.gpa                     IS '학점 (0.00~4.50)';
COMMENT ON COLUMN user_profiles.language_scores         IS '어학 점수(JSONB). 예: {"toeic": 900, "jlpt": "N2"}';
COMMENT ON COLUMN user_profiles.created_at              IS '생성 시각';
COMMENT ON COLUMN user_profiles.updated_at              IS '수정 시각 (trigger: set_updated_at)';

COMMIT;
