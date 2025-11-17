import os
from urllib.parse import urlsplit, unquote

import psycopg2
from dotenv import load_dotenv

# .env에서 DATABASE_URL 읽어오기
load_dotenv(encoding="utf-8")

dsn = os.getenv("DATABASE_URL")
if not dsn:
    raise RuntimeError("DATABASE_URL not set")

u = urlsplit(dsn)
params = dict(
    host=u.hostname,
    port=u.port or 5432,
    dbname=u.path.lstrip("/"),
    user=unquote(u.username) if u.username else None,
    password=unquote(u.password) if u.password else None,
)

# 017_notification_settings.sql 읽어서 실행
with open("017_notification_settings.sql", "r", encoding="utf-8") as f:
    ddl = f.read()

with psycopg2.connect(**params) as conn, conn.cursor() as cur:
    cur.execute(ddl)
    conn.commit()

print("✅ 017_notification_settings.sql applied")
