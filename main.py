# main.py (프로젝트 루트 / readiness healthcheck + webhook 버전)
import os
import logging
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from datetime import datetime
import requests
from typing import Optional

# 1) .env 로드 (로컬 실행용 / Railway에선 환경변수로 자동 주입)
load_dotenv(encoding="utf-8")

# 2) 환경변수
ENV = os.getenv("ENV", "dev")
DATABASE_URL = os.getenv("DATABASE_URL")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")]
HEALTH_REQUIRE_SEEDED = os.getenv("HEALTH_REQUIRE_SEEDED", "1")  # '1'이면 colleges 시드 완료까지 대기
APIFY_WEBHOOK_TOKEN = os.getenv("APIFY_WEBHOOK_TOKEN", "change-me")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")

# 3) colleges.py import
from colleges import COLLEGES

# 4) 로깅
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("dice-api")

# 5) FastAPI 앱
app = FastAPI(title="DICE API", version="0.1.0", docs_url="/docs", redoc_url="/redoc")

# 6) CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 7) 헬퍼: DB 연결 (요청마다 짧게 열고 닫는 패턴)
def query_all(sql: str, params=None):
    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()

# 8) Crawler 헬퍼 함수들
def normalize_item(item: dict, base_url: str = None) -> dict:
    """Apify 아이템을 정규화"""
    normalized = {
        "title": item.get("title", "").strip(),
        "url": item.get("url", ""),
        "summary_raw": item.get("summary", "").strip() if item.get("summary") else None,
        "body_html": item.get("body_html", "").strip() if item.get("body_html") else None,
        "body_text": item.get("body_text", "").strip() if item.get("body_text") else None,
        "published_at": None
    }
    
    # URL이 상대 경로인 경우 절대 경로로 변환
    if normalized["url"] and not normalized["url"].startswith(("http://", "https://")):
        if base_url:
            normalized["url"] = base_url.rstrip("/") + "/" + normalized["url"].lstrip("/")
    
    # 날짜 파싱 (필요시 형식 조정)
    if item.get("published_at"):
        try:
            normalized["published_at"] = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        except:
            pass
    elif item.get("date"):
        try:
            normalized["published_at"] = datetime.strptime(item["date"], "%Y-%m-%d")
        except:
            pass
    
    return normalized

def validate_normalized_item(item: dict) -> bool:
    """정규화된 아이템이 유효한지 검증"""
    if not item.get("title") or not item.get("url"):
        return False
    if not item["url"].startswith(("http://", "https://")):
        return False
    return True

def content_hash(college_key: str, title: str, url: str, published_at: Optional[datetime]) -> str:
    """중복 방지를 위한 해시 생성"""
    date_str = published_at.isoformat() if published_at else "no-date"
    content = f"{college_key}|{title}|{url}|{date_str}"
    return hashlib.sha256(content.encode()).hexdigest()

# UPSERT SQL
UPSERT_SQL = """
    INSERT INTO notices (
        college_key, title, url, summary_raw, body_html, body_text, 
        published_at, source_site, content_hash
    ) VALUES (
        %(college_key)s, %(title)s, %(url)s, %(summary_raw)s, 
        %(body_html)s, %(body_text)s, %(published_at)s, 
        %(source_site)s, %(content_hash)s
    )
    ON CONFLICT (content_hash) 
    DO UPDATE SET
        summary_raw = EXCLUDED.summary_raw,
        body_html = EXCLUDED.body_html,
        body_text = EXCLUDED.body_text,
        updated_at = CURRENT_TIMESTAMP
"""

# 9) 라우트들
@app.get("/health")
def health():
    base = {"env": ENV, "service": "dice-api"}
    # 1) DB 연결 확인
    try:
        query_all("SELECT 1 AS ok;")
    except Exception as e:
        raise HTTPException(status_code=503, detail={"status": "db_unavailable", **base})

    # 2) 마이그레이션/시드 준비 상태 확인
    try:
        tbl = query_all("SELECT COUNT(*) AS c FROM information_schema.tables WHERE table_name = 'colleges';")
        have_colleges = tbl and tbl[0]["c"] == 1
        if not have_colleges:
            raise HTTPException(status_code=503, detail={"status": "migrations_pending", **base})

        if HEALTH_REQUIRE_SEEDED == "1":
            seeded = query_all("SELECT COUNT(*) AS c FROM colleges;")[0]["c"] > 0
            if not seeded:
                raise HTTPException(status_code=503, detail={"status": "seeding_pending", **base})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail={"status": "health_check_error", **base})

    return {"status": "ok", **base}

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
        where.append("college_key = %s")
        params.append(college)
    if q:
        where.append("(title ILIKE %s OR summary_raw ILIKE %s OR summary_ai ILIKE %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if date_from:
        where.append("published_at >= %s")
        params.append(datetime.fromisoformat(date_from))
    if date_to:
        where.append("published_at < %s")
        params.append(datetime.fromisoformat(date_to))

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

@app.post("/apify/webhook")
def apify_webhook(token: str = Query(...), payload: dict = Body(...)):
    """Apify webhook 엔드포인트 - 크롤링 완료 시 호출됨"""
    # 1) 보안 토큰 확인
    if token != APIFY_WEBHOOK_TOKEN:
        logger.warning(f"Invalid webhook token attempt")
        raise HTTPException(status_code=401, detail="invalid token")
    
    logger.info(f"Webhook received: {payload.get('eventType', 'unknown')}")
    
    # 2) datasetId / taskId 추출 (Apify RUN.SUCCEEDED payload 대비)
    ds_id = (
        payload.get("defaultDatasetId")
        or payload.get("data", {}).get("defaultDatasetId")
        or payload.get("resource", {}).get("defaultDatasetId")
    )
    task_id = (
        payload.get("data", {}).get("actorTaskId")
        or payload.get("resource", {}).get("actorTaskId")
    )
    
    if not ds_id:
        logger.error("Dataset ID missing in webhook payload")
        raise HTTPException(status_code=400, detail="datasetId missing")
    
    logger.info(f"Processing dataset: {ds_id}, task: {task_id}")
    
    # 3) 데이터셋 아이템 가져오기
    url = f"https://api.apify.com/v2/datasets/{ds_id}/items"
    params = {"token": APIFY_TOKEN, "format": "json", "clean": "true"}
    
    try:
        r = requests.get(url, params=params, timeout=60)
        if r.status_code != 200:
            logger.error(f"Apify API error: {r.status_code}")
            raise HTTPException(status_code=502, detail=f"apify fetch error: {r.text[:200]}")
        items = r.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch dataset: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch dataset")
    
    if not isinstance(items, list):
        items = items.get("items", [])
    
    logger.info(f"Fetched {len(items)} items from dataset")
    
    # 4) 어떤 단과대 task인지 매핑
    college_key = None
    if task_id:
        for ck, meta in COLLEGES.items():
            if meta.get("task_id") == task_id:
                college_key = ck
                break
    
    if not college_key:
        college_key = "main"  # fallback
        logger.warning(f"Unknown task_id {task_id}, using 'main' as default")
    
    logger.info(f"Processing for college: {college_key}")
    
    # 5) upsert
    upserted, skipped = 0, 0
    try:
        with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            for rec in items:
                norm = normalize_item(rec, base_url=COLLEGES[college_key].get("url"))
                if not validate_normalized_item(norm):
                    skipped += 1
                    continue
                
                h = content_hash(college_key, norm["title"], norm["url"], norm["published_at"])
                
                try:
                    cur.execute(UPSERT_SQL, {
                        "college_key": college_key,
                        "title": norm["title"],
                        "url": norm["url"],
                        "summary_raw": norm.get("summary_raw"),
                        "body_html": norm.get("body_html"),
                        "body_text": norm.get("body_text"),
                        "published_at": norm.get("published_at"),
                        "source_site": COLLEGES[college_key].get("url"),
                        "content_hash": h
                    })
                    upserted += 1
                except psycopg2.Error as e:
                    logger.error(f"Failed to upsert item: {e}")
                    skipped += 1
            
            conn.commit()
            logger.info(f"Webhook processing complete: {upserted} upserted, {skipped} skipped")
    
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    
    return {
        "status": "ok",
        "college": college_key,
        "upserted": upserted,
        "skipped": skipped,
        "total_items": len(items)
    }