import os, psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv(encoding="utf-8")
url = os.getenv("DATABASE_URL")

with psycopg2.connect(url) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
    cur.execute("SELECT college_key, title, published_at FROM notices ORDER BY published_at DESC LIMIT 10;")
    for row in cur.fetchall():
        print(row)
