"""
auth_security.py
비밀번호 해시/검증 및 JWT 토큰 발급/검증 유틸리티

환경변수:
- JWT_SECRET (필수): JWT 서명용 시크릿 키
- JWT_EXPIRES_MIN (선택, 기본 1440): 토큰 만료 시간(분)
"""

import os
import time
from typing import Any, Dict, Optional

import bcrypt
import jwt


# ============================================================================
# 내부 헬퍼 함수
# ============================================================================

def _get_secret() -> str:
    """JWT_SECRET 환경변수 로드 (없으면 RuntimeError)"""
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT secret missing")
    return secret


def _now() -> int:
    """현재 UTC epoch seconds"""
    return int(time.time())


def _parse_bearer(token_or_header: str) -> str:
    """
    'Bearer XXX' 형태에서 토큰만 추출
    그 외 경우 원본 반환
    """
    parts = token_or_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return token_or_header


# ============================================================================
# 1) 비밀번호 해시 생성
# ============================================================================

def hash_password(plain: str) -> str:
    """
    평문 비밀번호를 bcrypt로 해시
    
    Args:
        plain: 평문 비밀번호
    
    Returns:
        UTF-8 인코딩된 bcrypt 해시 문자열
    
    Raises:
        ValueError: 해시 생성 실패 시
    """
    try:
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(plain.encode("utf-8"), salt)
        return hashed.decode("utf-8")
    except Exception as e:
        raise ValueError("Password hashing failed") from e


# ============================================================================
# 2) 비밀번호 검증
# ============================================================================

def verify_password(plain: str, hashed: str) -> bool:
    """
    평문 비밀번호와 해시를 비교
    
    Args:
        plain: 평문 비밀번호
        hashed: bcrypt 해시 문자열
    
    Returns:
        일치 여부 (예외 발생 시 False 반환)
    """
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            hashed.encode("utf-8")
        )
    except Exception:
        return False


# ============================================================================
# 3) JWT 액세스 토큰 생성
# ============================================================================

def create_access_token(
    user_id: str,
    expires_min: Optional[int] = None
) -> str:
    """
    JWT 액세스 토큰 생성
    
    Args:
        user_id: 사용자 UUID (토큰의 sub 클레임)
        expires_min: 만료 시간(분), None이면 환경변수 사용
    
    Returns:
        서명된 JWT 토큰 문자열
    
    Raises:
        RuntimeError: JWT_SECRET 환경변수 미설정 시
    """
    secret = _get_secret()
    
    # 만료 시간 설정
    if expires_min is None:
        expires_min = int(os.getenv("JWT_EXPIRES_MIN", "1440"))
    
    # 페이로드 구성
    now = _now()
    payload = {
        "sub": user_id,           # Subject (사용자 ID)
        "typ": "access",          # Token type
        "iat": now,               # Issued at
        "exp": now + (expires_min * 60)  # Expiration
    }
    
    # 토큰 생성
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token


# ============================================================================
# 4) JWT 토큰 검증 및 디코드
# ============================================================================

def decode_token(token: str) -> Dict[str, Any]:
    """
    JWT 토큰 검증 및 디코드
    
    Args:
        token: JWT 토큰 문자열 (또는 'Bearer XXX' 형태)
    
    Returns:
        디코드된 페이로드 딕셔너리 (sub, typ, iat, exp 포함)
    
    Raises:
        jwt.ExpiredSignatureError: 토큰 만료 시
        jwt.InvalidTokenError: 토큰 검증 실패 시
        RuntimeError: JWT_SECRET 환경변수 미설정 시
    """
    secret = _get_secret()
    
    # Bearer 접두사 제거
    token = _parse_bearer(token)
    
    # 토큰 디코드 (예외는 그대로 전파)
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    return payload


# ============================================================================
# 자체 테스트 (주석 해제하여 실행 가능)
# ============================================================================

# if __name__ == "__main__":
#     import os
#     os.environ["JWT_SECRET"] = "test-secret-key-do-not-use-in-production"
#     
#     # 1. 비밀번호 해시/검증 테스트
#     print("=== Password Hashing Test ===")
#     password = "Passw0rd!"
#     hashed = hash_password(password)
#     print(f"Password: {password}")
#     print(f"Hashed: {hashed}")
#     print(f"Verify correct: {verify_password(password, hashed)}")
#     print(f"Verify wrong: {verify_password('wrong', hashed)}")
#     
#     # 2. JWT 토큰 생성/검증 테스트
#     print("\n=== JWT Token Test ===")
#     user_id = "00000000-0000-0000-0000-000000000000"
#     token = create_access_token(user_id, expires_min=60)
#     print(f"Token: {token}")
#     
#     decoded = decode_token(token)
#     print(f"Decoded: {decoded}")
#     print(f"Subject matches: {decoded['sub'] == user_id}")
#     print(f"Token type: {decoded['typ']}")
#     
#     # 3. Bearer 토큰 파싱 테스트 (대소문자 무시)
#     print("\n=== Bearer Token Parsing Test ===")
#     bearer_token = f"Bearer {token}"
#     bearer_lower = f"bearer {token}"
#     decoded_bearer = decode_token(bearer_token)
#     decoded_lower = decode_token(bearer_lower)
#     print(f"Bearer token decoded: {decoded_bearer['sub'] == user_id}")
#     print(f"bearer (lowercase) decoded: {decoded_lower['sub'] == user_id}")
#     
#     # 4. 만료된 토큰 테스트
#     print("\n=== Expired Token Test ===")
#     expired_token = create_access_token(user_id, expires_min=0)
#     import time
#     time.sleep(1)
#     try:
#         decode_token(expired_token)
#         print("ERROR: Should have raised ExpiredSignatureError")
#     except jwt.ExpiredSignatureError:
#         print("Expired token correctly rejected")
#     
#     # 5. 잘못된 토큰 테스트
#     print("\n=== Invalid Token Test ===")
#     try:
#         decode_token("invalid.token.here")
#         print("ERROR: Should have raised InvalidTokenError")
#     except jwt.InvalidTokenError:
#         print("Invalid token correctly rejected")
#     
#     # 6. JWT_SECRET 미설정 테스트
#     print("\n=== Missing Secret Test ===")
#     del os.environ["JWT_SECRET"]
#     try:
#         create_access_token(user_id)
#         print("ERROR: Should have raised RuntimeError")
#     except RuntimeError as e:
#         print(f"Secret missing error: {e}")