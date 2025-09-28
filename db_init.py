# db_init.py
import os
import re
import sqlparse
from sqlalchemy import create_engine

PHASE_PATTERNS = {
    "ext":     re.compile(r"^\s*CREATE\s+EXTENSION\b", re.I),
    "type":    re.compile(r"^\s*CREATE\s+TYPE\b", re.I),
    "table":   re.compile(r"^\s*CREATE\s+TABLE\b", re.I),
    "index":   re.compile(r"^\s*CREATE\s+INDEX\b", re.I),
    "func":    re.compile(r"^\s*CREATE\s+OR\s+REPLACE\s+FUNCTION\b|\bRETURNS\s+TRIGGER\b", re.I),
    "trigger": re.compile(r"^\s*CREATE\s+TRIGGER\b|^\s*DROP\s+TRIGGER\b", re.I),
    "insert":  re.compile(r"^\s*INSERT\b", re.I),
    "view":    re.compile(r"^\s*CREATE\s+OR\s+REPLACE\s+VIEW\b|^\s*CREATE\s+VIEW\b", re.I),
    # 기타 문장은 마지막에
}

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

def split_statements(sql: str):
    # sqlparse로 안전하게 분리
    statements = [s.strip() for s in sqlparse.split(sql) if s.strip()]
    # 주석 전용 문장은 제거
    statements = [s for s in statements if not s.startswith("--")]
    return statements

def bucketize(statements):
    """
    문장들을 Phase별로 분류.
    - 테이블 생성 중에는 users 테이블을 최우선 실행하도록 분리
    """
    buckets = {
        "ext": [], "type": [], "table_users": [], "table": [],
        "index": [], "func": [], "trigger": [], "insert": [], "view": [], "other": []
    }
    for stmt in statements:
        text = stmt.strip()
        if PHASE_PATTERNS["ext"].match(text):
            buckets["ext"].append(stmt)
        elif PHASE_PATTERNS["type"].match(text):
            buckets["type"].append(stmt)
        elif PHASE_PATTERNS["table"].match(text):
            # users 테이블을 제일 먼저
            if re.search(r"\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+users\b", text, re.I) or \
               re.search(r"\bCREATE\s+TABLE\s+users\b", text, re.I):
                buckets["table_users"].append(stmt)
            else:
                buckets["table"].append(stmt)
        elif PHASE_PATTERNS["index"].match(text):
            buckets["index"].append(stmt)
        elif PHASE_PATTERNS["func"].match(text):
            buckets["func"].append(stmt)
        elif PHASE_PATTERNS["trigger"].match(text):
            buckets["trigger"].append(stmt)
        elif PHASE_PATTERNS["insert"].match(text):
            buckets["insert"].append(stmt)
        elif PHASE_PATTERNS["view"].match(text):
            buckets["view"].append(stmt)
        else:
            buckets["other"].append(stmt)
    return buckets

def run_statements(engine, statements, title):
    if not statements:
        return
    print(f"▶ {title}: {len(statements)} statements")
    with engine.begin() as conn:
        for i, stmt in enumerate(statements, start=1):
            try:
                conn.exec_driver_sql(stmt)
            except Exception as e:
                # 실패 문장을 로그로 보여주고 즉시 중단
                print(f"\n❌ 실패 ({title} #{i}):\n{stmt}\n")
                raise

def apply_schema(engine, sql: str):
    stmts = split_statements(sql)
    buckets = bucketize(stmts)

    # 실행 순서(안전한 Phase)
    run_statements(engine, buckets["ext"],        "Extensions")
    run_statements(engine, buckets["type"],       "Types")
    run_statements(engine, buckets["table_users"],"Tables(users first)")
    run_statements(engine, buckets["table"],      "Tables(others)")
    run_statements(engine, buckets["index"],      "Indexes")
    run_statements(engine, buckets["func"],       "Functions")
    run_statements(engine, buckets["trigger"],    "Triggers")
    run_statements(engine, buckets["insert"],     "Initial Data")
    run_statements(engine, buckets["view"],       "Views")
    run_statements(engine, buckets["other"],      "Other")

def main():
    db_url = get_db_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    schema_sql = load_schema_sql("schema.sql")
    apply_schema(engine, schema_sql)
    print("✅ schema.sql 적용 완료")

if __name__ == "__main__":
    main()
