"""
auth_schemas.py
모든 인증 및 프로필 관련 Pydantic 스키마 정의
- 회원가입/로그인 요청 검증
- 사용자 정보 응답 직렬화
- 프로필 생성/수정 요청 검증 및 응답 (v2: 필수/선택 항목 수정, toeic→language_scores 통합)
"""

from datetime import datetime
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, EmailStr, field_validator, ConfigDict


# ============================================================================
# 공통 상수
# ============================================================================

# 허용되는 키워드 목록: 대분류 + 소분류 전체
# 허용되는 키워드 목록: 대분류 + 소분류 전체
_ALLOWED_KEYWORDS = [
    # 대분류 (프론트와 동기화)
    '#학사', '#장학', '#취업', '#행사', '#공모전/대회', '#국제교류', '#일반',

    # 학사 소분류
    '#소속변경', '#ABEEK', '#신입생', '#S/U', '#교직과정', '#휴학', '#복학', '#수강신청', '#졸업', '#등록금', '#교과목', '#전공과목', '#다전공', '#기타',

    # 장학 소분류
    '#가계곤란', '#국가장학', '#근로장학', '#성적우수', '#생활비', '#기타',

    # 취업 소분류
    '#채용', '#인턴십', '#현장실습', '#강사', '#조교', '#채용설명회', '#취업특강', '#창업', '#기타',

    # 행사 소분류
    '#특강', '#워크숍', '#세미나', '#설명회', '#포럼', '#지원', '#교육', '#프로그램', '#기타',

    # 공모전/대회 소분류
    '#공모전', '#경진대회', '#디자인', '#숏폼', '#영상', '#아이디어', '#논문', '#학생설계전공', '#마이크로전공', '#기타',

    # 국제교류 소분류
    '#교환학생', '#파견', '#campusasia', '#글로벌', '#단기', '#하계', '#동계', '#어학연수', '#해외봉사', '#일본', '#미국'
]

# DB ENUM과 맞춘 리터럴 타입
GenderType = Literal['male', 'female', 'prefer_not_to_say']
MilitaryServiceType = Literal['completed', 'pending', 'exempt', 'n/a']


# ============================================================================
# 1) 회원가입 요청
# ============================================================================
class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: EmailStr = Field(..., description="사용자 이메일")
    password: str = Field(..., min_length=8, description="비밀번호 (최소 8자)")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        return v


# ============================================================================
# 2) 로그인 요청
# ============================================================================
class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: EmailStr = Field(..., description="사용자 이메일")
    password: str = Field(..., min_length=8, description="비밀번호")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        return v


# ============================================================================
# 3) 인증 토큰 응답
# ============================================================================
class AuthTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(..., description="JWT access token")
    token_type: Literal["bearer"] = "bearer"


# ============================================================================
# 4) 사용자 정보 응답 (내 정보 조회)
# ============================================================================
class UserMeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="사용자 UUID")
    email: EmailStr = Field(..., description="사용자 이메일")
    created_at: datetime = Field(..., description="계정 생성 시각")


# ============================================================================
# 5) 사용자 프로필 요청 (생성/수정) [수정됨]
# ============================================================================
class UserProfileRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # 필수 5개
    gender: GenderType = Field(..., description="성별 ('male' | 'female' | 'prefer_not_to_say')")
    age: int = Field(..., description="나이 (15~100)")
    major: str = Field(..., description="전공명")
    college: str | None = Field(None, description="단과대 (선택)")
    grade: int = Field(..., description="학년 (1~6)")
    keywords: List[str] = Field(..., min_length=1, description="관심 카테고리 해시태그 (min 1)")

    # 선택 4개
    military_service: Optional[MilitaryServiceType] = Field(None, description="병역 여부 (선택)")
    income_bracket: Optional[int] = Field(None, description="소득 분위 (0~10, 선택)")
    gpa: Optional[float] = Field(None, description="학점 (0.00~4.50, 선택)")
    language_scores: Optional[Dict[str, Any]] = Field(None, description="어학 점수(JSON), 예: {'toeic': 900, 'jlpt':'N2'}")

    # Validators
    @field_validator("age")
    @classmethod
    def check_age(cls, v: int) -> int:
        if not (15 <= v <= 100):
            raise ValueError("나이는 15~100 사이여야 합니다.")
        return v

    @field_validator("grade")
    @classmethod
    def check_grade(cls, v: int) -> int:
        if not (1 <= v <= 6):
            raise ValueError("학년은 1~6 사이여야 합니다.")
        return v

    @field_validator("income_bracket")
    @classmethod
    def check_income_bracket(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if not (0 <= v <= 10):
            raise ValueError("소득 분위는 0~10 사이여야 합니다.")
        return v

    @field_validator("gpa")
    @classmethod
    def check_gpa(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not (0.0 <= v <= 4.5):
            raise ValueError("GPA는 0.00~4.50 사이여야 합니다.")
        return round(v, 2)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, v: List[str]) -> List[str]:
        # 1) 비어있지 않음(필드 min_length=1로도 보장)
        if not v:
            raise ValueError("키워드는 최소 1개 이상이어야 합니다.")

        unique: List[str] = []
        for kw in v:
            kw = (kw or "").strip()
            if not kw:
                continue
            if not kw.startswith("#"):
                raise ValueError(f"키워드는 '#'로 시작해야 합니다: '{kw}'")
            if kw not in _ALLOWED_KEYWORDS:
                raise ValueError(f"허용되지 않은 키워드: '{kw}'")
            if kw not in unique:
                unique.append(kw)

        if not unique:
            raise ValueError("유효한 키워드를 1개 이상 선택해야 합니다.")
        return unique


# ============================================================================
# 6) 사용자 프로필 응답 (조회) [수정됨]
# ============================================================================
class UserProfileResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str = Field(..., description="사용자 UUID")

    # 필수 5개
    gender: GenderType = Field(..., description="성별")
    age: int = Field(..., description="나이")
    major: str = Field(..., description="전공명")
    college: str | None = Field(None, description="단과대")
    grade: int = Field(..., description="학년")
    keywords: List[str] = Field(..., description="관심 카테고리 해시태그")

    # 선택 4개
    military_service: Optional[MilitaryServiceType] = Field(None, description="병역 여부")
    income_bracket: Optional[int] = Field(None, description="소득 분위")
    gpa: Optional[float] = Field(None, description="학점")
    language_scores: Optional[Dict[str, Any]] = Field(None, description="어학 점수(JSON)")

    created_at: datetime = Field(..., description="프로필 생성 시각")
    updated_at: datetime = Field(..., description="마지막 수정 시각")
