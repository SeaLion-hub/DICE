# auth_routes.py
import logging
from typing import Any, Dict, Iterable, List

import psycopg2
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
    ALLOWED_PROFILE_KEYWORDS,
)
from auth_security import hash_password, verify_password, create_access_token
from auth_deps import get_current_user
from db_pool import get_conn

PROFILE_SCHEMA_PATCH_SQL = [
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        gender TEXT,
        age INT,
        major TEXT,
        college TEXT,
        grade INT,
        keywords TEXT[] DEFAULT ARRAY[]::text[],
        military_service TEXT,
        income_bracket INT,
        gpa NUMERIC(3,2),
        language_scores JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS college TEXT;",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS keywords TEXT[] DEFAULT ARRAY[]::text[];",
    "ALTER TABLE user_profiles ALTER COLUMN keywords SET DEFAULT ARRAY[]::text[];",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS military_service TEXT;",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS income_bracket INT;",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS gpa NUMERIC(3,2);",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS language_scores JSONB DEFAULT '{}'::jsonb;",
    "ALTER TABLE user_profiles ALTER COLUMN language_scores SET DEFAULT '{}'::jsonb;",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();",
]

_profile_schema_verified = False

_KEYWORD_WHITELIST_VALUES = tuple(dict.fromkeys(ALLOWED_PROFILE_KEYWORDS))
_KEYWORD_WHITELIST_LITERAL = ", ".join(f"'{kw}'" for kw in _KEYWORD_WHITELIST_VALUES)


def _filter_allowed_keywords(keywords: Iterable[str]) -> List[str]:
    unique: List[str] = []
    allowed = set(ALLOWED_PROFILE_KEYWORDS)
    for kw in keywords:
        if not kw:
            continue
        if kw not in allowed:
            continue
        if kw not in unique:
            unique.append(kw)
    return unique


def ensure_user_profile_schema(conn) -> None:
    global _profile_schema_verified
    if _profile_schema_verified:
        return
    try:
        with conn.cursor() as cur:
            for statement in PROFILE_SCHEMA_PATCH_SQL:
                cur.execute(statement)
            if _KEYWORD_WHITELIST_LITERAL:
                cur.execute(
                    "ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS chk_user_profiles_keywords_whitelist;"
                )
                cur.execute(
                    f"""
                    ALTER TABLE user_profiles
                        ADD CONSTRAINT chk_user_profiles_keywords_whitelist
                        CHECK (
                            keywords <@ ARRAY[{_KEYWORD_WHITELIST_LITERAL}]::text[]
                        );
                    """
                )
        conn.commit()
        _profile_schema_verified = True
    except Exception as schema_err:
        logger.error(f"Failed to verify user_profiles schema: {schema_err}")
        conn.rollback()


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
            ensure_user_profile_schema(conn)
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
            ensure_user_profile_schema(conn)
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
            ensure_user_profile_schema(conn)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        user_id,
                        gender,
                        age,
                        major,
                        college,
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
                    college=profile["college"],
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
    keywords = _filter_allowed_keywords(req.keywords or [])
    if not keywords:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="키워드를 최소 1개 이상 선택해주세요.",
        )
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
                    cur.execute(
                        """
                        SELECT
                            gender,
                            age,
                            major,
                            college,
                            grade,
                            keywords,
                            military_service,
                            income_bracket,
                            gpa,
                            language_scores
                        FROM user_profiles
                        WHERE user_id = %s
                        """,
                        (user_id,),
                    )
                    current_profile = cur.fetchone() or {}

                    merged_keywords = req.keywords if req.keywords else (current_profile.get("keywords") or [])
                    merged_keywords = _filter_allowed_keywords(merged_keywords)
                    if not merged_keywords:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="키워드를 최소 1개 이상 선택해주세요.",
                        )
                    merged_lang_scores = req.language_scores if req.language_scores is not None else current_profile.get("language_scores")

                    # 2-A. 업데이트 (9개 필드 모두 반영)
                    cur.execute(
                        """
                        UPDATE user_profiles
                        SET
                            gender           = %s,
                            age              = %s,
                            major            = %s,
                            college          = %s,
                            grade            = %s,
                            keywords         = %s,
                            military_service = %s,
                            income_bracket   = %s,
                            gpa              = %s,
                            language_scores  = %s,
                            updated_at       = now()
                        WHERE user_id = %s
                        RETURNING
                            user_id, gender, age, major, college, grade, keywords,
                            military_service, income_bracket, gpa, language_scores,
                            created_at, updated_at
                        """,
                        (
                            req.gender,
                            req.age,
                            req.major,
                            req.college,
                            req.grade,
                            merged_keywords,
                            req.military_service,
                            req.income_bracket,
                            req.gpa,
                            Json(merged_lang_scores or {}),
                            user_id,
                        )
                    )
                else:
                    # 2-B. 생성 (9개 필드 모두 반영)
                    cur.execute(
                        """
                        INSERT INTO user_profiles (
                            user_id, gender, age, major, college, grade, keywords,
                            military_service, income_bracket, gpa, language_scores
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING
                            user_id, gender, age, major, college, grade, keywords,
                            military_service, income_bracket, gpa, language_scores,
                            created_at, updated_at
                        """,
                        (
                            user_id,
                            req.gender,
                            req.age,
                            req.major,
                            req.college,
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
                    college=profile.get("college"),
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
    except psycopg2.Error as db_err:
        conn.rollback()
        detail = getattr(getattr(db_err, "diag", None), "message_detail", None) or str(db_err)
        logger.error(f"Profile update failed: {db_err}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail or "Database error"
        )
    except Exception as e:
        logger.error(f"Database error in update_profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )
