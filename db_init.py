# db_init.py
import os
from sqlalchemy import create_engine, text

def get_db_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 환경변수가 없습니다.")
    # Railway Postgres는 SSL 필요
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
    # SQLAlchemy 2.0: exec_driver_sql로 멀티스테이트먼트 실행 가능(드라이버가 허용 시).
    # psycopg3 + PostgreSQL OK.
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)

def main():
    db_url = get_db_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    schema_sql = load_schema_sql("schema.sql")  # ← 네가 올린 schema.sql 그대로 사용
    apply_schema(engine, schema_sql)
    print("✅ schema.sql 적용 완료")

if __name__ == "__main__":
    main()
