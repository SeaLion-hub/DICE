# db_init.py
import os
import re
import sqlparse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

# ---------- helpers ----------
def get_db_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 환경변수가 없습니다.")
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url

def load_schema_sql(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"스키마 파일을 찾을 수 없습니다: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def normalize_sql(s: str) -> str:
    # 공백/개행 정규화 + 소문자
    s2 = re.sub(r"\s+", " ", s.strip())
    return s2.lower()

def split_statements(sql: str):
    # sqlparse로 안전 분리
    statements = [s.strip() for s in sqlparse.split(sql) if s.strip()]
    # -- 주석으로 시작하는 문장은 제거
    statements = [s for s in statements if not s.strip().startswith("--")]
    return statements

# PostgreSQL SQLSTATE codes we want to ignore in idempotent runs
PG_DUPLICATE_OBJECT = "42710"  # duplicate_object (type, function 등)
PG_DUPLICATE_TABLE  = "42P07"  # duplicate_table
PG_DUPLICATE_SCHEMA = "42P06"
PG_DUPLICATE_ALIAS  = "42712"
PG_DUPLICATE_COLUMN = "42701"
PG_DUPLICATE_PKEY   = "23505"  # unique violation (INSERT ON CONFLICT 없는 초기데이터 등)
PG_UNDEFINED_TABLE  = "42P01"  # undefined_table

def should_ignore_error(phase: str, err: DBAPIError) -> bool:
    """재실행 시 무시 가능한 에러는 건너뛰고 계속."""
    pgcode = getattr(getattr(err, "orig", None), "pgcode", None)
    if not pgcode:
        return False
    
    # 타입/인덱스/뷰/트리거/함수/테이블 재실행 시의 중복은 무시
    if pgcode in {PG_DUPLICATE_OBJECT, PG_DUPLICATE_TABLE, PG_DUPLICATE_SCHEMA, PG_DUPLICATE_ALIAS, PG_DUPLICATE_COLUMN}:
        return True
    # 초기데이터 중복(유니크 위반)도 무시
    if phase == "Initial Data" and pgcode == PG_DUPLICATE_PKEY:
        return True
    
    return False

def ensure_enums_exist(engine):
    """ENUM 타입들이 확실히 존재하도록 강제 생성"""
    print("🔧 ENUM 타입 존재 확인 및 생성...")
    
    enum_sqls = [
        """
        DO $ 
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
                CREATE TYPE user_role AS ENUM ('student', 'admin', 'moderator');
                RAISE NOTICE 'user_role ENUM created';
            ELSE
                RAISE NOTICE 'user_role ENUM already exists';
            END IF;
        END $;
        """,
        """
        DO $ 
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_category') THEN
                CREATE TYPE notice_category AS ENUM (
                    'general','scholarship','internship','competition',
                    'recruitment','academic','seminar','event'
                );
                RAISE NOTICE 'notice_category ENUM created';
            ELSE
                RAISE NOTICE 'notice_category ENUM already exists';
            END IF;
        END $;
        """,
        """
        DO $ 
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_status') THEN
                CREATE TYPE notice_status AS ENUM ('active', 'archived', 'deleted');
                RAISE NOTICE 'notice_status ENUM created';
            ELSE
                RAISE NOTICE 'notice_status ENUM already exists';
            END IF;
        END $;
        """
    ]
    
    for i, enum_sql in enumerate(enum_sqls, 1):
        try:
            with engine.begin() as conn:
                conn.execute(text(enum_sql))
            print(f"  ✅ ENUM {i}/3 처리 완료")
        except Exception as e:
            print(f"  ❌ ENUM {i}/3 처리 실패: {e}")
            # ENUM 생성 실패 시 계속 진행하지 않음
            raise

def run_statements(engine, statements, title):
    """각 문장을 개별 트랜잭션으로 실행"""
    if not statements:
        return
    print(f"▶ {title}: {len(statements)} statements")
    
    for i, stmt in enumerate(statements, start=1):
        # 각 문장을 개별 트랜잭션으로 실행
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except DBAPIError as e:
            if should_ignore_error(title, e):
                # 이미 존재 → 스킵하고 계속
                print(f"  ⚠️ 스킵 ({title} #{i}): 이미 존재")
                continue
            # 실패한 문장 로그 후 중단
            print(f"\n❌ 실패 ({title} #{i}):\n{stmt}\n")
            raise

def categorize_statements(statements):
    """간단하고 확실한 방법으로 SQL 문장들을 분류"""
    
    categories = {
        "extensions": [],
        "types": [],
        "users_table": [],
        "other_tables": [],
        "indexes": [],
        "functions": [],
        "triggers": [],
        "inserts": [],
        "views": [],
        "other": []
    }
    
    for stmt in statements:
        normalized = normalize_sql(stmt)
        
        if re.match(r"^\s*create\s+extension", normalized):
            categories["extensions"].append(stmt)
        elif re.match(r"^\s*create\s+type", normalized):
            categories["types"].append(stmt)
        elif re.search(r"create\s+table.*\busers\s*\(", normalized) and not re.search(r"user_", normalized):
            # users 테이블만 정확히 찾기 (user_settings 등 제외)
            categories["users_table"].append(stmt)
            print(f"✅ USERS 테이블 발견!")
        elif re.match(r"^\s*create\s+table", normalized):
            categories["other_tables"].append(stmt)
        elif re.match(r"^\s*create\s+(unique\s+)?index", normalized):
            categories["indexes"].append(stmt)
        elif re.match(r"^\s*create\s+(or\s+replace\s+)?function", normalized):
            categories["functions"].append(stmt)
        elif re.match(r"^\s*(create|drop)\s+trigger", normalized):
            categories["triggers"].append(stmt)
        elif re.match(r"^\s*insert\s+into", normalized):
            categories["inserts"].append(stmt)
        elif re.match(r"^\s*create\s+(or\s+replace\s+)?view", normalized):
            categories["views"].append(stmt)
        else:
            categories["other"].append(stmt)
    
    return categories

def apply_schema(engine, sql: str):
    statements = split_statements(sql)
    categories = categorize_statements(statements)
    
    print(f"\n🔍 디버그: 총 {len(statements)} 개 문장 발견")
    print(f"📊 카테고리별 분류:")
    for name, stmts in categories.items():
        print(f"  {name}: {len(stmts)} statements")
    
    # users 테이블이 없으면 동적으로 생성
    if not categories["users_table"]:
        print("🚨 users 테이블이 없습니다. 동적으로 생성합니다.")
        users_sql = """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            name VARCHAR(100),
            student_id VARCHAR(20),
            major VARCHAR(100),
            gpa DECIMAL(3,2) CHECK (gpa >= 0 AND gpa <= 4.5),
            toeic_score INTEGER CHECK (toeic_score >= 0 AND toeic_score <= 990),
            role user_role DEFAULT 'student',
            is_active BOOLEAN DEFAULT true,
            email_verified BOOLEAN DEFAULT false,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT email_format CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}

def main():
    db_url = get_db_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    schema_sql = load_schema_sql("schema.sql")
    apply_schema(engine, schema_sql)
    print("✅ schema.sql 적용 완료")

if __name__ == "__main__":
    main())
        );
        """
        categories["users_table"].append(users_sql)
        print("✅ users 테이블을 동적으로 추가했습니다.")
    
    # 실행 순서 (의존성 고려)
    print(f"\n🚀 실행 순서:")
    
    # 1. Extensions
    run_statements(engine, categories["extensions"], "Extensions")
    
    # 2. ENUM 타입 강제 확인 및 생성
    ensure_enums_exist(engine)
    
    # 3. Users 테이블 먼저
    run_statements(engine, categories["users_table"], "Users Table")
    
    # 4. 나머지 테이블들
    run_statements(engine, categories["other_tables"], "Other Tables")
    
    # 5. 인덱스
    run_statements(engine, categories["indexes"], "Indexes")
    
    # 6. 함수
    run_statements(engine, categories["functions"], "Functions")
    
    # 7. 트리거
    run_statements(engine, categories["triggers"], "Triggers")
    
    # 8. 초기 데이터
    run_statements(engine, categories["inserts"], "Initial Data")
    
    # 9. 뷰
    run_statements(engine, categories["views"], "Views")
    
    # 10. 기타
    run_statements(engine, categories["other"], "Other")

def main():
    db_url = get_db_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    schema_sql = load_schema_sql("schema.sql")
    apply_schema(engine, schema_sql)
    print("✅ schema.sql 적용 완료")

if __name__ == "__main__":
    main()