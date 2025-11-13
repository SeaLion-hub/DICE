"""
validators.py - 입력 검증 및 검증 함수

Pydantic 모델과 커스텀 검증 함수를 제공합니다.
"""
import re
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime


class NoticeListQueryParams(BaseModel):
    """공지사항 목록 조회 쿼리 파라미터 검증"""
    college: Optional[str] = Field(None, description="단과대학 키")
    q: Optional[str] = Field(None, max_length=500, description="검색어 (최대 500자)")
    search_mode: str = Field("websearch", pattern="^(like|trgm|fts|websearch)$")
    op: str = Field("and", pattern="^(and|or)$")
    rank: Optional[str] = Field(None, pattern="^(off|trgm|fts)$")
    date_from: Optional[str] = Field(None, description="YYYY-MM-DD 형식")
    date_to: Optional[str] = Field(None, description="YYYY-MM-DD 형식")
    sort: str = Field("recent", pattern="^(recent|oldest)$")
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)
    my: bool = Field(False)
    count: bool = Field(True)
    no_cache: bool = Field(False)
    hashtags: List[str] = Field(default_factory=list)
    
    @field_validator("q")
    @classmethod
    def validate_search_query(cls, v: Optional[str]) -> Optional[str]:
        """검색어 검증"""
        if v is None:
            return v
        
        v = v.strip()
        
        # SQL 인젝션 시도 패턴 검사
        dangerous_patterns = [
            r"(\bUNION\b|\bSELECT\b|\bINSERT\b|\bDELETE\b|\bUPDATE\b|\bDROP\b)",
            r"(--|/\*|\*/|;|')",
            r"(\bor\b\s+\d+\s*=\s*\d+)",  # SQL injection: OR 1=1
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError("검색어에 허용되지 않는 문자가 포함되어 있습니다.")
        
        # 길이 제한
        if len(v) > 500:
            raise ValueError("검색어는 500자를 초과할 수 없습니다.")
        
        return v
    
    @field_validator("date_from", "date_to")
    @classmethod
    def validate_date_format(cls, v: Optional[str]) -> Optional[str]:
        """날짜 형식 검증"""
        if v is None:
            return v
        
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
        
        return v
    
    @field_validator("hashtags")
    @classmethod
    def validate_hashtags(cls, v: List[str]) -> List[str]:
        """해시태그 검증"""
        if not isinstance(v, list):
            return []
        
        validated = []
        for tag in v:
            if isinstance(tag, str):
                tag = tag.strip()
                # 해시태그는 최대 50자, 특수문자 제한
                if len(tag) > 50:
                    continue
                if re.match(r"^[#\w가-힣\s-]+$", tag):
                    validated.append(tag)
        
        # 중복 제거
        return list(dict.fromkeys(validated))
    
    @field_validator("college")
    @classmethod
    def validate_college(cls, v: Optional[str]) -> Optional[str]:
        """단과대학 키 검증"""
        if v is None or v == "all":
            return v
        
        # SQL 인젝션 방지
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("단과대학 키 형식이 올바르지 않습니다.")
        
        if len(v) > 100:
            raise ValueError("단과대학 키가 너무 깁니다.")
        
        return v
    
    @model_validator(mode="after")
    def validate_date_range(self):
        """날짜 범위 검증"""
        if self.date_from and self.date_to:
            try:
                from_date = datetime.strptime(self.date_from, "%Y-%m-%d")
                to_date = datetime.strptime(self.date_to, "%Y-%m-%d")
                
                if from_date > to_date:
                    raise ValueError("시작 날짜가 종료 날짜보다 늦을 수 없습니다.")
                
                # 날짜 범위가 너무 크면 제한 (예: 1년)
                if (to_date - from_date).days > 365:
                    raise ValueError("검색 기간은 1년을 초과할 수 없습니다.")
            except ValueError as e:
                if "날짜는" in str(e) or "YYYY-MM-DD" in str(e):
                    raise
                raise ValueError(str(e))
        
        return self


class NoticeDetailParams(BaseModel):
    """공지사항 상세 조회 파라미터 검증"""
    notice_id: str = Field(..., description="공지사항 ID")
    
    @field_validator("notice_id")
    @classmethod
    def validate_notice_id(cls, v: str) -> str:
        """공지사항 ID 검증"""
        # UUID 형식 또는 숫자만 허용
        if not re.match(r"^[a-fA-F0-9-]{1,100}$", v):
            raise ValueError("공지사항 ID 형식이 올바르지 않습니다.")
        return v


def sanitize_search_query(query: str) -> str:
    """
    검색 쿼리 문자열 정제 (추가 보안)
    
    Args:
        query: 원본 검색 쿼리
    
    Returns:
        정제된 검색 쿼리
    """
    if not query:
        return ""
    
    # 공백 정규화
    query = re.sub(r"\s+", " ", query.strip())
    
    # 위험한 문자 제거
    query = re.sub(r"[;'\"\\]", "", query)
    
    # 길이 제한
    if len(query) > 500:
        query = query[:500]
    
    return query


def validate_pagination_params(limit: int, offset: int) -> tuple[int, int]:
    """
    페이지네이션 파라미터 검증 및 정규화
    
    Args:
        limit: 페이지 크기
        offset: 오프셋
    
    Returns:
        (정규화된 limit, 정규화된 offset)
    """
    # limit 검증
    if limit < 1:
        limit = 1
    elif limit > 100:
        limit = 100
    
    # offset 검증
    if offset < 0:
        offset = 0
    elif offset > 10000:  # 최대 오프셋 제한 (DoS 방지)
        offset = 10000
    
    return limit, offset

