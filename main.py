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
    """
    프론트엔드 프로필 설정용으로, 단과대학별 표준 전공 목록을 반환합니다.
    데이터 소스: majors.py (MAJORS_BY_COLLEGE 딕셔너리)
    """
    formatted_list = [
        {"college": college_name, "majors": major_list}
        for college_name, major_list in MAJORS_BY_COLLEGE.items()
    ]
    return {"items": formatted_list}
# --- (신규) 메타데이터 API 추가 끝 ---

# 8) 캐시 시스템
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key: str) -> Any:
    """만료되지 않은 캐시 값 반환, 없거나 만료 시 None"""
    with _cache_lock:
        if key in _cache:
            expire_time, value = _cache[key]
            if time.time() < expire_time:
                logger.debug(f"Cache hit for key: {key}")
                return value
            else:
                del _cache[key]
                logger.debug(f"Cache expired for key: {key}")
        return None

def cache_set(key: str, value: Any, ttl: int = None):
    """캐시 저장 (기본 TTL은 CACHE_TTL 환경변수)"""
    if ttl is None:
        ttl = CACHE_TTL
    expire_time = time.time() + ttl
    with _cache_lock:
        _cache[key] = (expire_time, value)
        logger.debug(f"Cache set for key: {key}, TTL: {ttl}s")

# 9) AI 헬퍼 함수
def _to_utc_ts(date_yyyy_mm_dd: str | None):
    """'YYYY-MM-DD' -> aware UTC midnight; None 유지 (방어적 파싱)"""
    if not date_yyyy_mm_dd:
        return None
    try:
        d = dt.date.fromisoformat(date_yyyy_mm_dd)
        return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        logger.warning(f"Invalid date format: {date_yyyy_mm_dd}. Returning None.")
        return None

# 10) Crawler 헬퍼 함수들
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
    
    if normalized["url"] and not normalized["url"].startswith(("http://", "https://")):
        if base_url:
            normalized["url"] = base_url.rstrip("/") + "/" + normalized["url"].lstrip("/")
    
    if item.get("published_at"):
        try:
            normalized["published_at"] = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        except: pass
    elif item.get("date"):
        try:
            d = dt.date.fromisoformat(item["date"])
            normalized["published_at"] = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
        except: pass
    
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

# [수정] UPSERT SQL (summary_ai 제거, search_vector 사용)
UPSERT_SQL = """
    INSERT INTO notices (
        college_key, title, url, summary_raw, body_html, body_text,
        published_at, source_site, content_hash,
        category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai,
        search_vector
    ) VALUES (
        %(college_key)s, %(title)s, %(url)s, %(summary_raw)s,
        %(body_html)s, %(body_text)s, %(published_at)s,
        %(source_site)s, %(content_hash)s,
        %(category_ai)s, %(start_at_ai)s, %(end_at_ai)s, %(qualification_ai)s, %(hashtags_ai)s,
        setweight(to_tsvector('simple', coalesce(%(title)s, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(array_to_string(%(hashtags_ai)s, ' '), '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(%(body_text)s, '')), 'C')
    )
    ON CONFLICT (content_hash)
    DO UPDATE SET
        title = EXCLUDED.title,
        url = EXCLUDED.url,
        summary_raw = EXCLUDED.summary_raw,
        body_html = EXCLUDED.body_html,
        body_text = EXCLUDED.body_text,
        published_at = EXCLUDED.published_at,
        category_ai = EXCLUDED.category_ai,
        start_at_ai = EXCLUDED.start_at_ai,
        end_at_ai = EXCLUDED.end_at_ai,
        qualification_ai = EXCLUDED.qualification_ai,
        hashtags_ai = EXCLUDED.hashtags_ai,
        updated_at = CURRENT_TIMESTAMP,
        search_vector = setweight(to_tsvector('simple', coalesce(EXCLUDED.title, '')), 'A') ||
                        setweight(to_tsvector('simple', coalesce(array_to_string(EXCLUDED.hashtags_ai, ' '), '')), 'B') ||
                        setweight(to_tsvector('simple', coalesce(EXCLUDED.body_text, '')), 'C')
"""

# 11) Apify Webhook Models
class ApifyResource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    defaultDatasetId: Optional[str] = None
    status: Optional[str] = None
    actorTaskId: Optional[str] = None # [수정] taskId 추가

class ApifyWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    eventType: str = Field(...)
    resource: Optional[ApifyResource] = None

# 12) 라우트들
@app.get("/health")
def health():
    base = {"env": ENV, "service": "dice-api"}
    try:
        # [수정] DB 연결 풀 사용
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT 1 AS ok;")
            
            tbl = cur.fetchone()
            if not tbl or tbl["ok"] != 1:
                 raise Exception("DB query failed")

            cur.execute("SELECT COUNT(*) AS c FROM information_schema.tables WHERE table_name = 'colleges';")
            tbl_check = cur.fetchone()
            have_colleges = tbl_check and tbl_check["c"] == 1
            if not have_colleges:
                raise HTTPException(status_code=503, detail={"status": "migrations_pending", **base})

            if HEALTH_REQUIRE_SEEDED == "1":
                cur.execute("SELECT COUNT(*) AS c FROM colleges;")
                seeded_result = cur.fetchone()
                seeded = seeded_result["c"] > 0 if seeded_result else False
                if not seeded:
                    raise HTTPException(status_code=503, detail={"status": "seeding_pending", **base})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Health check DB error: {e}")
        raise HTTPException(status_code=503, detail={"status": "db_unavailable", **base})

    return {"status": "ok", **base}

@app.get("/notices")
def list_notices(
    request: Request,
    college: str | None = Query(None),
    q: str | None = Query(None),
    search_mode: str = Query("websearch", regex="^(like|trgm|fts|websearch)$", description="검색 모드: like|trgm|fts|websearch"),
    op: str = Query("and", regex="^(and|or)$", description="키워드 결합 방식: and|or (fts 모드 전용)"),
    rank: str | None = Query(None, regex="^(off|trgm|fts)$", description="랭킹 모드: off|trgm|fts (기본값: fts/websearch 모드일 때 fts)"),
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    sort: str = Query("recent", regex="^(recent|oldest)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    my: bool = Query(False, description="내 키워드와 일치하는 공지만 보기 (인증 필요)"),
    count: bool = Query(True, description="전체 카운트 포함 여부 (false로 설정 시 성능 향상)"),
    no_cache: bool = Query(False, description="캐시 무시 (디버그용)"),
):
    """
    공지사항 목록 조회 - 고급 검색 기능 포함
    [수정] FTS 컬럼명을 `search_vector`로, trgm 대상 컬럼을 `body_text`로 변경
    """
    
    if rank is None:
        rank = "fts" if search_mode in ("fts", "websearch") else "off"
    
    # 캐시 키 생성 (my=true는 캐시 안 함)
    cache_key = None
    if not no_cache and not my:
        cache_key_parts = [
            f"notices:v2", f"c={college or 'all'}", f"q={q or ''}", f"sm={search_mode}",
            f"op={op}", f"r={rank}", f"df={date_from or ''}", f"dt={date_to or ''}",
            f"s={sort}", f"l={limit}", f"o={offset}", f"cnt={count}"
        ]
        cache_key = ":".join(cache_key_parts)
        cached_response = cache_get(cache_key)
        if cached_response is not None:
            logger.info(f"Cache hit for notices query: {cache_key[:50]}...")
            return cached_response
    
    user_keywords = None
    if my:
        try:
            # [수정] auth_deps.py의 get_current_user를 직접 호출하는 대신
            # 헤더를 수동으로 파싱하고 DB에서 프로필을 조회합니다. (API 라우트 내에서 의존성 주입은 복잡함)
            auth = request.headers.get("Authorization", "")
            parts = auth.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]
                payload = decode_token(token)
                user_id = payload.get("sub")
                if not user_id: raise HTTPException(status_code=401, detail="Invalid token")
            else:
                raise HTTPException(status_code=401, detail="Not authenticated")
            
            with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT keywords FROM user_profiles WHERE user_id = %s", [user_id])
                profile_row = cur.fetchone()
                user_keywords = profile_row['keywords'] if profile_row else []
            
            if not user_keywords: # 키워드 없으면 빈 결과 반환
                 return {"meta": {"returned": 0, "total_count": 0}, "items": []}

        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, HTTPException) as e:
            raise HTTPException(status_code=401, detail=str(e.detail if hasattr(e, 'detail') else e))
        except Exception as e:
            logger.error(f"DB error fetching user keywords: {e}")
            raise HTTPException(status_code=500, detail="Database error fetching profile")

    # 검색어 토큰화
    tokens = []
    q_raw = ""
    if q:
        q_raw = q.strip()
        if q_raw:
            tokens = [t for t in re.split(r'\s+', q_raw) if len(t) >= 2] # 2글자 이상

    where_clauses = []
    order_clauses = []
    params = {}
    select_extra = []
    
    if college and college != "all":
        where_clauses.append("college_key = %(college)s")
        params["college"] = college
    
    if date_from:
        try: params["date_from"] = datetime.fromisoformat(date_from)
        except: raise HTTPException(status_code=400, detail="Invalid date_from format")
        where_clauses.append("published_at >= %(date_from)s")
    
    if date_to:
        try: params["date_to"] = datetime.fromisoformat(date_to) + dt.timedelta(days=1)
        except: raise HTTPException(status_code=400, detail="Invalid date_to format")
        where_clauses.append("published_at < %(date_to)s")
    
    if my and user_keywords:
        where_clauses.append("(hashtags_ai && %(user_keywords)s::text[])")
        params["user_keywords"] = user_keywords
    
    # [수정] 검색 로직 (search_vector 사용)
    if tokens:
        if search_mode in ("like", "trgm"):
            token_conditions = []
            for i, token in enumerate(tokens):
                param_name = f"token_{i}"
                params[param_name] = f"%{token}%"
                # [수정] summary_ai 대신 body_text 사용
                token_conditions.append(f"(title ILIKE %({param_name})s OR body_text ILIKE %({param_name})s)")
            
            op_join = " AND " if op == "and" else " OR "
            where_clauses.append("(" + op_join.join(token_conditions) + ")")
            
            if rank == "trgm":
                params["q_raw"] = q_raw
                # [수정] summary_ai 대신 body_text 사용
                select_extra.append("GREATEST(similarity(title, %(q_raw)s), similarity(body_text, %(q_raw)s)) AS rank_score")
                order_clauses.append("rank_score DESC")
                
        elif search_mode == "fts":
            if op == "and": tsquery_str = " & ".join(tokens)
            else: tsquery_str = " | ".join(tokens)
            
            tsquery_str = re.sub(r'[^0-9A-Za-z가-힣\|\&\s]', '', tsquery_str) # 간단한 정제
            if tsquery_str:
                params["tsquery"] = tsquery_str
                # [수정] ts_ko -> search_vector
                where_clauses.append("search_vector @@ to_tsquery('simple', %(tsquery)s)")
                if rank == "fts":
                    select_extra.append("ts_rank(search_vector, to_tsquery('simple', %(tsquery)s)) AS rank_score")
                    order_clauses.append("rank_score DESC")

        elif search_mode == "websearch":
            if q_raw:
                params["websearch_query"] = q_raw
                try:
                    # [수정] ts_ko -> search_vector
                    where_clauses.append("search_vector @@ websearch_to_tsquery('simple', %(websearch_query)s)")
                    if rank == "fts":
                        select_extra.append("ts_rank(search_vector, websearch_to_tsquery('simple', %(websearch_query)s)) AS rank_score")
                        order_clauses.append("rank_score DESC")
                except psycopg2.Error as e: # websearch_to_tsquery 실패 시 (오래된 PG)
                    logger.warning(f"websearch_to_tsquery failed, falling back to plainto_tsquery: {e}")
                    params["fallback_query"] = q_raw
                    where_clauses.append("search_vector @@ plainto_tsquery('simple', %(fallback_query)s)")
                    if rank == "fts":
                        select_extra.append("ts_rank(search_vector, plainto_tsquery('simple', %(fallback_query)s)) AS rank_score")
                        order_clauses.append("rank_score DESC")

    # 기본 정렬
    if sort == "oldest":
        order_clauses.append("published_at ASC NULLS FIRST, created_at ASC")
    else:
        order_clauses.append("published_at DESC NULLS LAST, created_at DESC")
    
    # SQL 구성
    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    order_sql = " ORDER BY " + ", ".join(order_clauses) if order_clauses else ""
    select_extra_sql = ", " + ", ".join(select_extra) if select_extra else ""
    
    total_count = None
    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # COUNT 쿼리 (옵션)
            if count:
                count_sql = f"SELECT COUNT(*) AS total FROM notices{where_sql}"
                cur.execute(count_sql, params)
                total_count = cur.fetchone()["total"]
            
            # SELECT 쿼리
            params["limit"] = min(limit, 100)
            params["offset"] = offset
            
            sql = f"""
                SELECT 
                    id, college_key, title, url, summary_raw,
                    category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai,
                    published_at, created_at
                    {select_extra_sql}
                FROM notices
                {where_sql}
                {order_sql}
                LIMIT %(limit)s OFFSET %(offset)s
            """
            
            cur.execute(sql, params)
            rows = cur.fetchall()

    except Exception as e:
        logger.error(f"DB error searching notices: {e}")
        raise HTTPException(status_code=500, detail="Database error executing search")

    # 응답 구성
    response = {
        "meta": {
            "search_mode": search_mode, "op": op, "rank": rank,
            "limit": params["limit"], "offset": offset, "returned": len(rows)
        },
        "items": rows
    }
    
    if count and total_count is not None:
        response["meta"]["total_count"] = total_count
    
    # 캐시 저장
    if cache_key:
        cache_ttl = 60 if q else 30
        cache_set(cache_key, response, ttl=cache_ttl)
        logger.info(f"Cached notices response for {cache_ttl}s: {cache_key[:50]}...")
    
    return response

@app.get("/stats")
def stats():
    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT college_key, COUNT(*) AS cnt
                FROM notices GROUP BY college_key ORDER BY cnt DESC
            """)
            by_college = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS total FROM notices;")
            total = cur.fetchone()["total"]
    except Exception as e:
        logger.error(f"DB error fetching stats: {e}")
        raise HTTPException(status_code=500, detail="Database error fetching stats")
    return {"total": total, "by_college": by_college}

@app.get("/colleges")
def list_colleges():
    cache_key = "colleges_list_v1"
    cached_data = cache_get(cache_key)
    if cached_data: return cached_data
    
    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT key AS college_key, name, url, color, icon FROM colleges ORDER BY name")
            rows = cur.fetchall()
        result = {"items": rows}
        cache_set(cache_key, result, ttl=3600) # 1시간 캐시
        return result
    except Exception as e:
        logger.error(f"DB error fetching colleges: {e}")
        raise HTTPException(status_code=500, detail="Database error fetching colleges")

@app.get("/notices/{notice_id}")
def get_notice(notice_id: str):
    cache_key = f"notice_v1_{notice_id}"
    cached_data = cache_get(cache_key)
    if cached_data: return cached_data

    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
              SELECT id, college_key, title, url, summary_raw, body_html, body_text,
                     category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai,
                     published_at, created_at, updated_at
              FROM notices WHERE id = %s
            """, [notice_id])
            notice = cur.fetchone()
    except Exception as e:
        logger.error(f"DB error fetching notice {notice_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error fetching notice")

    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    # [수정] summary_ai 대신 summary_raw 기본값 처리
    if notice.get("summary_raw") is None:
        notice["summary_raw"] = ""
    
    cache_set(cache_key, notice)
    return notice

@app.post("/apify/webhook")
def apify_webhook_redis(
    request: Request,
    payload: ApifyWebhookPayload = Body(...)
):
    """
    Apify webhook endpoint - Redis Queue version
    """
    token = request.headers.get("x-apify-token")
    if not APIFY_WEBHOOK_TOKEN or token != APIFY_WEBHOOK_TOKEN:
        logger.warning(f"[apify] Invalid webhook token attempt")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    resource = payload.resource
    
    logger.info(
        "[apify] received event=%s run=%s dataset=%s task=%s",
        payload.eventType,
        resource.id if resource else None,
        resource.defaultDatasetId if resource else None,
        resource.actorTaskId if resource else None # taskId 로깅
    )
    
    if payload.eventType != "ACTOR.RUN.SUCCEEDED":
        return {"ok": True, "queued": False, "reason": "ignored eventType"}
    
    dataset_id = resource.defaultDatasetId if resource else None
    if not dataset_id:
        raise HTTPException(status_code=400, detail="defaultDatasetId missing")
    
    # [수정] 작업 큐에 actorTaskId도 포함
    job = {
        "dataset_id": dataset_id,
        "source": "apify",
        "run_id": resource.id if resource else None,
        "actor_task_id": resource.actorTaskId if resource else None, # taskId 추가
        "created_at": datetime.utcnow().isoformat()
    }
    
    global redis_client
    if not redis_client:
        if not REDIS_URL:
            logger.error("[apify] REDIS_URL not configured")
            raise HTTPException(status_code=500, detail="Redis not configured")
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            redis_client.ping()
            logger.info("[apify] Redis reconnected")
        except Exception as e:
            logger.error(f"[apify] Redis connection failed: {e}")
            raise HTTPException(status_code=500, detail="enqueue failed")
    
    try:
        redis_client.rpush(QUEUE_NAME, json.dumps(job))
        logger.info(
            "[apify] enqueued dataset=%s run=%s task=%s queue=%s",
            job["dataset_id"], job["run_id"], job["actor_task_id"], QUEUE_NAME
        )
        return {"ok": True, "dataset_id": dataset_id, "queued": True}
    except Exception as e:
        logger.exception("[apify] Redis enqueue failed")
        raise HTTPException(status_code=500, detail="enqueue failed")

# [수정] 실시간 자격검증 엔드포인트
@app.get("/notices/{notice_id}/verify-eligibility", tags=["auth"])
def verify_eligibility_endpoint(
    notice_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    [인증 필요] 현재 로그인된 사용자의 프로필을 기준으로
    특정 공지사항과의 적합도를 실시간으로 검증합니다.
    (comparison_logic.py의 check_suitability 사용)
    """
    user_id = current_user["id"]
    
    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. 공지사항 정보 가져오기 (AI 필드 포함)
            cur.execute("""
                SELECT 
                    id, title, category_ai, hashtags_ai, 
                    start_at_ai, end_at_ai, created_at,
                    qualification_ai
                FROM notices 
                WHERE id = %s
            """, (notice_id,))
            notice_row = cur.fetchone()
            
            if not notice_row:
                raise HTTPException(status_code=404, detail="Notice not found")

            # 2. 사용자 프로필 정보 가져오기
            cur.execute("""
                SELECT 
                    gender, age, major, grade, keywords,
                    military_service, income_bracket, gpa
                FROM user_profiles 
                WHERE user_id = %s
            """, (user_id,))
            profile_row = cur.fetchone()

            if not profile_row:
                raise HTTPException(status_code=404, detail="User profile not found. Please create a profile first.")

    except psycopg2.Error as e:
        logger.error(f"Database error in verify_eligibility: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    # 3. comparison_logic.py의 check_suitability 호출
    try:
        # DB에서 읽은 JSON(B)은 이미 dict임
        notice_qualifications = notice_row.get("qualification_ai") or {}
        
        # check_suitability 함수가 요구하는 notice_json 형식에 맞게 데이터 재구성
        # (comparison_logic.py의 _temporal_weight, _jaccard_bonus가 요구하는 키 포함)
        notice_data_for_check = {
            "qualifications": notice_qualifications,
            "hashtags_ai": notice_row.get("hashtags_ai") or [],
            "deadline_ai": notice_row.get("end_at_ai"),
            "end_at_ai": notice_row.get("end_at_ai"),
            "start_at_ai": notice_row.get("start_at_ai"),
            "created_at": notice_row.get("created_at"),
            "target_term_ai": notice_qualifications.get("grade_level") # 근사치로 grade_level 사용
        }

        # check_suitability 함수가 요구하는 user_profile 형식
        user_profile_for_check = dict(profile_row)
        
        # comparison_logic.py 실행
        result = check_suitability(user_profile_for_check, notice_data_for_check)
        return result

    except Exception as e:
        logger.error(f"Error during check_suitability call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Eligibility check failed: {e}")

# --- (신규) AI 수동 재추출 엔드포인트 ---
class ManualExtractRequest(BaseModel):
    notice_id: str
    mode: str = Field("all", regex="^(all|category|qualifications)$")

@app.post("/admin/re-extract-ai", tags=["admin"])
async def manual_reextract_ai(
    req: ManualExtractRequest,
    # (실제 운영 시) 여기에 관리자 인증 의존성을 추가해야 합니다.
    # current_admin: dict = Depends(get_admin_user) 
):
    """
    [관리자용] 특정 공지 ID에 대해 AI 추출을 수동으로 재실행합니다.
    - mode='all': 카테고리 분류 + 자격 요건 추출 모두 실행
    - mode='category': 카테고리 분류만 실행
    - mode='qualifications': 자격 요건 추출만 실행 (기존 카테고리 사용)
    """
    notice_id = req.notice_id
    mode = req.mode

    try:
        with get_conn() as conn:
            # 1. 원본 데이터 가져오기
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
            
            # 2. AI 처리
            new_data = {}
            if mode == "all" or mode == "category":
                logger.info(f"Re-classifying category for {notice_id}...")
                category_ai = classify_notice_category(title=title, body=body)
                new_data["category_ai"] = category_ai
                new_data["hashtags_ai"] = [category_ai] if category_ai and category_ai != "#일반" else None
                logger.info(f"New category: {category_ai}")
            else:
                category_ai = notice.get("category_ai") or "#일반" # 기존 카테고리 사용

            if mode == "all" or mode == "qualifications":
                logger.info(f"Re-extracting qualifications for {notice_id} (using category: {category_ai})...")
                structured_info = extract_structured_info(title=title, body=body, category=category_ai)
                
                # ai_processor.py의 결과 형식에 맞게 파싱
                new_data["qualification_ai"] = Json(structured_info if isinstance(structured_info, dict) else {})
                new_data["start_at_ai"] = _to_utc_ts(structured_info.get("start_date"))
                new_data["end_at_ai"] = _to_utc_ts(structured_info.get("end_date"))
                logger.info(f"New qualifications (brief): {str(structured_info)[:100]}...")

            # 3. DB 업데이트
            if new_data:
                set_clauses = []
                params = {"id": notice_id}
                for key, value in new_data.items():
                    set_clauses.append(f"{key} = %({key})s")
                    params[key] = value
                
                set_sql = ", ".join(set_clauses)
                
                with conn.cursor() as cur:
                    # search_vector도 강제로 재계산되도록 title을 업데이트합니다. (트리거 활용)
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