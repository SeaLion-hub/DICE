# main.py — DICE API (Pydantic v2 호환 + /health 추가)
# Project root: backend/main.py 기준

import os
import json
import time
import threading
import logging
import hashlib
import re
from datetime import datetime
import datetime as dt
from typing import Optional, Any, Dict, List

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2 import OperationalError

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field, ConfigDict
import jwt  # 토큰 디코딩(필요 시)
import redis

# 외부(프로젝트 내부) 모듈들
# 이들 파일/함수는 네 프로젝트에 이미 존재한다고 가정
from majors import MAJORS_BY_COLLEGE  # 전공 메타데이터
from ai_processor import classify_notice_category, extract_structured_info
from comparison_logic import check_suitability  # 사용 중이면 남겨둠
from auth_routes import router as auth_router
from auth_deps import get_current_user  # 사용 위치가 있다면
from auth_security import decode_token   # 사용 위치가 있다면
from db_pool import init_pool, close_pool, get_conn

# ─────────────────────────────────────────────────────────────────────────────
# 환경 설정 / 로깅
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv(encoding="utf-8")

ENV = os.getenv("ENV", "dev")
DATABASE_URL = os.getenv("DATABASE_URL")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")]
HEALTH_REQUIRE_SEEDED = os.getenv("HEALTH_REQUIRE_SEEDED", "1")
APIFY_WEBHOOK_TOKEN = os.getenv("APIFY_WEBHOOK_TOKEN", "change-me")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
CACHE_TTL = int(os.getenv("CACHE_TTL", "60"))
AI_IN_PIPELINE = os.getenv("AI_IN_PIPELINE", "true").lower() == "true"

REDIS_URL = os.getenv("REDIS_URL")
QUEUE_NAME = os.getenv("QUEUE_NAME", "apify:dataset:jobs")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("dice-api")

# ─────────────────────────────────────────────────────────────────────────────
# Redis
# ─────────────────────────────────────────────────────────────────────────────

redis_client = None
if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        logging.info("Redis connected successfully")
    except Exception as e:
        logging.warning(f"Redis connection failed, will retry on demand: {e}")
        redis_client = None

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI 앱
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DICE API",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# DB 풀 초기화/정리
@app.on_event("startup")
async def startup_event():
    logger.info("Initializing database connection pool...")
    init_pool()

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Closing database connection pool...")
    close_pool()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 인증 라우터
app.include_router(auth_router, tags=["auth"])

# ─────────────────────────────────────────────────────────────────────────────
# Healthcheck
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    out = {"status": "ok", "checks": {}}

    # DB 체크
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        out["checks"]["db"] = "ok"
    except OperationalError as e:
        out["status"] = "degraded"
        out["checks"]["db"] = f"error: {e.__class__.__name__}"

    # Redis 체크(있을 때만)
    if redis_client:
        try:
            redis_client.ping()
            out["checks"]["redis"] = "ok"
        except Exception as e:
            out["status"] = "degraded"
            out["checks"]["redis"] = f"error: {e.__class__.__name__}"
    else:
        out["checks"]["redis"] = "not_configured"

    return out

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# ─────────────────────────────────────────────────────────────────────────────
# 메타데이터 API (전공 목록)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/meta/majors")
def get_majors_list():
    formatted_list = [
        {"college": college_name, "majors": major_list}
        for college_name, major_list in MAJORS_BY_COLLEGE.items()
    ]
    return {"items": formatted_list}

# ─────────────────────────────────────────────────────────────────────────────
# 간단 메모리 캐시
# ─────────────────────────────────────────────────────────────────────────────

_cache: Dict[str, Any] = {}
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

def cache_set(key: str, value: Any, ttl: Optional[int] = None):
    if ttl is None:
        ttl = CACHE_TTL
    expire_time = time.time() + ttl
    with _cache_lock:
        _cache[key] = (expire_time, value)

# ─────────────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────────────

def _to_utc_ts(date_yyyy_mm_dd: Optional[str]):
    if not date_yyyy_mm_dd:
        return None
    try:
        d = dt.date.fromisoformat(date_yyyy_mm_dd)
        return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Apify 웹훅 페이로드 모델(필요 시 사용)
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# AI 수동 재추출(Admin) — pydantic v2 호환 수정 반영
# ─────────────────────────────────────────────────────────────────────────────

class ManualExtractRequest(BaseModel):
    notice_id: str
    # pydantic v2: regex → pattern
    mode: str = Field(default="all", pattern="^(all|category|qualifications)$")

@app.post("/admin/re-extract-ai", tags=["admin"])
async def manual_reextract_ai(req: ManualExtractRequest):
    notice_id = req.notice_id
    mode = req.mode

    conn = None
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

            title = notice.get("title", "") or ""
            body = notice.get("body_text", "") or ""

            new_data: Dict[str, Any] = {}

            # 카테고리 재분류
            if mode in ("all", "category"):
                logger.info(f"Re-classifying category for {notice_id}...")
                category_ai = classify_notice_category(title=title, body=body)
                new_data["category_ai"] = category_ai
                new_data["hashtags_ai"] = [category_ai] if category_ai and category_ai != "#일반" else None
                logger.info(f"New category: {category_ai}")
            else:
                category_ai = notice.get("category_ai") or "#일반"

            # 요건/기간 재추출
            if mode in ("all", "qualifications"):
                logger.info(f"Re-extracting qualifications for {notice_id} (using category: {category_ai})...")
                structured_info = extract_structured_info(title=title, body=body, category=category_ai)
                if isinstance(structured_info, dict):
                    new_data["qualification_ai"] = Json(structured_info)
                    new_data["start_at_ai"] = _to_utc_ts(structured_info.get("start_date"))
                    new_data["end_at_ai"] = _to_utc_ts(structured_info.get("end_date"))
                else:
                    new_data["qualification_ai"] = Json({})
                    new_data["start_at_ai"] = None
                    new_data["end_at_ai"] = None

            if new_data:
                set_clauses = []
                params: Dict[str, Any] = {"id": notice_id}
                for key, value in new_data.items():
                    set_clauses.append(f"{key} = %({key})s")
                    params[key] = value
                set_sql = ", ".join(set_clauses)

                with conn.cursor(cursor_factory=RealDictCursor) as cur:
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

                # 캐시 무효화
                cache_set(f"notice_v1_{notice_id}", None, ttl=1)

                logger.info(f"Successfully re-extracted AI data for {notice_id}")
                return {"status": "success", "updated_data": updated_row}

            return {"status": "no_action", "message": "Invalid mode or no fields to update."}

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"DB error re-extracting AI for {notice_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"AI error re-extracting AI for {notice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"AI processing error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# 이 파일에 다른 엔드포인트들이 더 있다면 아래에 그대로 두면 됩니다.
# (/notices, /notices/{id}, /search 등)
# 본 파일은 /health 추가와 pydantic v2 호환을 위해 핵심 부분만 손봤습니다.
# ─────────────────────────────────────────────────────────────────────────────
