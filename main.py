# main.py (프로젝트 루트 / DB풀 + 인증 + AI + FTS + majors API 통합 버전)
import os
import logging
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from fastapi import FastAPI, Query, HTTPException, Body, Request, Header, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from datetime import datetime
import datetime as dt
import requests
from typing import Optional, Any, Dict, List
import time
import threading
import json
import re
from pydantic import BaseModel, Field, ConfigDict
import jwt
import redis

# [신규] 전공 메타데이터 import
from majors import MAJORS_BY_COLLEGE

# AI processor import (현재 파일셋 기준)
from ai_processor import (
    classify_notice_category,
    extract_structured_info,
)

# [신규] 적합도 검증 로직 import
from comparison_logic import check_suitability

# 인증 라우터 및 의존성 import
from auth_routes import router as auth_router
from auth_deps import get_current_user
from auth_security import decode_token

# DB Pool import (신규 표준)
from db_pool import init_pool, close_pool, get_conn

# 1) .env 로드
load_dotenv(encoding="utf-8")

# 2) 환경변수
ENV = os.getenv("ENV", "dev")
DATABASE_URL = os.getenv("DATABASE_URL")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")]
HEALTH_REQUIRE_SEEDED = os.getenv("HEALTH_REQUIRE_SEEDED", "1")
APIFY_WEBHOOK_TOKEN = os.getenv("APIFY_WEBHOOK_TOKEN", "change-me")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
CACHE_TTL = int(os.getenv("CACHE_TTL", "60"))
AI_IN_PIPELINE = os.getenv("AI_IN_PIPELINE", "true").lower() == "true"

# Redis 관련 환경변수
REDIS_URL = os.getenv("REDIS_URL")
QUEUE_NAME = os.getenv("QUEUE_NAME", "apify:dataset:jobs")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")

# Redis 클라이언트 초기화
redis_client = None
if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        logging.info("Redis connected successfully")
    except Exception as e:
        logging.warning(f"Redis connection failed, will retry on demand: {e}")
        redis_client = None

# 3) colleges.py import
from colleges import COLLEGES

# 4) 로깅
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("dice-api")

# 5) FastAPI 앱
app = FastAPI(title="DICE API", version="0.2.0 (Schema-Aligned)", docs_url="/docs", redoc_url="/redoc")

# --- [수정] DB 풀 이벤트 핸들러 ---
@app.on_event("startup")
async def startup_event():
    logger.info("Initializing database connection pool...")
    init_pool()

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Closing database connection pool...")
    close_pool()

# 6) CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 7) 인증 라우터 등록
app.include_router(auth_router, tags=["auth"])

# --- (신규) 메타데이터 API 추가 ---
@app.get("/meta/majors")
def get_majors_list():
    formatted_list = [
        {"college": college_name, "majors": major_list}
        for college_name, major_list in MAJORS_BY_COLLEGE.items()
    ]
    return {"items": formatted_list}
# --- (신규) 메타데이터 API 추가 끝 ---

# 캐시 시스템
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key: str) -> Any:
    with _cache_lock:
        if key in _cache:
            expire_time, value = _cache[key]
            if time.time() < expire_time:
                return value
            else:
                del _cache[key]
        return None

def cache_set(key: str, value: Any, ttl: int = None):
    if ttl is None:
        ttl = CACHE_TTL
    expire_time = time.time() + ttl
    with _cache_lock:
        _cache[key] = (expire_time, value)

# 헬퍼 함수
def _to_utc_ts(date_yyyy_mm_dd: str | None):
    if not date_yyyy_mm_dd:
        return None
    try:
        d = dt.date.fromisoformat(date_yyyy_mm_dd)
        return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        return None

# 11) Apify Webhook Models
class ApifyResource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    defaultDatasetId: Optional[str] = None
    status: Optional[str] = None
    actorTaskId: Optional[str] = None

class ApifyWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    eventType: str = Field(...)
    resource: Optional[ApifyResource] = None

# ... (중략 — 중간의 /health, /notices 등 기존 코드 동일) ...

# --- (신규) AI 수동 재추출 엔드포인트 ---
class ManualExtractRequest(BaseModel):
    notice_id: str
    mode: str = Field("all", pattern="^(all|category|qualifications)$")  # ← regex → pattern 수정됨

@app.post("/admin/re-extract-ai", tags=["admin"])
async def manual_reextract_ai(req: ManualExtractRequest):
    notice_id = req.notice_id
    mode = req.mode

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, title, body_text, category_ai FROM notices WHERE id = %s",
                    (notice_id,)
                )
                notice = cur.fetchone()
                if not notice:
                    raise HTTPException(status_code=404, detail="Notice not found")

            title = notice.get("title", "")
            body = notice.get("body_text", "")

            new_data = {}
            if mode == "all" or mode == "category":
                logger.info(f"Re-classifying category for {notice_id}...")
                category_ai = classify_notice_category(title=title, body=body)
                new_data["category_ai"] = category_ai
                new_data["hashtags_ai"] = [category_ai] if category_ai and category_ai != "#일반" else None
                logger.info(f"New category: {category_ai}")
            else:
                category_ai = notice.get("category_ai") or "#일반"

            if mode == "all" or mode == "qualifications":
                logger.info(f"Re-extracting qualifications for {notice_id} (using category: {category_ai})...")
                structured_info = extract_structured_info(title=title, body=body, category=category_ai)
                new_data["qualification_ai"] = Json(structured_info if isinstance(structured_info, dict) else {})
                new_data["start_at_ai"] = _to_utc_ts(structured_info.get("start_date"))
                new_data["end_at_ai"] = _to_utc_ts(structured_info.get("end_date"))

            if new_data:
                set_clauses = []
                params = {"id": notice_id}
                for key, value in new_data.items():
                    set_clauses.append(f"{key} = %({key})s")
                    params[key] = value
                set_sql = ", ".join(set_clauses)

                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE notices
                        SET {set_sql},
                            updated_at = CURRENT_TIMESTAMP,
                            title = title 
                        WHERE id = %(id)s
                        RETURNING id, category_ai, hashtags_ai, qualification_ai, start_at_ai, end_at_ai
                        """,
                        params
                    )
                    updated_row = cur.fetchone()
                    conn.commit()
                    cache_set(f"notice_v1_{notice_id}", None, ttl=1)
                    logger.info(f"Successfully re-extracted AI data for {notice_id}")
                    return {"status": "success", "updated_data": updated_row}
            else:
                return {"status": "no_action", "message": "Invalid mode"}

    except psycopg2.Error as e:
        if conn: conn.rollback()
        logger.error(f"DB error re-extracting AI for {notice_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"AI error re-extracting AI for {notice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"AI processing error: {e}")
