"""
run_migration.py
Windows에서 PostgreSQL 마이그레이션을 실행하는 스크립트
사용법: python run_migration.py
"""

import os
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
import sys

# .env 파일 로드
load_dotenv(encoding='utf-8')

def run_migration():
    """004_full_text_search.sql 마이그레이션 실행"""
    
    # DATABASE_URL 가져오기
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("❌ 오류: DATABASE_URL이 .env 파일에 설정되지 않았습니다.")
        return False
    
    print(f"📊 데이터베이스 연결 중...")
    print(f"   URL: {database_url[:30]}...")  # URL 일부만 표시
    
    # SQL 파일 읽기
    sql_file = '004_full_text_search.sql'
    if not os.path.exists(sql_file):
        # SQL 파일이 없으면 생성
        print(f"📝 {sql_file} 파일을 생성합니다...")
        create_migration_file(sql_file)
    
    try:
        with open(sql_file, 'r', encoding='utf-8') as f:
            migration_sql = f.read()
    except Exception as e:
        print(f"❌ SQL 파일 읽기 실패: {e}")
        return False
    
    # 데이터베이스 연결 및 실행
    try:
        print("🔧 마이그레이션 실행 중...")
        
        with psycopg2.connect(database_url) as conn:
            with conn.cursor() as cur:
                # 트랜잭션 단위로 실행
                cur.execute(migration_sql)
                conn.commit()
                
                print("✅ 마이그레이션 성공!")
                
                # 검증 쿼리 실행
                print("\n📋 검증 중...")
                verify_migration(cur)
                
    except psycopg2.Error as e:
        print(f"❌ 데이터베이스 오류: {e}")
        return False
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        return False
    
    return True

def create_migration_file(filename):
    """마이그레이션 SQL 파일 생성"""
    migration_content = """-- 004_full_text_search.sql
-- PostgreSQL Full-Text Search implementation for notices table
-- 제목과 요약만 사용하는 최적화된 검색 구현

BEGIN;

-- 1. tsvector 검색 컬럼 추가
ALTER TABLE notices ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- 2. 검색 벡터 자동 업데이트 함수 생성
CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
BEGIN
  -- 제목과 요약만 사용하여 검색 벡터 생성
  NEW.search_vector :=
    setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
    setweight(to_tsvector('simple', 
      CASE 
        WHEN NEW.summary_ai IS NOT NULL AND length(NEW.summary_ai) > 5 THEN NEW.summary_ai
        ELSE coalesce(NEW.summary_raw, '')
      END
    ), 'B');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. 자동 업데이트 트리거 생성
DROP TRIGGER IF EXISTS trg_update_search_vector ON notices;
CREATE TRIGGER trg_update_search_vector
BEFORE INSERT OR UPDATE OF title, summary_ai, summary_raw ON notices
FOR EACH ROW EXECUTE FUNCTION update_search_vector();

-- 4. 고성능 검색을 위한 GIN 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_notices_search_vector 
ON notices USING GIN(search_vector);

-- 5. 기존 데이터 검색 벡터 업데이트
UPDATE notices 
SET search_vector = 
  setweight(to_tsvector('simple', coalesce(title, '')), 'A') || 
  setweight(to_tsvector('simple', 
    CASE 
      WHEN summary_ai IS NOT NULL AND length(summary_ai) > 5 THEN summary_ai
      ELSE coalesce(summary_raw, '')
    END
  ), 'B')
WHERE search_vector IS NULL;

COMMIT;
"""
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(migration_content)
    print(f"✅ {filename} 파일이 생성되었습니다.")

def verify_migration(cursor):
    """마이그레이션 검증"""
    try:
        # 1. search_vector 컬럼 확인
        cursor.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'notices' AND column_name = 'search_vector'
        """)
        result = cursor.fetchone()
        if result:
            print(f"✅ search_vector 컬럼: {result[1]} 타입")
        else:
            print("⚠️  search_vector 컬럼이 없습니다.")
        
        # 2. 인덱스 확인
        cursor.execute("""
            SELECT indexname 
            FROM pg_indexes 
            WHERE tablename = 'notices' 
            AND indexname = 'idx_notices_search_vector'
        """)
        result = cursor.fetchone()
        if result:
            print(f"✅ GIN 인덱스: {result[0]}")
        else:
            print("⚠️  GIN 인덱스가 없습니다.")
        
        # 3. 트리거 확인
        cursor.execute("""
            SELECT tgname 
            FROM pg_trigger 
            WHERE tgname = 'trg_update_search_vector'
        """)
        result = cursor.fetchone()
        if result:
            print(f"✅ 자동 업데이트 트리거: {result[0]}")
        else:
            print("⚠️  트리거가 없습니다.")
        
        # 4. 인덱싱된 레코드 수 확인
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(search_vector) as indexed
            FROM notices
        """)
        result = cursor.fetchone()
        if result:
            total, indexed = result
            percentage = (indexed / total * 100) if total > 0 else 0
            print(f"✅ 검색 인덱싱: {indexed}/{total} ({percentage:.1f}%)")
        
        # 5. 샘플 검색 테스트
        cursor.execute("""
            SELECT COUNT(*) 
            FROM notices 
            WHERE search_vector @@ websearch_to_tsquery('simple', '장학')
        """)
        result = cursor.fetchone()
        if result:
            print(f"✅ '장학' 검색 결과: {result[0]}건")
            
    except Exception as e:
        print(f"⚠️  검증 중 오류: {e}")

def test_search():
    """검색 기능 테스트"""
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        return
    
    print("\n🔍 검색 테스트...")
    
    test_queries = ['장학', '공지', '신청', '2025']
    
    try:
        with psycopg2.connect(database_url) as conn:
            with conn.cursor() as cur:
                for query in test_queries:
                    cur.execute("""
                        SELECT 
                            COUNT(*) as count,
                            MAX(ts_rank(search_vector, websearch_to_tsquery('simple', %s))) as max_rank
                        FROM notices 
                        WHERE search_vector @@ websearch_to_tsquery('simple', %s)
                    """, (query, query))
                    
                    result = cur.fetchone()
                    if result:
                        count, max_rank = result
                        print(f"   '{query}': {count}건 (최고 관련도: {max_rank:.3f})")
                        
    except Exception as e:
        print(f"⚠️  검색 테스트 실패: {e}")

if __name__ == "__main__":
    print("=" * 60)
    print("PostgreSQL Full-Text Search 마이그레이션")
    print("=" * 60)
    
    # 마이그레이션 실행
    success = run_migration()
    
    if success:
        # 검색 테스트
        test_search()
        
        print("\n" + "=" * 60)
        print("🎉 모든 작업이 완료되었습니다!")
        print("이제 main.py를 업데이트하면 전체 텍스트 검색을 사용할 수 있습니다.")
        print("=" * 60)
    else:
        print("\n❌ 마이그레이션 실패. 위의 오류 메시지를 확인하세요.")
        sys.exit(1)