"""
auth_schemas.py
모든 인증 및 프로필 관련 Pydantic 스키마 정의
- 회원가입/로그인 요청 검증
- 사용자 정보 응답 직렬화
- 프로필 생성/수정 요청 검증 및 응답
"""

from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, EmailStr, field_validator, ConfigDict


# ============================================================================
# 공통 상수
# ============================================================================
_ALLOWED_KEYWORDS = ['학사', '장학', '행사', '취업', '국제교류', '공모전/대회', '일반']


# ============================================================================
# 1) 회원가입 요청
# ============================================================================
class RegisterRequest(BaseModel):
    """회원가입 요청 스키마"""
    model_config = ConfigDict(extra="ignore")
    
    email: EmailStr = Field(..., description="사용자 이메일")
    password: str = Field(..., min_length=8, description="비밀번호 (최소 8자)")
    
    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """비밀번호 검증: 공백 제거 후 최소 8자"""
        v = v.strip()
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        return v


# ============================================================================
# 2) 로그인 요청
# ============================================================================
class LoginRequest(BaseModel):
    """로그인 요청 스키마"""
    model_config = ConfigDict(extra="ignore")
    
    email: EmailStr = Field(..., description="사용자 이메일")
    password: str = Field(..., min_length=8, description="비밀번호")
    
    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """비밀번호 검증: 공백 제거 후 최소 8자"""
        v = v.strip()
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        return v


# ============================================================================
# 3) 인증 토큰 응답
# ============================================================================
class AuthTokenResponse(BaseModel):
    """로그인/회원가입 성공 시 반환되는 JWT 토큰"""
    model_config = ConfigDict(extra="ignore")
    
    access_token: str = Field(..., description="JWT access token")
    token_type: Literal["bearer"] = "bearer"


# ============================================================================
# 4) 사용자 정보 응답 (내 정보 조회)
# ============================================================================
class UserMeResponse(BaseModel):
    """현재 로그인한 사용자 정보"""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(..., description="사용자 UUID")
    email: EmailStr = Field(..., description="사용자 이메일")
    created_at: datetime = Field(..., description="계정 생성 시각")


# ============================================================================
# 5) 사용자 프로필 요청 (생성/수정)
# ============================================================================
class UserProfileRequest(BaseModel):
    """프로필 생성/수정 요청 스키마"""
    model_config = ConfigDict(extra="ignore")
    
    grade: Optional[int] = Field(None, description="학년 (1~6)")
    major: Optional[str] = Field(None, description="전공명")
    gpa: Optional[float] = Field(None, description="학점 (0.00~4.50)")
    toeic: Optional[int] = Field(None, description="TOEIC 점수 (0~990)")
    keywords: List[str] = Field(default_factory=list, description="관심 카테고리")
    
    @field_validator("grade")
    @classmethod
    def check_grade(cls, v: Optional[int]) -> Optional[int]:
        """학년 검증: 1~6 사이"""
        if v is None:
            return v
        if not (1 <= v <= 6):
            raise ValueError("학년은 1~6 사이여야 합니다.")
        return v
    
    @field_validator("gpa")
    @classmethod
    def check_gpa(cls, v: Optional[float]) -> Optional[float]:
        """GPA 검증: 0.00~4.50, 소수점 2자리"""
        if v is None:
            return v
        if not (0.0 <= v <= 4.5):
            raise ValueError("GPA는 0.00~4.50 사이여야 합니다.")
        return round(v, 2)
    
    @field_validator("toeic")
    @classmethod
    def check_toeic(cls, v: Optional[int]) -> Optional[int]:
        """TOEIC 점수 검증: 0~990"""
        if v is None:
            return v
        if not (0 <= v <= 990):
            raise ValueError("TOEIC 점수는 0~990 사이여야 합니다.")
        return v
    
    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, v: List[str]) -> List[str]:
        """
        키워드 검증:
        1. 화이트리스트 검사
        2. 중복 제거
        3. 빈 문자열 제거
        """
        if not v:
            return []
        
        unique_keywords = []
        for keyword in v:
            keyword = keyword.strip()
            
            # 빈 문자열 스킵
            if not keyword:
                continue
            
            # 화이트리스트 검증
            if keyword not in _ALLOWED_KEYWORDS:
                raise ValueError(
                    f"허용되지 않은 키워드: '{keyword}'. "
                    f"허용 목록: {', '.join(_ALLOWED_KEYWORDS)}"
                )
            
            # 중복 제거
            if keyword not in unique_keywords:
                unique_keywords.append(keyword)
        
        return unique_keywords


# ============================================================================
# 6) 사용자 프로필 응답 (조회)
# ============================================================================
class UserProfileResponse(BaseModel):
    """프로필 조회 응답 스키마"""
    model_config = ConfigDict(extra="ignore")
    
    user_id: str = Field(..., description="사용자 UUID")
    grade: Optional[int] = Field(None, description="학년 (1~6)")
    major: Optional[str] = Field(None, description="전공명")
    gpa: Optional[float] = Field(None, description="학점 (0.00~4.50)")
    toeic: Optional[int] = Field(None, description="TOEIC 점수 (0~990)")
    keywords: List[str] = Field(default_factory=list, description="관심 카테고리")
    created_at: datetime = Field(..., description="프로필 생성 시각")
    updated_at: datetime = Field(..., description="마지막 수정 시각")