# admin_routes.py
import logging
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor
from db_pool import get_conn
import os # FileResponse 경로용

# 수정된 ai_processor에서 새 함수 임포트
try:
    from ai_processor import extract_detailed_hashtags
except ImportError:
    # 임시방편 (실제로는 ai_processor.py에 있어야 함)
    def extract_detailed_hashtags(body_text: str, main_category: str) -> list[str]:
        logging.warning("Using mock extract_detailed_hashtags function!")
        if main_category == "#취업": return ["#임시_채용", "#임시_조교"]
        return ["#임시태그"]

logger = logging.getLogger("dice-api.admin")
router = APIRouter(prefix="/admin", tags=["admin"])

# admin.html 파일의 경로 설정 (main.py 기준)
# 이 파일(admin_routes.py)이 있는 디렉토리를 기준으로 admin.html을 찾습니다.
current_dir = os.path.dirname(os.path.abspath(__file__))
ADMIN_HTML_PATH = os.path.join(current_dir, "admin.html")


# 1. Admin HTML 페이지 서빙
@router.get("/dashboard", response_class=FileResponse)
async def get_admin_dashboard():
    """관리자용 세부 해시태그 추출 대시보드 HTML을 반환합니다."""
    if not os.path.exists(ADMIN_HTML_PATH):
        logger.error(f"Admin HTML file not found at: {ADMIN_HTML_PATH}")
        raise HTTPException(status_code=404, detail="Admin dashboard HTML not found.")
    return FileResponse(ADMIN_HTML_PATH)


# 2. 공지사항 목록 API (Admin용) - [수정됨: detailed_hashtags 조회]
@router.get("/api/notices")
async def get_notices_for_admin(limit: int = 100, offset: int = 0):
    """관리자 페이지용 공지사항 목록 (단과대명, 제목, URL, 대분류, 세부태그)"""
    # #일반이 아닌, 세부 추출이 의미 있는 공지사항만 조회
    query = """
    SELECT 
        n.id,
        COALESCE(c.name, 'N/A') as college_name,
        n.title,
        n.url,
        n.category_ai,
        n.detailed_hashtags  -- [수정] 기존에 저장된 세부 해시태그 조회
    FROM notices n
    LEFT JOIN colleges c ON n.college_key = c.key
    WHERE n.category_ai IS NOT NULL AND n.category_ai != '#일반'
    ORDER BY n.created_at DESC
    LIMIT %s OFFSET %s;
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (limit, offset))
                notices = cur.fetchall()
        return {"items": notices}
    except Exception as e:
        logger.error(f"Admin API Error fetching notices: {e}")
        raise HTTPException(status_code=500, detail="Database error")


# 3. 세부 해시태그 추출 및 저장 API - [수정됨: DB 저장 로직 추가]
@router.post("/api/extract-detailed-hashtags")
async def api_extract_detailed_hashtags(payload: dict):
    """공지 ID와 대분류를 받아 세부 해시태그를 AI로 추출하고 DB에 저장"""
    notice_id = payload.get("notice_id")
    main_category = payload.get("main_category")

    if not notice_id or not main_category:
        raise HTTPException(status_code=400, detail="notice_id and main_category required")

    try:
        body_text = ""
        # 1. DB에서 body_text 가져오기 (커넥션 분리)
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT body_text FROM notices WHERE id = %s", (notice_id,))
                row = cur.fetchone()
        
        if not row or not row.get("body_text"):
            raise HTTPException(status_code=404, detail="Notice body not found or empty")
        
        body_text = row["body_text"]

        # 2. AI 함수 호출 (시간이 걸릴 수 있음)
        detailed_hashtags = extract_detailed_hashtags(body_text, main_category)
        
        # 3. [신규] AI 결과를 DB에 저장
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE notices 
                    SET detailed_hashtags = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (detailed_hashtags, notice_id)
                )
                conn.commit() # 변경 사항 저장

        logger.info(f"Admin: Successfully extracted and saved {len(detailed_hashtags)} detailed tags for notice {notice_id}")
        
        return {"notice_id": notice_id, "detailed_hashtags": detailed_hashtags}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI extraction API error for {notice_id}: {e}")
        raise HTTPException(status_code=500, detail=f"AI processing error: {e}")