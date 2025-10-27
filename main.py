# main.py (프로젝트 루트 / AI 자동추출 통합 + 인증 라우터 통합 + FTS 검색 + DB 풀 버전 + 캘린더 이벤트 파싱)
import os
import logging
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from fastapi import FastAPI, Query, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from datetime import datetime
import datetime as dt
import requests
from typing import Optional, Any, Dict
import time
import threading
import json
from pydantic import BaseModel
import jwt


# AI processor import (extract_notice_info 포함)
from ai_processor import (
    extract_hashtags_from_title,
    classify_notice_category, # <-- 새로 추가
    extract_structured_info,
)
from comparison_logic import check_suitability
# 캘린더 유틸리티 import (이번 작업)
from calendar_utils import normalize_datetime_for_calendar

# 인증 라우터 import
from auth_routes import router as auth_router
from auth_security import decode_token

# DB Pool import (db_pool 사용)
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

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")

# 3) colleges.py import
from colleges import COLLEGES

# 4) 로깅
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("dice-api")

# 5) FastAPI 앱
app = FastAPI(title="DICE API", version="0.1.0", docs_url="/docs", redoc_url="/redoc")

# --- 시작 시 DB 풀 초기화 ---
@app.on_event("startup")
async def startup_event():
    logger.info("Initializing database connection pool...")
    init_pool()

# --- 종료 시 DB 풀 닫기 ---
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

    # URL이 상대 경로인 경우 절대 경로로 변환
    if normalized["url"] and not normalized["url"].startswith(("http://", "https://")):
        if base_url:
            normalized["url"] = base_url.rstrip("/") + "/" + normalized["url"].lstrip("/")

    # 날짜 파싱
    if item.get("published_at"):
        try:
            normalized["published_at"] = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        except:
            logger.warning(f"Could not parse 'published_at': {item.get('published_at')}")
            pass
    elif item.get("date"):
        try:
            d = dt.date.fromisoformat(item["date"])
            normalized["published_at"] = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
        except:
            logger.warning(f"Could not parse 'date': {item.get('date')}")
            pass

    return normalized

def validate_normalized_item(item: dict) -> bool:
    """정규화된 아이템이 유효한지 검증"""
    if not item.get("title") or not item.get("url"):
        logger.debug(f"Skipping item due to missing title or URL: {item.get('url')}")
        return False
    if not item["url"].startswith(("http://", "https://")):
        logger.debug(f"Skipping item due to invalid URL scheme: {item['url']}")
        return False
    if len(item["title"]) < 2:
        logger.debug(f"Skipping item due to short title: {item['title']}")
        return False
    return True

def content_hash(college_key: str, title: str, url: str, published_at: Optional[datetime]) -> str:
    """중복 방지를 위한 해시 생성"""
    date_str = published_at.isoformat() if published_at else "no-date"
    content = f"{college_key}|{title}|{url}|{date_str}"
    return hashlib.sha256(content.encode()).hexdigest()

# UPSERT SQL (AI 필드 포함, summary_ai 제거)
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

# 11) 라우트들
@app.get("/health")
def health():
    base = {"env": ENV, "service": "dice-api"}
    # 1) DB 연결 확인
    try:
        with get_conn() as conn, conn.cursor() as cur:
             cur.execute("SELECT 1 AS ok;")
    except Exception as e:
        logger.error(f"Health check DB connection error: {e}")
        raise HTTPException(status_code=503, detail={"status": "db_unavailable", **base})

    # 2) 마이그레이션/시드 준비 상태 확인
    try:
         with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS c FROM information_schema.tables WHERE table_name = 'colleges';")
            tbl = cur.fetchone()
            have_colleges = tbl and tbl["c"] == 1
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
        logger.error(f"Health check seeding/migration error: {e}")
        raise HTTPException(status_code=503, detail={"status": "health_check_error", **base})

    return {"status": "ok", **base}

@app.get("/notices")
def list_notices(
    request: Request,
    college: str | None = Query(None),
    q: str | None = Query(None),
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    sort: str = Query("recent", regex="^(recent|oldest|relevance)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    my: bool = Query(False, description="내 키워드와 일치하는 공지만 보기 (인증 필요)"),
):
    """
    공지사항 목록 조회 - Full-Text Search 사용
    """
    where, params = [], []
    order_params = []
    select_fields = """
        id, college_key, title, url,
        COALESCE(summary_raw, '') as summary_raw,
        category_ai, start_at_ai, end_at_ai,
        qualification_ai, hashtags_ai, published_at, created_at
    """
    relevance_score_field = ""

    # my=true 인 경우: 인증 + 사용자 keywords 로드
    user_keywords = None
    if my:
        auth = request.headers.get("Authorization", "")
        parts = auth.split()
        token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")

        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
            if not user_id: raise HTTPException(status_code=401, detail="Invalid token")
        except jwt.ExpiredSignatureError: raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError: raise HTTPException(status_code=401, detail="Invalid token")

        try:
            with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT keywords FROM user_profiles WHERE user_id = %s", [user_id])
                profile_row = cur.fetchone()
                user_keywords = profile_row['keywords'] if profile_row else []
        except Exception as e:
            logger.error(f"DB error fetching user keywords: {e}")
            raise HTTPException(status_code=500, detail="Database error fetching profile")

        if not user_keywords:
             return {"items": [], "total_count": 0, "limit": limit, "offset": offset, "sort": sort}

    # WHERE 절 구성
    if college and college.lower() != "all":
        where.append("college_key = %s")
        params.append(college)

    if q:
        where.append("search_vector @@ websearch_to_tsquery('simple', %s)")
        params.append(q.strip())
        order_params.append(q.strip())
        relevance_score_field = ", ts_rank(search_vector, websearch_to_tsquery('simple', %s)) as relevance_score"
        select_fields += relevance_score_field

    if date_from:
        try:
            from_dt = dt.datetime.fromisoformat(date_from).replace(tzinfo=dt.timezone.utc)
            where.append("published_at >= %s")
            params.append(from_dt)
        except ValueError:
             raise HTTPException(status_code=400, detail="Invalid date_from format (YYYY-MM-DD)")

    if date_to:
         try:
            to_dt_exclusive = dt.datetime.fromisoformat(date_to).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
            where.append("published_at < %s")
            params.append(to_dt_exclusive)
         except ValueError:
             raise HTTPException(status_code=400, detail="Invalid date_to format (YYYY-MM-DD)")

    if my and user_keywords:
        where.append("hashtags_ai && %s::text[]")
        params.append(user_keywords)

    where_clause = " WHERE " + " AND ".join(where) if where else ""

    # COUNT 쿼리
    count_sql = f"SELECT COUNT(*) AS total FROM notices{where_clause}"
    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_sql, params)
            count_result = cur.fetchone()
            total_count = count_result["total"] if count_result else 0
    except Exception as e:
        logger.error(f"DB error counting notices: {e}")
        raise HTTPException(status_code=500, detail="Database error counting results")

    # ORDER BY 절 구성
    if q and sort == "relevance":
        order_clause = " ORDER BY relevance_score DESC, published_at DESC NULLS LAST"
    elif sort == "oldest":
        order_clause = " ORDER BY published_at ASC NULLS FIRST, created_at ASC"
    else:
        order_clause = " ORDER BY published_at DESC NULLS LAST, created_at DESC"

    # 최종 SELECT 쿼리
    final_select_params = order_params if relevance_score_field else []
    sql = f"""
      SELECT {select_fields}
      FROM notices
      {where_clause}
      {order_clause}
      LIMIT %s OFFSET %s
    """
    final_params = final_select_params + params + [limit, offset]

    try:
         with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, final_params)
            rows = cur.fetchall()
            for row in rows:
                if 'qualification_ai' in row and isinstance(row['qualification_ai'], str):
                    try:
                        row['qualification_ai'] = json.loads(row['qualification_ai'])
                    except json.JSONDecodeError:
                        row['qualification_ai'] = {}
                elif 'qualification_ai' not in row or row['qualification_ai'] is None:
                     row['qualification_ai'] = {}

    except Exception as e:
        logger.error(f"DB error fetching notices: {e}")
        raise HTTPException(status_code=500, detail="Database error fetching results")

    response = {
        "items": rows,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
        "sort": sort
    }
    if q:
        response["search_query"] = q

    return response


@app.get("/stats")
def stats():
    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT college_key, COUNT(*) AS cnt
                FROM notices
                GROUP BY college_key
                ORDER BY cnt DESC
            """)
            by_college = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS total FROM notices;")
            total_result = cur.fetchone()
            total = total_result["total"] if total_result else 0
    except Exception as e:
        logger.error(f"DB error fetching stats: {e}")
        raise HTTPException(status_code=500, detail="Database error fetching stats")
    return {"total": total, "by_college": by_college}

@app.get("/colleges")
def list_colleges():
    cache_key = "colleges_list"
    cached_data = cache_get(cache_key)
    if cached_data:
        return cached_data

    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
              SELECT key AS college_key, name, url, color, icon
              FROM colleges ORDER BY name
            """)
            rows = cur.fetchall()
        result = {"items": rows}
        cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"DB error fetching colleges: {e}")
        raise HTTPException(status_code=500, detail="Database error fetching colleges")

@app.get("/notices/{notice_id}")
def get_notice(notice_id: str):
    cache_key = f"notice_{notice_id}"
    cached_data = cache_get(cache_key)
    if cached_data:
        if isinstance(cached_data.get('qualification_ai'), str):
             try:
                 cached_data['qualification_ai'] = json.loads(cached_data['qualification_ai'])
             except json.JSONDecodeError:
                 cached_data['qualification_ai'] = {}
        elif cached_data.get('qualification_ai') is None:
            cached_data['qualification_ai'] = {}
        return cached_data

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
        raise HTTPException(status_code=404, detail="notice not found")

    if notice.get("summary_raw") is None:
        notice["summary_raw"] = ""

    if isinstance(notice.get('qualification_ai'), str):
        try:
            notice['qualification_ai'] = json.loads(notice['qualification_ai'])
        except json.JSONDecodeError:
            notice['qualification_ai'] = {}
    elif notice.get('qualification_ai') is None:
        notice['qualification_ai'] = {}

    cache_set(cache_key, notice)
    return notice

@app.post("/apify/webhook")
def apify_webhook(token: str = Query(...), payload: dict = Body(...)):
    """Apify webhook 엔드포인트 - 크롤링 완료 시 호출됨 (AI 처리 포함)"""
    # 1) 보안 토큰 확인
    if token != APIFY_WEBHOOK_TOKEN:
        logger.warning(f"Invalid webhook token attempt")
        raise HTTPException(status_code=401, detail="invalid token")

    logger.info(f"Webhook received: {payload.get('eventType', 'unknown')}")

    # 2) datasetId / taskId 추출
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
        r.raise_for_status()
        items = r.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch dataset {ds_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch dataset: {e}")
    except json.JSONDecodeError as e:
         logger.error(f"Failed to decode JSON from dataset {ds_id}: {e}")
         raise HTTPException(status_code=502, detail="Invalid JSON response from Apify")


    if not isinstance(items, list):
        logger.warning(f"Apify response was not a list, attempting to extract 'items' key.")
        items = items.get("items", [])
        if not isinstance(items, list):
             logger.error(f"Could not extract list of items from Apify response for dataset {ds_id}")
             raise HTTPException(status_code=500, detail="Unexpected Apify response format")


    logger.info(f"Fetched {len(items)} items from dataset {ds_id}")

    # 4) 어떤 단과대 task인지 매핑
    college_key = None
    if task_id:
        for ck, meta in COLLEGES.items():
            if meta.get("task_id") == task_id:
                college_key = ck
                break

    if not college_key:
        college_key = "main"
        logger.warning(f"Unknown task_id {task_id}, using 'main' as default for dataset {ds_id}")

    logger.info(f"Processing for college: {college_key}")

    # 5) upsert (AI 추출 포함)
    upserted, skipped = 0, 0
    try:
        with get_conn() as conn, conn.cursor() as cur:
            for rec in items:
                norm = normalize_item(rec, base_url=COLLEGES[college_key].get("url"))
                if not validate_normalized_item(norm):
                    skipped += 1
                    continue

                # AI 자동추출
                if AI_IN_PIPELINE:
                    try:
                        title_for_ai = norm.get("title", "")
                        body_for_ai = norm.get("body_text", "")

                        # 1단계: 카테고리 분류
                        category_ai = classify_notice_category(title=title_for_ai, body=body_for_ai)
                        norm["category_ai"] = category_ai # 분류 결과 저장
                        norm["hashtags_ai"] = [category_ai] if category_ai else None # 해시태그는 분류 결과 사용

                        # 2단계: 구조화된 정보 추출 (분류된 카테고리 사용)
                        structured_info = extract_structured_info(title=title_for_ai, body=body_for_ai, category=category_ai)

                        # --- structured_info에서 start_at_ai, end_at_ai, qualification_ai 추출 ---
                        # 이 부분은 ai_processor.py의 JSON 구조에 맞게 구현해야 합니다.
                        # 예시: key_date 필드를 파싱하거나, qualifications 객체를 사용합니다.
                        # calendar_utils.py의 normalize_datetime_for_calendar 함수 활용 고려
                        start_at_ai = None # structured_info['key_date'] 등을 파싱하여 설정
                        end_at_ai = None   # structured_info['key_date'] 등을 파싱하여 설정
                        qualification_ai = structured_info.get("qualifications", structured_info if isinstance(structured_info, dict) else {}) # 구조 확인 필요


                        norm["start_at_ai"] = start_at_ai
                        norm["end_at_ai"] = end_at_ai
                        norm["qualification_ai"] = qualification_ai

                    except Exception as e:
                        logger.warning(f"AI extraction failed for '{norm.get('title', 'N/A')[:50]}...': {e}. Proceeding without AI data.")
                        norm["category_ai"] = None
                        norm["start_at_ai"] = None
                        norm["end_at_ai"] = None
                        norm["qualification_ai"] = {}
                        norm["hashtags_ai"] = None
                else:
                    norm["category_ai"] = None
                    norm["start_at_ai"] = None
                    norm["end_at_ai"] = None
                    norm["qualification_ai"] = {}
                    norm["hashtags_ai"] = None

                h = content_hash(college_key, norm["title"], norm["url"], norm.get("published_at"))

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
                        "content_hash": h,
                        "category_ai": norm.get("category_ai"),
                        "start_at_ai": norm.get("start_at_ai"),
                        "end_at_ai": norm.get("end_at_ai"),
                        "qualification_ai": Json(norm.get("qualification_ai") or {}),
                        "hashtags_ai": norm.get("hashtags_ai"),
                    })
                    if cur.rowcount > 0:
                        upserted += 1
                        logger.debug(f"Upserted: {norm['title'][:50]}...")
                    else:
                        logger.debug(f"Skipped (already exists?): {norm['title'][:50]}...")

                except psycopg2.Error as db_err:
                    conn.rollback()
                    logger.error(f"Failed to upsert item '{norm.get('title', 'N/A')[:50]}...' ({h}): {db_err}")
                    skipped += 1
                except Exception as general_err:
                    conn.rollback()
                    logger.error(f"Unexpected error during upsert for item '{norm.get('title', 'N/A')[:50]}...': {general_err}")
                    skipped += 1


            conn.commit()
            logger.info(f"Webhook processing complete for college {college_key}: {upserted} upserted, {skipped} skipped")

    except psycopg2.Error as e:
        logger.error(f"Database connection or general error during webhook processing: {e}")
        raise HTTPException(status_code=500, detail="Database error during processing")
    except Exception as e:
         logger.error(f"Unexpected error during webhook processing: {e}")
         raise HTTPException(status_code=500, detail="An unexpected error occurred")


    cache_set("colleges_list", None, ttl=1)

    return {
        "status": "ok",
        "college": college_key,
        "upserted": upserted,
        "skipped": skipped,
        "total_items": len(items)}