# main.py (프로젝트 루트)
import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from fastapi import HTTPException
from datetime import datetime

# 1) .env 로드 (로컬 실행용 / Railway에선 환경변수로 자동 주입)
load_dotenv(encoding="utf-8")

# 2) 환경변수
ENV = os.getenv("ENV", "dev")
DATABASE_URL = os.getenv("DATABASE_URL")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")]

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")

# 3) 로깅
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("dice-api")

# 4) FastAPI 앱
app = FastAPI(title="DICE API", version="0.1.0", docs_url="/docs", redoc_url="/redoc")

# 5) CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 6) 헬퍼: DB 연결 (요청마다 짧게 열고 닫는 패턴)
def query_all(sql: str, params=None):
    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()

# 7) 라우트들
@app.get("/health")
def health():
    return {"status": "ok", "env": ENV, "service": "dice-api"}

@app.get("/notices")
def list_notices(
    college: str | None = Query(None),
    q: str | None = Query(None),
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    where, params = [], []
    if college:
        where.append("college_key = %s"); params.append(college)
    if q:
        where.append("(title ILIKE %s OR summary_raw ILIKE %s OR summary_ai ILIKE %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if date_from:
        where.append("published_at >= %s"); params.append(datetime.fromisoformat(date_from))
    if date_to:
        where.append("published_at < %s"); params.append(datetime.fromisoformat(date_to))

    sql = """
      SELECT id, college_key, title, url, summary_ai, summary_raw, published_at, created_at
      FROM notices
    """
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY published_at DESC NULLS LAST, created_at DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
    rows = query_all(sql, params)
    return {"items": rows, "limit": limit, "offset": offset}

    where, params = [], []
    if college:
        where.append("college_key = %s"); params.append(college)
    if q:
        where.append("(title ILIKE %s OR summary_raw ILIKE %s OR summary_ai ILIKE %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]

    sql = """
      SELECT id, college_key, title, url, summary_ai, summary_raw, published_at, created_at
      FROM notices
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY published_at DESC NULLS LAST, created_at DESC LIMIT %s OFFSET %s"
    params += [limit, offset]

    rows = query_all(sql, params)
    return {"items": rows, "limit": limit, "offset": offset}

@app.get("/stats")
def stats():
    by_college = query_all("""
        SELECT college_key, COUNT(*) AS cnt
        FROM notices
        GROUP BY college_key
        ORDER BY cnt DESC
    """)
    total = query_all("SELECT COUNT(*) AS total FROM notices;")[0]["total"]
    return {"total": total, "by_college": by_college}

@app.get("/colleges")
def list_colleges():
    rows = query_all("""
      SELECT key AS college_key, name, url, color, icon
      FROM colleges ORDER BY name
    """)
    return {"items": rows}



@app.get("/notices/{notice_id}")
def get_notice(notice_id: str):
    rows = query_all("""
      SELECT id, college_key, title, url, summary_ai, summary_raw, body_html, body_text,
             published_at, created_at, updated_at
      FROM notices WHERE id = %s
    """, [notice_id])
    if not rows:
        raise HTTPException(status_code=404, detail="notice not found")
    return rows[0]

