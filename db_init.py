# db_init.py
import os
import sqlparse
from sqlalchemy import create_engine, text

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

def apply_schema(engine, sql: str):
    """
    멀티 스테이트먼트를 안전하게 나눠서 순차 실행.
    함수/트리거/$$ 바디, CREATE TYPE, CREATE EXTENSION 등도 처리 가능.
    """
    statements = [s.strip() for s in sqlparse.split(sql) if s.strip()]
    with engine.begin() as conn:
        for i, stmt in enumerate(statements, start=1):
            # 주석/빈문 제외
            if stmt.startswith("--") or stmt == "":
                continue
            conn.exec_driver_sql(stmt)

def main():
    db_url = get_db_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    schema_sql = load_schema_sql("schema.sql")
    apply_schema(engine, schema_sql)
    print("✅ schema.sql 적용 완료")

if __name__ == "__main__":
    main()
