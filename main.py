# main.py (프로젝트 루트 / AI 자동추출 통합 + 인증 라우터 통합 최종 버전 + AI 요약 통합 + 고급 검색)
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
from typing import Optional, Any
import time
import threading
import json
import re
from pydantic import BaseModel
import jwt

# AI processor import
from ai_processor import (
    extract_hashtags_from_title,
    extract_notice_info,
    verify_eligibility_ai,
    generate_brief_summary,  # NEW: 요약 생성 함수 추가
)

# 인증 라우터 import
from auth_routes import router as auth_router
from auth_security import decode_token

# 1) .env 로드 (로컬 실행용 / Railway에선 환경변수로 자동 주입)
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

# 8) 헬퍼: DB 연결
def query_all(sql: str, params=None):
    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()

# 9) 캐시 시스템
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

# 10) AI 헬퍼 함수 (내구성 강화 버전)
def _to_utc_ts(date_yyyy_mm_dd: str | None):
    """'YYYY-MM-DD' -> aware UTC midnight; None 유지 (방어적 파싱)"""
    if not date_yyyy_mm_dd:
        return None
    try:
        d = dt.date.fromisoformat(date_yyyy_mm_dd)
        return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    except Exception:
        return None

# 11) Crawler 헬퍼 함수들
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

# UPSERT SQL (AI 필드 포함) - summary_ai 추가
UPSERT_SQL = """
    INSERT INTO notices (
        college_key, title, url, summary_raw, summary_ai, body_html, body_text, 
        published_at, source_site, content_hash,
        category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai
    ) VALUES (
        %(college_key)s, %(title)s, %(url)s, %(summary_raw)s, %(summary_ai)s,
        %(body_html)s, %(body_text)s, %(published_at)s, 
        %(source_site)s, %(content_hash)s,
        %(category_ai)s, %(start_at_ai)s, %(end_at_ai)s, %(qualification_ai)s, %(hashtags_ai)s
    )
    ON CONFLICT (content_hash) 
    DO UPDATE SET
        summary_raw = EXCLUDED.summary_raw,
        summary_ai = EXCLUDED.summary_ai,
        body_html = EXCLUDED.body_html,
        body_text = EXCLUDED.body_text,
        category_ai = EXCLUDED.category_ai,
        start_at_ai = EXCLUDED.start_at_ai,
        end_at_ai = EXCLUDED.end_at_ai,
        qualification_ai = EXCLUDED.qualification_ai,
        hashtags_ai = EXCLUDED.hashtags_ai,
        updated_at = CURRENT_TIMESTAMP
"""

# 12) 라우트들
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
    request: Request,
    college: str | None = Query(None),
    q: str | None = Query(None),
    search_mode: str = Query("trgm", regex="^(like|trgm|fts|websearch)$", description="검색 모드: like|trgm|fts|websearch"),
    op: str = Query("and", regex="^(and|or)$", description="키워드 결합 방식: and|or"),
    rank: str | None = Query(None, regex="^(off|trgm|fts)$", description="랭킹 모드: off|trgm|fts (기본값: fts모드일 때 fts, 아니면 off)"),
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
    
    - my=true: 로그인 사용자의 프로필 키워드와 일치하는 공지만 반환
    - search_mode: 검색 엔진 선택 (like: 기본 ILIKE, trgm: trigram 인덱스, fts: 전문 검색, websearch: 고급 자연어)
    - op: 다중 키워드 결합 방식 (and: 모든 키워드 포함, or: 하나 이상 포함)
    - rank: 검색 결과 정렬 방식 (기본값: fts/websearch 모드에서는 fts, 그 외는 off)
    - count: 전체 카운트 포함 여부 (무한 스크롤 등에서 false로 성능 향상)
    - no_cache: 캐시 무시 옵션 (디버그/테스트용)
    """
    
    # rank 기본값 설정: FTS/websearch 모드일 때는 FTS 랭킹이 기본
    if rank is None:
        rank = "fts" if search_mode in ("fts", "websearch") else "off"
    
    # 캐시 키 생성 (쿼리 파라미터 기반)
    if not no_cache and not my:  # my=true는 사용자별이므로 캐시 제외
        cache_key_parts = [
            f"notices:v1",
            f"c={college or 'all'}",
            f"q={q or ''}",
            f"sm={search_mode}",
            f"op={op}",
            f"r={rank}",
            f"df={date_from or ''}",
            f"dt={date_to or ''}",
            f"s={sort}",
            f"l={limit}",
            f"o={offset}",
            f"cnt={count}"
        ]
        cache_key = ":".join(cache_key_parts)
        
        # 캐시 확인
        cached_response = cache_get(cache_key)
        if cached_response is not None:
            logger.info(f"Cache hit for notices query: {cache_key[:50]}...")
            return cached_response
    
    # my=true 인 경우: 인증 + 사용자 keywords 로드
    user_keywords = None
    if my:
        # Authorization 헤더에서 Bearer 토큰 읽기
        auth = request.headers.get("Authorization", "")
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        else:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # 토큰 검증 후 user_id 추출
        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
            if not user_id:
                raise HTTPException(status_code=401, detail="Invalid token")
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

        # DB에서 keywords 조회
        try:
            rows = query_all("""
                SELECT keywords
                FROM user_profiles
                WHERE user_id = %s
            """, [user_id])
        except Exception:
            raise HTTPException(status_code=500, detail="Database error")

        if not rows:
            # 프로필이 없으면 맞춤 공지는 없음
            return {
                "meta": {
                    "search_mode": search_mode,
                    "op": op,
                    "rank": rank,
                    "limit": limit,
                    "offset": offset,
                    "returned": 0
                },
                "items": []
            }

        user_keywords = rows[0].get("keywords") or []
        if not user_keywords:
            # 키워드가 비어 있으면 결과도 비움
            return {
                "meta": {
                    "search_mode": search_mode,
                    "op": op,
                    "rank": rank,
                    "limit": limit,
                    "offset": offset,
                    "returned": 0
                },
                "items": []
            }
    
    # 검색어 토큰화 (길이 2 미만 제외)
    tokens = []
    q_raw = ""
    if q:
        q_raw = q.strip()
        if q_raw:
            # 공백으로 분리 후 2글자 이상만 필터
            tokens = [t for t in re.split(r'\s+', q_raw) if len(t) >= 2]
    
    # WHERE 절과 파라미터 구성
    where_clauses = []
    order_clauses = []
    params = {}
    select_extra = []
    
    # 기본 필터들
    if college and college != "all":
        where_clauses.append("college_key = %(college)s")
        params["college"] = college
    
    # 날짜 파싱 with 방어적 처리
    if date_from:
        try:
            params["date_from"] = datetime.fromisoformat(date_from)
            where_clauses.append("published_at >= %(date_from)s")
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid date_from format: {date_from}. Use YYYY-MM-DD")
    
    if date_to:
        try:
            params["date_to"] = datetime.fromisoformat(date_to)
            where_clauses.append("published_at < %(date_to)s")
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid date_to format: {date_to}. Use YYYY-MM-DD")
    
    # my=true 필터: 사용자 키워드와 공지 해시태그의 교집합
    if my and user_keywords:
        where_clauses.append("(hashtags_ai && %(user_keywords)s::text[])")
        params["user_keywords"] = user_keywords
    
    # 검색 모드별 처리
    if tokens:
        if search_mode in ("like", "trgm"):
            # ILIKE 기반 검색 (trgm은 인덱스가 있어 더 빠름)
            token_conditions = []
            for i, token in enumerate(tokens):
                param_name = f"token_{i}"
                params[param_name] = f"%{token}%"
                token_conditions.append(
                    f"(title ILIKE %({param_name})s OR summary_ai ILIKE %({param_name})s)"
                )
            
            # AND/OR 결합
            if op == "and":
                where_clauses.append("(" + " AND ".join(token_conditions) + ")")
            else:  # or
                where_clauses.append("(" + " OR ".join(token_conditions) + ")")
            
            # trgm 랭킹 처리
            if rank == "trgm":
                params["q_raw"] = q_raw
                select_extra.append("GREATEST(similarity(title, %(q_raw)s), similarity(summary_ai, %(q_raw)s)) AS trgm_rank")
                order_clauses.append("GREATEST(similarity(title, %(q_raw)s), similarity(summary_ai, %(q_raw)s)) DESC")
                
        elif search_mode == "fts":
            # 전문 검색 - 고급 tsquery 구성
            if len(tokens) == 1:
                # 단일 토큰은 plainto_tsquery 사용 (더 유연한 매칭)
                params["tsquery_single"] = tokens[0]
                where_clauses.append("ts_ko @@ plainto_tsquery('simple', %(tsquery_single)s)")
                
                # fts 랭킹 처리
                if rank == "fts":
                    select_extra.append("ts_rank(ts_ko, plainto_tsquery('simple', %(tsquery_single)s)) AS fts_rank")
                    order_clauses.append("ts_rank(ts_ko, plainto_tsquery('simple', %(tsquery_single)s)) DESC")
            else:
                # 다중 토큰 - to_tsquery with AND/OR
                if op == "and":
                    tsquery = " & ".join(tokens)
                else:  # or
                    tsquery = " | ".join(tokens)
                
                # 특수문자 필터링 (한글, 영문, 숫자, &, | 만 허용)
                tsquery = re.sub(r'[^0-9A-Za-z가-힣\|\&\s]', '', tsquery)
                
                if tsquery:  # 필터링 후에도 남아있으면
                    params["tsquery"] = tsquery
                    where_clauses.append("ts_ko @@ to_tsquery('simple', %(tsquery)s)")
                    
                    # fts 랭킹 처리
                    if rank == "fts":
                        select_extra.append("ts_rank(ts_ko, to_tsquery('simple', %(tsquery)s)) AS fts_rank")
                        order_clauses.append("ts_rank(ts_ko, to_tsquery('simple', %(tsquery)s)) DESC")
                        
        elif search_mode == "websearch":
            # websearch_to_tsquery - PostgreSQL 12+ 자연어 검색
            # 따옴표, OR, - 등을 자동 파싱 ("장학 신청" OR 등록 -연체)
            if tokens:
                # 원본 쿼리를 그대로 사용 (websearch가 알아서 파싱)
                params["websearch_query"] = q_raw
                
                # PostgreSQL 버전 체크 및 폴백
                try:
                    where_clauses.append("ts_ko @@ websearch_to_tsquery('simple', %(websearch_query)s)")
                    
                    # websearch 랭킹 처리
                    if rank == "fts":
                        select_extra.append("ts_rank(ts_ko, websearch_to_tsquery('simple', %(websearch_query)s)) AS fts_rank")
                        order_clauses.append("ts_rank(ts_ko, websearch_to_tsquery('simple', %(websearch_query)s)) DESC")
                except Exception as e:
                    # PostgreSQL 11 이하 폴백: plainto_tsquery 사용
                    logger.warning(f"websearch_to_tsquery not available, falling back to plainto_tsquery: {e}")
                    params["fallback_query"] = q_raw
                    where_clauses.append("ts_ko @@ plainto_tsquery('simple', %(fallback_query)s)")
                    
                    if rank == "fts":
                        select_extra.append("ts_rank(ts_ko, plainto_tsquery('simple', %(fallback_query)s)) AS fts_rank")
                        order_clauses.append("ts_rank(ts_ko, plainto_tsquery('simple', %(fallback_query)s)) DESC")
    
    # 기본 정렬 추가
    if sort == "oldest":
        order_clauses.append("published_at ASC NULLS FIRST")
        order_clauses.append("created_at ASC")
    else:  # recent
        order_clauses.append("published_at DESC NULLS LAST")
        order_clauses.append("created_at DESC")
    
    # SQL 구성
    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)
    
    order_sql = ""
    if order_clauses:
        order_sql = " ORDER BY " + ", ".join(order_clauses)
    
    select_extra_sql = ""
    if select_extra:
        select_extra_sql = ", " + ", ".join(select_extra)
    
    # COUNT 쿼리 (옵션)
    total_count = None
    if count:
        count_sql = f"SELECT COUNT(*) AS total FROM notices{where_sql}"
        count_result = query_all(count_sql, params)
        total_count = count_result[0]["total"] if count_result else 0
    
    # SELECT 쿼리
    sql = f"""
        SELECT 
            id, college_key, title, url, summary_ai, summary_raw,
            category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai,
            published_at, created_at
            {select_extra_sql}
        FROM notices
        {where_sql}
        {order_sql}
        LIMIT %(limit)s OFFSET %(offset)s
    """
    
    params["limit"] = min(limit, 100)  # 상한 강제
    params["offset"] = offset
    
    rows = query_all(sql, params)
    
    # 응답 전 summary_ai 없는 경우 기본값 설정
    for row in rows:
        if not row.get("summary_ai"):
            row["summary_ai"] = "요약 준비중"
    
    # 응답 구성
    response = {
        "meta": {
            "search_mode": search_mode,
            "op": op,
            "rank": rank,
            "limit": params["limit"],
            "offset": offset,
            "returned": len(rows)
        },
        "items": rows
    }
    
    # 전체 카운트는 요청 시에만 포함
    if count and total_count is not None:
        response["meta"]["total_count"] = total_count
    
    # 캐시 저장 (my=false이고 no_cache=false일 때만)
    if not no_cache and not my and 'cache_key' in locals():
        cache_ttl = 60 if q else 30  # 검색 쿼리는 60초, 전체 목록은 30초
        cache_set(cache_key, response, ttl=cache_ttl)
        logger.info(f"Cached notices response for {cache_ttl}s: {cache_key[:50]}...")
    
    return response

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
             category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai,
             published_at, created_at, updated_at
      FROM notices WHERE id = %s
    """, [notice_id])
    if not rows:
        raise HTTPException(status_code=404, detail="notice not found")
    
    notice = rows[0]
    # summary_ai 기본값 처리
    if not notice.get("summary_ai"):
        notice["summary_ai"] = "요약 준비중"
        
    return notice

@app.post("/apify/webhook")
def apify_webhook(token: str = Query(...), payload: dict = Body(...)):
    """Apify webhook 엔드포인트 - 크롤링 완료 시 호출됨"""
    # 1) 보안 토큰 확인 (강화)
    expected_token = os.getenv("APIFY_WEBHOOK_TOKEN")
    if not expected_token or token != expected_token:
        logger.warning(f"Invalid webhook token attempt")
        raise HTTPException(status_code=401, detail="unauthorized")
    
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
        college_key = "main"
        logger.warning(f"Unknown task_id {task_id}, using 'main' as default")
    
    logger.info(f"Processing for college: {college_key}")
    
    # 5) upsert (AI 추출 포함)
    upserted, skipped = 0, 0
    try:
        with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            for rec in items:
                norm = normalize_item(rec, base_url=COLLEGES[college_key].get("url"))
                if not validate_normalized_item(norm):
                    skipped += 1
                    continue
                
                # AI 자동추출 (실패해도 파이프라인은 계속)
                use_ai = AI_IN_PIPELINE
                if use_ai:
                    try:
                        title_for_ai = (norm.get("title") or "").strip()
                        body_for_ai = (norm.get("body_text") or "").strip()

                        # 1) 본문/제목 기반 구조화 추출
                        ai = extract_notice_info(body_text=body_for_ai, title=title_for_ai) or {}

                        # 2) 제목 해시태그 추출
                        ht = extract_hashtags_from_title(title_for_ai) or {}

                        # 3) 필드 매핑
                        norm["category_ai"] = ai.get("category_ai")
                        norm["start_at_ai"] = _to_utc_ts(ai.get("start_date_ai"))
                        norm["end_at_ai"] = _to_utc_ts(ai.get("end_date_ai"))

                        # qualification_ai: dict or {}
                        qual_dict = ai.get("qualification_ai") or {}
                        norm["qualification_ai"] = qual_dict

                        # hashtags_ai: list or None
                        norm["hashtags_ai"] = ht.get("hashtags") or None

                        # 4) NEW: 요약 생성
                        # 입력 소스 우선순위: title + (summary_raw 우선, 없으면 body_text)
                        text_for_summary = norm.get("summary_raw") or norm.get("body_text") or ""
                        try:
                            norm["summary_ai"] = generate_brief_summary(
                                title=title_for_ai,
                                text=text_for_summary
                            )
                            logger.debug(f"Summary generated for: {title_for_ai[:50]}")
                        except Exception as se:
                            logger.warning(f"Summary generation failed: {se}")
                            # 폴백: 제목 기반 최소 요약
                            norm["summary_ai"] = title_for_ai[:180] if title_for_ai else "요약 준비중"

                    except Exception as e:
                        # 실패 시에도 저장 (기존 파이프라인을 막지 않음)
                        logger.warning(f"AI extraction failed for {norm.get('title', 'unknown')}: {e}")
                        norm["category_ai"] = None
                        norm["start_at_ai"] = None
                        norm["end_at_ai"] = None
                        norm["qualification_ai"] = {}
                        norm["hashtags_ai"] = None
                        # AI 요약 실패 시 폴백
                        norm["summary_ai"] = norm.get("title", "요약 준비중")[:180]
                else:
                    # AI 비활성화 시 전부 None/빈값
                    norm["category_ai"] = None
                    norm["start_at_ai"] = None
                    norm["end_at_ai"] = None
                    norm["qualification_ai"] = {}
                    norm["hashtags_ai"] = None
                    norm["summary_ai"] = None
                
                h = content_hash(college_key, norm["title"], norm["url"], norm["published_at"])
                
                try:
                    cur.execute(UPSERT_SQL, {
                        "college_key": college_key,
                        "title": norm["title"],
                        "url": norm["url"],
                        "summary_raw": norm.get("summary_raw"),
                        "summary_ai": norm.get("summary_ai"), # NEW: summary_ai 추가
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

# 13) 실시간 자격검증 엔드포인트
class UserProfile(BaseModel):
    grade: int | None = None
    major: str | None = None
    gpa: float | None = None
    lang: str | None = None

@app.post("/notices/{notice_id}/verify-eligibility")
def verify_eligibility_endpoint(notice_id: str, profile: UserProfile):
    """사용자 프로필을 기반으로 공지 자격요건 검증"""
    try:
        with psycopg2.connect(DATABASE_URL) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
              SELECT qualification_ai
              FROM notices
              WHERE id=%(id)s
            """, {"id": notice_id})
            row = cur.fetchone()
    except psycopg2.Error as e:
        logger.error(f"Database error in verify_eligibility: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    if not row:
        raise HTTPException(status_code=404, detail="notice not found")

    qa = row.get("qualification_ai")
    if not qa:
        return {"eligible": False, "reason": "해당 공지에 AI 자격요건 데이터가 없습니다."}

    # ai_processor 호출 (dict 필요)
    if isinstance(qa, str):
        try:
            qa = json.loads(qa)
        except Exception:
            qa = {}

    try:
        result = verify_eligibility_ai(qa, profile.model_dump())
        return {
            "eligible": bool(result.get("eligible")),
            "reason": result.get("reason") or ""
        }
    except Exception as e:
        logger.error(f"AI verification failed: {e}")
        return {"eligible": False, "reason": "자격 검증 중 오류가 발생했습니다."}