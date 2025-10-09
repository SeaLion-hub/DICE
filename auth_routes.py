
import os
from typing import Any, Dict

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import errors as pg_errors
from fastapi import APIRouter, Depends, HTTPException, status

from auth_schemas import (
    RegisterRequest,
    LoginRequest,
    AuthTokenResponse,
    UserMeResponse,
    UserProfileRequest,
    UserProfileResponse,
)
from auth_security import hash_password, verify_password, create_access_token
from auth_deps import get_current_user


# ============================================================================
# 라우터 설정
# ============================================================================
router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================================================
# 공통 헬퍼 함수
# ============================================================================

def _require_db_url() -> str:
    """DATABASE_URL 환경변수 확인 (없으면 500 에러)"""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database not configured"
        )
    return url


def _norm_email(email: str) -> str:
    """이메일 정규화 (공백 제거 + 소문자)"""
    return (email or "").strip().lower()


# ============================================================================
# 1) 회원가입 POST /auth/register
# ============================================================================

@router.post("/register", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest):
    """
    회원가입
    
    - 이메일 중복 체크
    - 비밀번호 bcrypt 해시
    - JWT 토큰 발급
    """
    db_url = _require_db_url()
    email = _norm_email(req.email)
    pw_hash = hash_password(req.password)
    
    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. 중복 체크
                cur.execute(
                    "SELECT 1 FROM users WHERE lower(email) = lower(%s)",
                    (email,)
                )
                if cur.fetchone():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Email already registered"
                    )
                
                # 2. 사용자 생성
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash)
                    VALUES (%s, %s)
                    RETURNING id, created_at
                    """,
                    (email, pw_hash)
                )
                user = cur.fetchone()
                user_id = str(user["id"])
                
                # 3. JWT 토큰 발급
                token = create_access_token(user_id)
                
                return AuthTokenResponse(access_token=token, token_type="bearer")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )


# ============================================================================
# 2) 로그인 POST /auth/login
# ============================================================================

@router.post("/login", response_model=AuthTokenResponse)
async def login(req: LoginRequest):
    """
    로그인
    
    - 이메일/비밀번호 검증
    - JWT 토큰 발급
    """
    db_url = _require_db_url()
    email = _norm_email(req.email)
    
    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. 사용자 조회
                cur.execute(
                    """
                    SELECT id, password_hash
                    FROM users
                    WHERE lower(email) = lower(%s)
                    """,
                    (email,)
                )
                user = cur.fetchone()
                
                # 2. 사용자 없음 또는 비밀번호 불일치
                if not user or not verify_password(req.password, user["password_hash"]):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="이메일 또는 비밀번호가 올바르지 않습니다"
                    )
                
                # 3. JWT 토큰 발급
                user_id = str(user["id"])
                token = create_access_token(user_id)
                
                return AuthTokenResponse(access_token=token, token_type="bearer")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )


# ============================================================================
# 3) 내 정보 조회 GET /auth/me
# ============================================================================

@router.get("/me", response_model=UserMeResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    현재 로그인한 사용자 정보 조회
    
    - Authorization 헤더 필수
    """
    return UserMeResponse(
        id=str(current_user["id"]),
        email=current_user["email"],
        created_at=current_user["created_at"]
    )


# ============================================================================
# 4) 프로필 조회 GET /auth/me/profile
# ============================================================================

@router.get("/me/profile", response_model=UserProfileResponse)
async def get_profile(current_user: dict = Depends(get_current_user)):
    """
    현재 사용자의 프로필 조회
    
    - Authorization 헤더 필수
    - 프로필이 없으면 404
    """
    db_url = _require_db_url()
    user_id = str(current_user["id"])
    
    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT user_id, grade, major, gpa, toeic, keywords,
                           created_at, updated_at
                    FROM user_profiles
                    WHERE user_id = %s
                    """,
                    (user_id,)
                )
                profile = cur.fetchone()
                
                if not profile:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Profile not found"
                    )
                
                return UserProfileResponse(
                    user_id=str(profile["user_id"]),
                    grade=profile["grade"],
                    major=profile["major"],
                    gpa=profile["gpa"],
                    toeic=profile["toeic"],
                    keywords=profile["keywords"] or [],
                    created_at=profile["created_at"],
                    updated_at=profile["updated_at"]
                )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )


# ============================================================================
# 5) 프로필 생성/수정 PUT /auth/me/profile
# ============================================================================

@router.put("/me/profile", response_model=UserProfileResponse)
async def update_profile(
    req: UserProfileRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    현재 사용자의 프로필 생성 또는 수정 (UPSERT)
    
    - Authorization 헤더 필수
    - 프로필이 없으면 생성, 있으면 수정
    """
    db_url = _require_db_url()
    user_id = str(current_user["id"])
    
    # 키워드 정규화 (None → 빈 배열)
    keywords = req.keywords or []
    
    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. 기존 프로필 존재 여부 확인
                cur.execute(
                    "SELECT 1 FROM user_profiles WHERE user_id = %s",
                    (user_id,)
                )
                exists = cur.fetchone()
                
                if exists:
                    # 2-A. 업데이트
                    cur.execute(
                        """
                        UPDATE user_profiles
                        SET grade = %s,
                            major = %s,
                            gpa = %s,
                            toeic = %s,
                            keywords = %s,
                            updated_at = now()
                        WHERE user_id = %s
                        RETURNING user_id, grade, major, gpa, toeic, keywords,
                                  created_at, updated_at
                        """,
                        (req.grade, req.major, req.gpa, req.toeic, keywords, user_id)
                    )
                else:
                    # 2-B. 생성
                    cur.execute(
                        """
                        INSERT INTO user_profiles
                            (user_id, grade, major, gpa, toeic, keywords)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING user_id, grade, major, gpa, toeic, keywords,
                                  created_at, updated_at
                        """,
                        (user_id, req.grade, req.major, req.gpa, req.toeic, keywords)
                    )
                
                profile = cur.fetchone()
                
                return UserProfileResponse(
                    user_id=str(profile["user_id"]),
                    grade=profile["grade"],
                    major=profile["major"],
                    gpa=profile["gpa"],
                    toeic=profile["toeic"],
                    keywords=profile["keywords"] or [],
                    created_at=profile["created_at"],
                    updated_at=profile["updated_at"]
                )
    
    except pg_errors.CheckViolation as e:
        # CHECK 제약 위반 (키워드 화이트리스트, 범위 등)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e).split('DETAIL:')[-1].strip() if 'DETAIL:' in str(e) else str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )