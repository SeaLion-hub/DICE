# admin_routes.py
import logging
import json 
from fastapi import APIRouter, HTTPException, status, Depends, Query 
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor
from db_pool import get_conn
from uuid import UUID
import os 
from pydantic import BaseModel # [신규] API 요청 본문을 위한 임포트

# [수정] 3가지 AI/로직 함수 모두 임포트
try:
    from ai_processor import extract_detailed_hashtags, extract_structured_info
    from comparison_logic import check_suitability
except ImportError:
    # ... (기존 mock 함수들) ...
    logging.warning("Using mock extract_detailed_hashtags function!")
    def extract_detailed_hashtags(title: str, body_text: str, main_categories: list[str]) -> list[str]:
        if "#취업" in main_categories: return ["#임시_채용", "#임시_조교"]
        return ["#임시태그"]
    def extract_structured_info(title: str, body: str, category: str) -> dict:
        logging.warning("Using mock extract_structured_info function!")
        return {"error": "mock function", "qualifications": {"gpa_min": "3.0"}}
    def check_suitability(user_profile: dict, notice_json: dict) -> dict:
         logging.warning("Using mock check_suitability function!")
         return {"eligibility": "BORDERLINE", "match_percentage": 50.0}


logger = logging.getLogger("dice-api.admin")
router = APIRouter(prefix="/admin", tags=["admin"])

# [수정] 3개의 HTML 파일 경로 설정
current_dir = os.path.dirname(os.path.abspath(__file__))
ADMIN_HASHTAG_HTML_PATH = os.path.join(current_dir, "admin_hashtags.html")
ADMIN_COMPARE_HTML_PATH = os.path.join(current_dir, "admin_compare.html")
ADMIN_BODY_HTML_PATH = os.path.join(current_dir, "admin_body.html") # [신규] 본문 수정 HTML


# 1. 세부 해시태그 관리자 페이지
@router.get("/dashboard_hashtags", response_class=FileResponse)
async def get_admin_hashtag_dashboard():
    """관리자용 세부 해시태그 추출 대시보드 HTML을 반환합니다."""
    if not os.path.exists(ADMIN_HASHTAG_HTML_PATH):
        logger.error(f"Admin HTML file not found at: {ADMIN_HASHTAG_HTML_PATH}")
        raise HTTPException(status_code=404, detail="Admin dashboard (hashtags) HTML not found.")
    return FileResponse(ADMIN_HASHTAG_HTML_PATH)

# 2. 지원자격/비교 관리자 페이지
@router.get("/dashboard_compare", response_class=FileResponse)
async def get_admin_compare_dashboard():
    """관리자용 지원자격 추출 및 비교 테스트 대시보드 HTML을 반환합니다."""
    if not os.path.exists(ADMIN_COMPARE_HTML_PATH):
        logger.error(f"Admin HTML file not found at: {ADMIN_COMPARE_HTML_PATH}")
        raise HTTPException(status_code=404, detail="Admin dashboard (compare) HTML not found.")
    return FileResponse(ADMIN_COMPARE_HTML_PATH)

# 3. [신규] 본문 수정 관리자 페이지
@router.get("/dashboard_body", response_class=FileResponse)
async def get_admin_body_dashboard():
    """관리자용 공지사항 본문(body_text) 수정 대시보드 HTML을 반환합니다."""
    if not os.path.exists(ADMIN_BODY_HTML_PATH):
        logger.error(f"Admin HTML file not found at: {ADMIN_BODY_HTML_PATH}")
        raise HTTPException(status_code=404, detail="Admin dashboard (body) HTML not found.")
    return FileResponse(ADMIN_BODY_HTML_PATH)


# 4. 공지사항 목록 API (기존)
@router.get("/api/notices")
async def get_notices_for_admin(
    limit: int = 100, 
    offset: int = 0,
    sort_by: str = Query("recent", description="Sort order: 'missing_tags', 'missing_quals', 'recent'")
):
    # ... (기존 /api/notices 로직과 동일) ...
    order_sql = ""
    filter_sql = "" 
    limit_sql = "LIMIT %s OFFSET %s" 
    params = [] 

    if sort_by == "missing_tags":
        filter_sql = "AND (n.detailed_hashtags IS NULL OR cardinality(n.detailed_hashtags) = 0)"
        order_sql = "ORDER BY n.created_at DESC"
        limit_sql = "" 
        
    elif sort_by == "missing_quals":
        order_sql = "ORDER BY (n.qualification_ai IS NULL OR n.qualification_ai IN ('', '{}', 'null', '[]')) DESC, n.created_at DESC"
    
    else: # Default "recent"
        order_sql = "ORDER BY n.created_at DESC"

    # [수정] 쿼리에 {filter_sql}, {limit_sql} 변수 사용
    query = f"""
    SELECT 
        n.id,
        COALESCE(c.name, 'N/A') as college_name,
        n.title,
        n.url,
        n.category_ai,
        n.detailed_hashtags,
        n.qualification_ai as ai_extracted_json,
        n.hashtags_ai,
        n.created_at,
        n.start_at_ai,
        n.end_at_ai
        -- [참고] 이 목록 API는 용량 문제로 body_text를 가져오지 않습니다.
    FROM notices n
    LEFT JOIN colleges c ON n.college_key = c.key
    WHERE n.hashtags_ai != ARRAY['#일반']
    {filter_sql} 
    {order_sql}
    {limit_sql};
    """
    
    if limit_sql:
        params.extend([limit, offset])

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, tuple(params)) 
                notices = cur.fetchall()
        
        for notice in notices:
            if notice.get('created_at'):
                notice['created_at'] = notice['created_at'].isoformat()
            if notice.get('start_at_ai'):
                notice['start_at_ai'] = notice['start_at_ai'].isoformat()
            if notice.get('end_at_ai'):
                notice['end_at_ai'] = notice['end_at_ai'].isoformat()
            
            qual_data = notice.get('ai_extracted_json')
            if qual_data and isinstance(qual_data, str):
                try:
                    notice['ai_extracted_json'] = json.loads(qual_data)
                except json.JSONDecodeError:
                    notice['ai_extracted_json'] = {"error": "Failed to parse saved JSON string"}
            elif not qual_data:
                notice['ai_extracted_json'] = None 
            
        return {"items": notices}
    except Exception as e:
        logger.error(f"Admin API Error fetching notices: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# 5. [수정] 공지사항 상세 API (본문 수정용)
@router.get("/api/notice-detail/{notice_id}")
async def get_notice_detail_for_admin(notice_id: UUID): # (FastAPI는 UUID로 검증)
    """(본문 수정용) 단일 공지사항의 전체 본문(body_text)을 가져옵니다."""
    query = "SELECT id, title, url, body_text FROM notices WHERE id = %s"
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # [수정] DB 드라이버에 전달 시 str()로 변환
                cur.execute(query, (str(notice_id),))
                notice = cur.fetchone()
        if not notice:
            raise HTTPException(status_code=404, detail="Notice not found")
        
        if notice['body_text'] is None:
            notice['body_text'] = ''
            
        return notice
    except Exception as e:
        logger.error(f"Admin API Error fetching notice detail {notice_id}: {e}", exc_info=False) # exc_info=False로 변경 (로그 단순화)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# 6. [수정] 본문 업데이트 API
class BodyUpdateRequest(BaseModel):
    notice_id: UUID # (FastAPI는 UUID로 검증)
    body_text: str

@router.post("/api/update-body")
async def api_update_body_text(payload: BodyUpdateRequest):
    """(본문 수정용) 공지사항의 body_text를 수동으로 업데이트합니다."""
    query = "UPDATE notices SET body_text = %s, updated_at = now() WHERE id = %s"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # [수정] DB 드라이버에 전달 시 str()로 변환
                cur.execute(query, (payload.body_text, str(payload.notice_id)))
                conn.commit()
        
        if cur.rowcount == 0:
             raise HTTPException(status_code=404, detail="Notice not found or no changes made")
        
        logger.info(f"Admin: Successfully updated body_text for notice {payload.notice_id}")
        return {"status": "success", "notice_id": str(payload.notice_id)} # 응답도 str로
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin API Error updating body_text for {payload.notice_id}: {e}", exc_info=False) # exc_info=False로 변경
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
# --- (이하 기존 API: 7. 해시태그, 8. 지원자격, 9. 비교 API) ---

# 7. 세부 해시태그 추출 API
@router.post("/api/extract-detailed-hashtags")
async def api_extract_detailed_hashtags(payload: dict):
    # ... (기존 코드) ...
    notice_id = payload.get("notice_id")
    main_categories = payload.get("main_categories") 
    if not notice_id or not main_categories or not isinstance(main_categories, list):
        raise HTTPException(status_code=400, detail="notice_id and main_categories (as a list) required")
    # ... (기존 로직) ...
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT title, body_text FROM notices WHERE id = %s", (notice_id,))
                row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Notice not found")
        body_text = row.get("body_text") or ""
        title = row.get("title") or ""
        if not title and not body_text:
             raise HTTPException(status_code=404, detail="Notice title and body are both empty")
        detailed_hashtags = extract_detailed_hashtags(title, body_text, main_categories)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notices SET detailed_hashtags = %s, updated_at = now() WHERE id = %s",
                    (detailed_hashtags, notice_id)
                )
                conn.commit()
        logger.info(f"Admin: Successfully extracted and saved {len(detailed_hashtags)} detailed tags for notice {notice_id}")
        return {"notice_id": notice_id, "detailed_hashtags": detailed_hashtags}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI extraction (detailed) API error for {notice_id}: {e}")
        raise HTTPException(status_code=500, detail=f"AI processing error: {e}")


# 8. 지원자격(JSON) 추출 API
@router.post("/api/extract-qualifications")
async def api_extract_qualifications(payload: dict):
    # ... (기존 코드) ...
    notice_id = payload.get("notice_id")
    main_category = payload.get("main_category") 
    if not notice_id or not main_category:
        raise HTTPException(status_code=400, detail="notice_id and main_category required")
    # ... (기존 로직) ...
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT title, body_text FROM notices WHERE id = %s", (notice_id,))
                row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Notice not found")
        body_text = row.get("body_text") or ""
        title = row.get("title") or ""
        if not title and not body_text:
             raise HTTPException(status_code=404, detail="Notice title and body are both empty")
        qual_json_dict = extract_structured_info(title, body_text, main_category)
        with get_conn() as conn:
            with conn.cursor() as cur:
                qual_json_string = json.dumps(qual_json_dict, ensure_ascii=False)
                cur.execute(
                    """
                    UPDATE notices 
                    SET qualification_ai = %s, updated_at = now() 
                    WHERE id = %s
                    """,
                    (qual_json_string, notice_id) 
                )
                conn.commit()
        logger.info(f"Admin: Successfully extracted and saved structured qualifications for notice {notice_id}")
        return {"notice_id": notice_id, "ai_extracted_json": qual_json_dict}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI extraction (structured) API error for {notice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"AI processing error: {e}")

# 9. 적합도 비교 API
@router.post("/api/compare-notice")
async def api_compare_notice(payload: dict):
    # ... (기존 코드) ...
    notice_json_from_client = payload.get("notice_json")
    user_profile = payload.get("user_profile")
    if not notice_json_from_client or not user_profile:
        raise HTTPException(status_code=400, detail="notice_json and user_profile required")
    # ... (기존 로직) ...
    try:
        notice_payload_for_comparison = dict(notice_json_from_client)
        ai_data_dict = notice_json_from_client.get('ai_extracted_json') 
        if ai_data_dict and isinstance(ai_data_dict, dict):
            notice_payload_for_comparison.update(ai_data_dict)
        comparison_result = check_suitability(user_profile, notice_payload_for_comparison)
        return comparison_result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comparison logic API error for {notice_json_from_client.get('id')}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Comparison processing error: {e}")