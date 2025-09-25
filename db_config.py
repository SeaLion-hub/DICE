import os
import psycopg2
from psycopg2.extras import RealDictCursor

# Railway는 DATABASE_URL을 자동 주입함.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Heroku/옛 문자열 호환
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def run_sql(cursor, sql_text: str):
    # 여러 스테이트먼트를 한 번에 실행
    cursor.execute(sql_text)

def init_database():
    """schema.sql 실행 (idempotent)"""
    conn = get_db_connection()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # 확장 설치는 권한 없으면 무시되게 try/except 처리
        try:
            cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
            cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
        except Exception as ext_err:
            print(f"[WARN] extension create skipped: {ext_err}")

        with open('schema.sql', 'r', encoding='utf-8') as f:
            sql_text = f.read()
        run_sql(cur, sql_text)

        conn.commit()
        print("Database initialized successfully!")
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] DB init failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()
