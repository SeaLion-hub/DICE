-- 006_hashtags_gin_index.sql
-- 해시태그 배열 검색 성능 최적화를 위한 GIN 인덱스
-- my=true 필터에서 배열 교집합 연산(&&) 속도 개선

-- hashtags_ai 배열 컬럼에 GIN 인덱스 추가
CREATE INDEX IF NOT EXISTS idx_notices_hashtags_ai_gin
ON notices USING GIN (hashtags_ai);

-- 인덱스 생성 확인
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 
        FROM pg_indexes 
        WHERE tablename = 'notices' 
        AND indexname = 'idx_notices_hashtags_ai_gin'
    ) THEN
        RAISE NOTICE 'Index idx_notices_hashtags_ai_gin created successfully';
    ELSE
        RAISE EXCEPTION 'Failed to create idx_notices_hashtags_ai_gin';
    END IF;
END $$;

-- 인덱스 통계 업데이트 (옵션)
ANALYZE notices;