# admin_routes.py
import logging
import json # [중요] JSON 문자열 변환을 위해 임포트
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor
from db_pool import get_conn
import os # FileResponse 경로용

# [수정] 3가지 AI/로직 함수 모두 임포트
try:
    from ai_processor import extract_detailed_hashtags, extract_structured_info
    from comparison_logic import check_suitability
except ImportError:
    # 임시방편 (실제로는 각 .py 파일에 있어야 함)
    def extract_detailed_hashtags(title: str, body_text: str, main_category: str) -> list[str]:
        logging.warning("Using mock extract_detailed_hashtags function!")
        if main_category == "#취업": return ["#임시_채용", "#임시_조교"]
        return ["#임시태그"]

    def extract_structured_info(title: str, body: str, category: str) -> dict:
        logging.warning("Using mock extract_structured_info function!")
        return {"error": "mock function", "qualifications": {"gpa_min": "3.0"}}
    
    def check_suitability(user_profile: dict, notice_json: dict) -> dict:
         logging.warning("Using mock check_suitability function!")
         return {"eligibility": "BORDERLINE", "match_percentage": 50.0}


logger = logging.getLogger("dice-api.admin")
router = APIRouter(prefix="/admin", tags=["admin"])

# [수정] 2개의 HTML 파일 경로 설정
current_dir = os.path.dirname(os.path.abspath(__file__))
ADMIN_HASHTAG_HTML_PATH = os.path.join(current_dir, "admin_hashtags.html")
ADMIN_COMPARE_HTML_PATH = os.path.join(current_dir, "admin_compare.html")


# 1. [신규] 세부 해시태그 관리자 페이지 서빙
@router.get("/dashboard_hashtags", response_class=FileResponse)
async def get_admin_hashtag_dashboard():
    """관리자용 세부 해시태그 추출 대시보드 HTML을 반환합니다."""
    if not os.path.exists(ADMIN_HASHTAG_HTML_PATH):
        logger.error(f"Admin HTML file not found at: {ADMIN_HASHTAG_HTML_PATH}")
        raise HTTPException(status_code=404, detail="Admin dashboard (hashtags) HTML not found.")
    return FileResponse(ADMIN_HASHTAG_HTML_PATH)

# 2. [신규] 지원자격/비교 관리자 페이지 서빙
@router.get("/dashboard_compare", response_class=FileResponse)
async def get_admin_compare_dashboard():
    """관리자용 지원자격 추출 및 비교 테스트 대시보드 HTML을 반환합니다."""
    if not os.path.exists(ADMIN_COMPARE_HTML_PATH):
        logger.error(f"Admin HTML file not found at: {ADMIN_COMPARE_HTML_PATH}")
        raise HTTPException(status_code=404, detail="Admin dashboard (compare) HTML not found.")
    return FileResponse(ADMIN_COMPARE_HTML_PATH)


# 3. 공지사항 목록 API - [수정됨: JSON 문자열을 dict로 파싱]
@router.get("/api/notices")
async def get_notices_for_admin(limit: int = 100, offset: int = 0):
    """관리자 페이지용 공지사항 목록 (AI 추출 및 비교에 필요한 모든 필드 포함)"""
    
    query = """
    SELECT 
        n.id,
        COALESCE(c.name, 'N/A') as college_name,
        n.title,
        n.url,
        n.category_ai,
        n.detailed_hashtags,
        n.qualification_ai as ai_extracted_json, -- (이것은 DB에서 TEXT/VARCHAR일 수 있음)
        n.hashtags_ai,
        n.created_at,
        n.start_at_ai,
        n.end_at_ai
    FROM notices n
    LEFT JOIN colleges c ON n.college_key = c.key
    WHERE n.category_ai IS NOT NULL AND n.category_ai != '#일반'
      AND n.body_text IS NOT NULL AND n.body_text != ''
    ORDER BY n.created_at DESC
    LIMIT %s OFFSET %s;
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (limit, offset))
                notices = cur.fetchall()
        
        # [수정] DB에서 가져온 데이터를 프론트엔드로 보내기 전 처리
        for notice in notices:
            # 1. 날짜 변환
            if notice.get('created_at'):
                notice['created_at'] = notice['created_at'].isoformat()
            if notice.get('start_at_ai'):
                notice['start_at_ai'] = notice['start_at_ai'].isoformat()
            if notice.get('end_at_ai'):
                notice['end_at_ai'] = notice['end_at_ai'].isoformat()
            
            # 2. [FIX] JSON 문자열을 dict(JSON 객체)로 파싱
            qual_data = notice.get('ai_extracted_json')
            if qual_data and isinstance(qual_data, str):
                try:
                    notice['ai_extracted_json'] = json.loads(qual_data)
                except json.JSONDecodeError:
                    # 만약 DB에 저장된 문자열이 깨진 JSON이라면 오류 객체를 보냄
                    notice['ai_extracted_json'] = {"error": "Failed to parse saved JSON string"}
            elif not qual_data:
                notice['ai_extracted_json'] = None # 명시적으로 null
            # (이미 dict/jsonb 타입이면 그대로 둠)

        return {"items": notices}
    except Exception as e:
        logger.error(f"Admin API Error fetching notices: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# 4. 세부 해시태그 추출 API (변경 없음)
@router.post("/api/extract-detailed-hashtags")
async def api_extract_detailed_hashtags(payload: dict):
    notice_id = payload.get("notice_id")
    main_category = payload.get("main_category")
    if not notice_id or not main_category:
        raise HTTPException(status_code=400, detail="notice_id and main_category required")
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT title, body_text FROM notices WHERE id = %s", (notice_id,))
                row = cur.fetchone()
        if not row or not row.get("body_text"):
            raise HTTPException(status_code=404, detail="Notice body not found or empty")
        body_text = row["body_text"]
        title = row["title"]
        detailed_hashtags = extract_detailed_hashtags(title, body_text, main_category)
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


# 5. 지원자격(JSON) 추출 API - [수정됨: dict를 JSON 문자열로 변환하여 저장]
@router.post("/api/extract-qualifications")
async def api_extract_qualifications(payload: dict):
    """(기능 2) 구조화된 지원자격(JSON)을 AI로 추출하고 DB(TEXT)에 저장"""
    notice_id = payload.get("notice_id")
    main_category = payload.get("main_category")

    if not notice_id or not main_category:
        raise HTTPException(status_code=400, detail="notice_id and main_category required")

    try:
        # 1. DB에서 body_text 가져오기
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT title, body_text FROM notices WHERE id = %s", (notice_id,))
                row = cur.fetchone()
        
        if not row or not row.get("body_text"):
            raise HTTPException(status_code=404, detail="Notice body not found or empty")
        
        body_text = row["body_text"]
        title = row["title"]

        # 2. AI 함수 호출 (결과는 dict)
        qual_json_dict = extract_structured_info(title, body_text, main_category)
        
        # 3. AI 결과를 DB에 저장
        with get_conn() as conn:
            with conn.cursor() as cur:
                
                # [FIX] dict -> JSON 문자열(string)로 변환
                # (ensure_ascii=False로 한글 깨짐 방지)
                qual_json_string = json.dumps(qual_json_dict, ensure_ascii=False)
                
                cur.execute(
                    """
                    UPDATE notices 
                    SET qualification_ai = %s, updated_at = now() 
                    WHERE id = %s
                    """,
                    (qual_json_string, notice_id) # [FIX] 문자열을 전달
                )
                conn.commit()

        logger.info(f"Admin: Successfully extracted and saved structured qualifications for notice {notice_id}")
        
        # [중요] 프론트엔드 호환성을 위해 응답은 dict(JSON 객체) 원본을 반환
        return {"notice_id": notice_id, "ai_extracted_json": qual_json_dict}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI extraction (structured) API error for {notice_id}: {e}", exc_info=True)
        # exc_info=True를 추가하면 "can't adapt type 'dict'" 같은 상세 오류가 로그에 남습니다.
        raise HTTPException(status_code=500, detail=f"AI processing error: {e}")


# 6. [신규] 적합도 비교 API - [수정됨: DB 재조회 대신 클라이언트 데이터 사용]
@router.post("/api/compare-notice")
async def api_compare_notice(payload: dict):
    """(기능 3) 클라이언트가 보낸 notice_json과 가상 프로필을 받아 비교 로직 실행"""
    
    # [수정] notice_id 대신 notice_json 객체를 받음
    notice_json_from_client = payload.get("notice_json")
    user_profile = payload.get("user_profile")

    if not notice_json_from_client or not user_profile:
        raise HTTPException(status_code=400, detail="notice_json and user_profile required")

    try:
        # [수정] DB를 다시 조회하는 대신, 클라이언트가 보낸 데이터를 신뢰
        
        # [데이터 정제]
        notice_payload_for_comparison = dict(notice_json_from_client)
        
        # 클라이언트가 보낸 데이터에는 'ai_extracted_json' 키에
        # 이미 파싱된 dict가 들어있음
        ai_data_dict = notice_json_from_client.get('ai_extracted_json') 

        # 파싱된 dict의 키들('qualifications' 등)을 최상위로 병합
        if ai_data_dict and isinstance(ai_data_dict, dict):
            notice_payload_for_comparison.update(ai_data_dict)
        
        # comparison_logic은 'qualifications', 'end_at_ai' 등을 모두 포함한
        # notice_payload_for_comparison 딕셔너리를 입력받음
        comparison_result = check_suitability(user_profile, notice_payload_for_comparison)
        
        return comparison_result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comparison logic API error for {notice_json_from_client.get('id')}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Comparison processing error: {e}")