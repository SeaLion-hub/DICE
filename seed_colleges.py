# seed_colleges.py
import os
from urllib.parse import urlsplit, unquote
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# .env 로드 (UTF-8)
load_dotenv(encoding="utf-8")

dsn = os.getenv("DATABASE_URL")
if not dsn:
    raise RuntimeError("DATABASE_URL not set")

# 1) URL → 파라미터 안전 파싱
u = urlsplit(dsn)
DB_HOST = u.hostname
DB_PORT = u.port or 5432
DB_NAME = u.path.lstrip("/")
DB_USER = unquote(u.username) if u.username else None
DB_PASSWORD = unquote(u.password) if u.password else None

# 2) colleges 메타 로드 (루트에 colleges.py)
from colleges import COLLEGES
rows = [(k, v["name"], v["url"], v["color"], v["icon"]) for k, v in COLLEGES.items()]

sql = """
INSERT INTO colleges(key, name, url, color, icon)
VALUES %s
ON CONFLICT (key) DO UPDATE
SET name=EXCLUDED.name, url=EXCLUDED.url, color=EXCLUDED.color, icon=EXCLUDED.icon;
"""

def main():
    # 3) 파라미터로 연결 (DSN 문자열 직접 전달 X)
    with psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    ) as conn, conn.cursor() as cur:
        execute_values(cur, sql, rows)
        conn.commit()
    print(f"✅ Seeded {len(rows)} colleges")

if __name__ == "__main__":
    # 문제 생기면 실제 값 확인용(민감정보 제외)
    # print(repr(dsn))  # 숨은 문자 디버깅에 도움
    main()
