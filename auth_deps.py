
from typing import Any, Dict

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import HTTPException, Request, status
import jwt
import os
from auth_security import decode_token


# ============================================================================
# 현재 사용자 조회 의존성
# ============================================================================

def get_current_user(request: Request) -> dict:
    """
    Authorization 헤더의 Bearer 토큰을 검증하고, DB에서 사용자 정보를 반환
    
    Args:
        request: FastAPI Request 객체
    
    Returns:
        사용자 정보 딕셔너리 {"id": <uuid>, "email": <str>, "created_at": <datetime>}
    
    Raises:
        HTTPException(401): 인증 실패 (토큰 없음/만료/위조, 사용자 없음)
        HTTPException(500): 서버 오류 (DB 설정 오류, 쿼리 실패)
    """
    
    # ========================================================================
    # 1. Authorization 헤더 추출
    # ========================================================================
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    # ========================================================================
    # 2. Bearer 토큰 분리
    # ========================================================================
    parts = auth_header.strip().split()
    
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    token = parts[1]
    
    # ========================================================================
    # 3. JWT 토큰 검증
    # ========================================================================
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    # ========================================================================
    # 4. 사용자 ID 추출
    # ========================================================================
    user_id = payload.get("sub")
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    # ========================================================================
    # 5. 데이터베이스 설정 확인
    # ========================================================================
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database not configured"
        )
    
    # ========================================================================
    # 6. DB에서 사용자 조회
    # ========================================================================
    try:
        with psycopg2.connect(database_url) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, email, created_at
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id,)
                )
                
                user = cur.fetchone()
                
                if not user:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="User not found"
                    )
                
                return dict(user)
    
    except HTTPException:
        # HTTPException은 그대로 전파
        raise
    except Exception as e:
        # DB 연결/쿼리 오류
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )


# ============================================================================
# 간단 테스트 (주석 해제하여 실행 가능)
# ============================================================================

# if __name__ == "__main__":
#     import os
#     os.environ["JWT_SECRET"] = "test-secret-key"
#     os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost/dbname"
#     
#     from auth_security import create_access_token
#     
#     # 가짜 Request 객체 생성
#     def _fake_request(header_value):
#         class FakeRequest:
#             def __init__(self, auth_header):
#                 self.headers = {"Authorization": auth_header}
#         return FakeRequest(header_value)
#     
#     # 테스트 1: 토큰 없음
#     print("=== Test 1: No Token ===")
#     try:
#         get_current_user(_fake_request(""))
#     except HTTPException as e:
#         print(f"Status: {e.status_code}, Detail: {e.detail}")
#     
#     # 테스트 2: 잘못된 형식
#     print("\n=== Test 2: Invalid Format ===")
#     try:
#         get_current_user(_fake_request("Token xxx.yyy.zzz"))
#     except HTTPException as e:
#         print(f"Status: {e.status_code}, Detail: {e.detail}")
#     
#     # 테스트 3: 만료된 토큰
#     print("\n=== Test 3: Expired Token ===")
#     expired_token = create_access_token("test-user-id", expires_min=0)
#     import time
#     time.sleep(1)
#     try:
#         get_current_user(_fake_request(f"Bearer {expired_token}"))
#     except HTTPException as e:
#         print(f"Status: {e.status_code}, Detail: {e.detail}")
#     
#     # 테스트 4: 유효한 토큰 (DB 연결 필요)
#     print("\n=== Test 4: Valid Token ===")
#     valid_token = create_access_token("00000000-0000-0000-0000-000000000000")
#     try:
#         user = get_current_user(_fake_request(f"Bearer {valid_token}"))
#         print(f"User: {user}")
#     except HTTPException as e:
#         print(f"Status: {e.status_code}, Detail: {e.detail}")
#     
#     # 테스트 5: 대소문자 무시 (bearer)
#     print("\n=== Test 5: Case Insensitive Bearer ===")
#     try:
#         user = get_current_user(_fake_request(f"bearer {valid_token}"))
#         print(f"User: {user}")
#     except HTTPException as e:
#         print(f"Status: {e.status_code}, Detail: {e.detail}")