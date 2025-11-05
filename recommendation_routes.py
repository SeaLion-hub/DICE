# DICE-auth_updated/recommendation_routes.py
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any

from db_pool import get_conn
from auth_deps import get_current_user  # 인증 의존성

router = APIRouter()
logger = logging.getLogger("dice-api.recommendations")

# 사용자의 프로필 키워드와 공지사항의 세부 해시태그를 비교하는 SQL
RECOMMENDED_NOTICES_SQL = """
SELECT
    n.id,
    n.college_key,
    n.title,
    n.url,
    n.category_ai,
    n.hashtags_ai,
    n.detailed_hashtags,
    n.published_at,
    n.created_at
FROM
    notices AS n
JOIN
    user_profiles AS up ON up.user_id = %(user_id)s
WHERE
    -- GIN 인덱스를 사용하는 배열 교집합 연산자 (&&)
    -- 1개라도 겹치는 항목이 있으면 TRUE
    n.detailed_hashtags && up.keywords
ORDER BY
    n.published_at DESC NULLS LAST, n.created_at DESC
LIMIT %(limit)s OFFSET %(offset)s;
"""

# 추천 공지사항의 총 개수를 계산하는 SQL
RECOMMENDED_COUNT_SQL = """
SELECT COUNT(n.id) AS total
FROM notices AS n
JOIN user_profiles AS up ON up.user_id = %(user_id)s
WHERE n.detailed_hashtags && up.keywords;
"""

@router.get("/notices/recommended",
            summary="추천 공지사항 조회 (프로필 키워드 기반)",
            tags=["notices"]) # 'notices' 태그로 그룹화
def get_recommended_notices(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    count: bool = Query(True, description="전체 카운트 포함 여부"),
    user: dict = Depends(get_current_user) # 인증된 사용자 정보
):
    """
    현재 로그인한 사용자의 프로필(user_profiles.keywords)을 기준으로
    AI가 추출한 세부 해시태그(notices.detailed_hashtags)와 1개 이상
    일치하는 공지사항을 반환합니다.
    """
    user_id = user.get("sub")  # JWT의 'sub' 클레임에서 user_id (UUID)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user credentials")

    params = {"user_id": user_id, "limit": limit, "offset": offset}
    total_count = None
    items = []

    try:
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. 전체 카운트 조회 (요청 시)
            if count:
                cur.execute(RECOMMENDED_COUNT_SQL, {"user_id": user_id})
                result = cur.fetchone()
                total_count = result['total'] if result else 0

            # 2. 추천 공지 목록 조회
            cur.execute(RECOMMENDED_NOTICES_SQL, params)
            items = cur.fetchall()

            # (선택적 로깅) 사용자가 키워드를 설정했는지 여부 확인
            if total_count == 0:
                cur.execute("SELECT 1 FROM user_profiles WHERE user_id = %s AND cardinality(keywords) > 0", [user_id])
                if not cur.fetchone():
                    logger.info(f"User {user_id} has no keywords set. Returning empty recommendations.")
                else:
                    logger.info(f"User {user_id} has keywords, but no matching notices found.")

    except Exception as e:
        logger.error(f"DB error fetching recommended notices for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error fetching recommendations")

    response = {
        "meta": {
            "limit": limit,
            "offset": offset,
            "returned": len(items)
        },
        "items": items
    }
    if total_count is not None:
        response["meta"]["total_count"] = total_count

    return response