# init_db.py
import os
from urllib.parse import urlsplit, unquote
import psycopg2
from dotenv import load_dotenv

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

sql_path = "000_init.sql"
with open(sql_path, "r", encoding="utf-8") as f:
    ddl = f.read()

with psycopg2.connect(**params) as conn, conn.cursor() as cur:
    cur.execute(ddl)
    conn.commit()

print("✅ 000_init.sql applied")
print("You can now run 'python seed_colleges.py' to seed initial data.")