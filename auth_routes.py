# auth_routes.py
import logging
from typing import Any, Dict

from psycopg2.extras import RealDictCursor, Json  # Json 추가
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
from db_pool import get_conn


# ============================================================================
# 로거 설정
# ============================================================================
logger = logging.getLogger(__name__)


# ============================================================================
# 라우터 설정
# ============================================================================
router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================================================
# 공통 헬퍼 함수
# ============================================================================
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
    email = _norm_email(req.email)
    pw_hash = hash_password(req.password)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. 중복 체크 (레이스 컨디션 가능성 있음)
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

                # 쓰기 작업이므로 커밋
                conn.commit()

                # 3. JWT 토큰 발급
                token = create_access_token(user_id)

                return AuthTokenResponse(access_token=token, token_type="bearer")

    except pg_errors.UniqueViolation:
        # DB 레벨에서 중복이 걸린 경우도 409로 통일 (레이스 컨디션 대비)
        logger.info(f"Race condition detected for email registration: {email}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error in register: {e}")
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
    email = _norm_email(req.email)

    try:
        with get_conn() as conn:
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
        logger.error(f"Database error in login: {e}")
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
    user_id = str(current_user["id"])

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        user_id,
                        gender,
                        age,
                        major,
                        grade,
                        keywords,
                        military_service,
                        income_bracket,
                        gpa,
                        language_scores,
                        created_at,
                        updated_at
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
                    gender=profile["gender"],
                    age=profile["age"],
                    major=profile["major"],
                    grade=profile["grade"],
                    keywords=profile["keywords"] or [],
                    military_service=profile["military_service"],
                    income_bracket=profile["income_bracket"],
                    gpa=profile["gpa"],
                    language_scores=profile["language_scores"],
                    created_at=profile["created_at"],
                    updated_at=profile["updated_at"]
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error in get_profile: {e}")
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
    user_id = str(current_user["id"])

    # 정규화
    keywords = req.keywords or []
    lang_scores_json = Json(req.language_scores or {})  # JSONB 안전 저장

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. 기존 프로필 존재 여부 확인
                cur.execute(
                    "SELECT 1 FROM user_profiles WHERE user_id = %s",
                    (user_id,)
                )
                exists = cur.fetchone()

                if exists:
                    # 2-A. 업데이트 (9개 필드 모두 반영)
                    cur.execute(
                        """
                        UPDATE user_profiles
                        SET
                            gender           = %s,
                            age              = %s,
                            major            = %s,
                            grade            = %s,
                            keywords         = %s,
                            military_service = %s,
                            income_bracket   = %s,
                            gpa              = %s,
                            language_scores  = %s,
                            updated_at       = now()
                        WHERE user_id = %s
                        RETURNING
                            user_id, gender, age, major, grade, keywords,
                            military_service, income_bracket, gpa, language_scores,
                            created_at, updated_at
                        """,
                        (
                            req.gender,
                            req.age,
                            req.major,
                            req.grade,
                            keywords,
                            req.military_service,
                            req.income_bracket,
                            req.gpa,
                            lang_scores_json,
                            user_id,
                        )
                    )
                else:
                    # 2-B. 생성 (9개 필드 모두 반영)
                    cur.execute(
                        """
                        INSERT INTO user_profiles (
                            user_id, gender, age, major, grade, keywords,
                            military_service, income_bracket, gpa, language_scores
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING
                            user_id, gender, age, major, grade, keywords,
                            military_service, income_bracket, gpa, language_scores,
                            created_at, updated_at
                        """,
                        (
                            user_id,
                            req.gender,
                            req.age,
                            req.major,
                            req.grade,
                            keywords,
                            req.military_service,
                            req.income_bracket,
                            req.gpa,
                            lang_scores_json,
                        )
                    )

                profile = cur.fetchone()

                # 쓰기 작업이므로 커밋
                conn.commit()

                return UserProfileResponse(
                    user_id=str(profile["user_id"]),
                    gender=profile["gender"],
                    age=profile["age"],
                    major=profile["major"],
                    grade=profile["grade"],
                    keywords=profile["keywords"] or [],
                    military_service=profile["military_service"],
                    income_bracket=profile["income_bracket"],
                    gpa=profile["gpa"],
                    language_scores=profile["language_scores"],
                    created_at=profile["created_at"],
                    updated_at=profile["updated_at"]
                )

    except pg_errors.CheckViolation as e:
        # CHECK 제약 위반 (키워드/범위/JSON 등)
        logger.warning(f"Check constraint violation in update_profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e).split('DETAIL:')[-1].strip() if 'DETAIL:' in str(e) else str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error in update_profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )
