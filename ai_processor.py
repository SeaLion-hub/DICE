"""
Gemini Flash 기반 '구조화 추출'과 '실시간 자격 검증' 모듈.
- extract_notice_info(title, body_text) -> dict
- verify_eligibility_ai(qualification_json, user_profile) -> dict
- extract_hashtags_from_title(title) -> dict  # NEW

의존:
  pip install google-generativeai pydantic
환경변수:
  GEMINI_API_KEY=...
  GEMINI_MODEL=gemini-2.0-flash-exp   # 콘솔에서 제공되는 Flash 계열로 교체 가능
  AI_TIMEOUT_S=20                      # 옵션
주의:
  이 파일만 추가한다. main.py 등의 통합은 '다음 작업'에서 별도 안내 예정.
"""

from __future__ import annotations  # ✅ 파일 맨 위(모듈 docstring 다음)로 이동

# (선택) 모듈 docstring이 있다면 그 위/아래 순서는 아래처럼 유지 가능
"""
Gemini Flash 기반 '구조화 추출'과 '실시간 자격 검증' 모듈.
"""

# .env 자동 로딩 (이제 future import보다 아래에 위치)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import os
import datetime as dt
from typing import Any, Dict, Optional, List

import google.generativeai as genai
from pydantic import BaseModel, Field, ValidationError

# ───────────────────────────────────────────────────────────────────────────────
# 0) 환경설정 및 모델 초기화
# ───────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")





AI_TIMEOUT_S   = int(os.getenv("AI_TIMEOUT_S", "20"))

if not GEMINI_API_KEY:
    # import 시점에 바로 에러 내지 않고, 호출 시점에서 검증한다.
    pass

# configure once
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Lazy 생성: 일부 환경(테스트/로컬)에서 API 키가 없는 경우 대비
_model: Optional[genai.GenerativeModel] = None
def _get_model() -> genai.GenerativeModel:
    global _model
    if _model is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set")
        genai.configure(api_key=GEMINI_API_KEY)
        _model = genai.GenerativeModel(GEMINI_MODEL)
    return _model


# ───────────────────────────────────────────────────────────────────────────────
# 1) Pydantic 스키마 (Structured Output 용)
# ───────────────────────────────────────────────────────────────────────────────
class ExtractSchema(BaseModel):
    """
    공지 구조화 추출 결과 스키마
    - 날짜는 YYYY-MM-DD (시간 없음)
    - qualification: grade/gpa/lang 중심의 요약 JSON (없으면 빈 값 허용)
    """
    category: str = Field(description="장학|채용|행사|수업|행정|기타 중 하나")
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD 또는 null")
    end_date: Optional[str]   = Field(default=None, description="YYYY-MM-DD 또는 null")
    qualification: Dict[str, Any] = Field(default_factory=dict, description="예: {'grade':'3+','gpa':'3.5/4.3','lang':'TOEIC 800+'}")


class VerifySchema(BaseModel):
    """
    실시간 자격 검증 결과 스키마
    """
    eligible: bool
    reason: str


class HashtagSchema(BaseModel):
    """
    해시태그 추출 결과 스키마
    """
    hashtags: List[str] = Field(
        description="최종 선택된 해시태그 리스트. 예: ['#학사'] 또는 ['#취업','#국제교류'] 등"
    )


# ───────────────────────────────────────────────────────────────────────────────
# 2) 유틸
# ───────────────────────────────────────────────────────────────────────────────
_ALLOWED_CATS = {"장학", "채용", "행사", "수업", "행정", "기타"}

# 해시태그 화이트리스트 및 정렬 순서
_ALLOWED_HASHTAGS = [
    "#학사", "#장학", "#행사", "#취업", "#국제교류", "#공모전/대회", "#일반"
]
_HASHTAG_ORDER = {tag: i for i, tag in enumerate(_ALLOWED_HASHTAGS)}

def _iso_or_none(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    try:
        dt.date.fromisoformat(s)
        return s
    except Exception:
        return None

def _norm_category(cat: Optional[str]) -> str:
    if not cat:
        return "기타"
    return cat if cat in _ALLOWED_CATS else "기타"

def _norm_hashtags(tags: List[str]) -> List[str]:
    """
    화이트리스트 필터링, #일반 단독 규칙, 중복 제거, 정렬.
    
    규칙:
    1. 화이트리스트에 없는 태그 제거
    2. 중복 제거 (입력 순서 보존)
    3. #일반 규칙: 다른 태그와 함께 있으면 #일반 제거, 단독일 때만 허용
    4. 정렬: _ALLOWED_HASHTAGS 정의 순서대로
    5. 빈 리스트면 ["#일반"] 반환
    """
    # 화이트리스트 필터링 + 공백 제거
    tags = [t.strip() for t in tags if t and t.strip() in _ALLOWED_HASHTAGS]
    
    # 중복 제거 (입력 순서 보존)
    seen, uniq = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    tags = uniq
    
    # #일반 규칙: 다른 태그가 있으면 #일반 제거
    if "#일반" in tags:
        others = [t for t in tags if t != "#일반"]
        if len(others) > 0:
            tags = others
        else:
            tags = ["#일반"]
    
    # 사전 정의 순서로 정렬
    tags.sort(key=lambda x: _HASHTAG_ORDER.get(x, 999))
    
    # 빈 리스트면 #일반 반환
    if not tags:
        return ["#일반"]
    
    return tags


# ───────────────────────────────────────────────────────────────────────────────
# 3) 공개 함수: 공지 구조화 추출
# ───────────────────────────────────────────────────────────────────────────────
def extract_notice_info(body_text: str, title: Optional[str] = None) -> Dict[str, Any]:
    """
    공지 본문(+제목)을 LLM에 전달하여 구조화된 정보를 추출한다.
    반환 dict 키(통합 단계에서 DB 컬럼과 맵핑하기 쉬운 형태로 맞춤):
      - category_ai: str ("장학|채용|행사|수업|행정|기타")
      - start_date_ai: str | None (YYYY-MM-DD)
      - end_date_ai: str | None (YYYY-MM-DD)
      - qualification_ai: dict
    예외 발생 시 RuntimeError를 던진다(상위에서 캐치하고 기본값 처리 권장).
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    model = _get_model()

    prompt = f"""
당신은 대학 공지 텍스트에서 핵심 정보를 구조화하는 시스템입니다.
출력은 반드시 JSON이며, 아래 스키마를 엄격히 따르세요.
- category: "장학|채용|행사|수업|행정|기타" 중 하나
- start_date / end_date: YYYY-MM-DD (모르면 null)
- qualification: 지원요건 핵심을 JSON으로 요약(가능하면 grade/gpa/lang 키 사용, 없으면 비워둠)

[제목]
{title or ""}

[본문]
{(body_text or "")[:6000]}
    """.strip()

    resp = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            response_mime_type="application/json",
            response_schema=ExtractSchema
        ),
    )

    try:
        data = ExtractSchema.model_validate_json(resp.text)
    except ValidationError as ve:
        raise RuntimeError(f"LLM structured output validation failed: {ve}") from ve

    # 최소 후처리(안정화)
    cat = _norm_category(data.category)
    s   = _iso_or_none(data.start_date)
    e   = _iso_or_none(data.end_date)
    qual = data.qualification or {}

    return {
        "category_ai": cat,
        "start_date_ai": s,
        "end_date_ai": e,
        "qualification_ai": qual,
    }


# ───────────────────────────────────────────────────────────────────────────────
# 4) 공개 함수: 실시간 자격 검증
# ───────────────────────────────────────────────────────────────────────────────
def verify_eligibility_ai(qualification_json: Dict[str, Any], user_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    qualification_json(공지의 AI 요건 요약)과 user_profile(사용자 입력)을 비교해
    적합 여부를 판단한다.
    반환:
      { "eligible": bool, "reason": "..." }
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    model = _get_model()

    prompt = f"""
[지원요건(JSON)]
{qualification_json}

[사용자 프로필(JSON)]
{user_profile}

위 두 정보를 비교하여 지원 가능 여부를 판단하세요.
반드시 다음 JSON 스키마로만 답하십시오:
{{
  "eligible": true/false,
  "reason": "간단하고 구체적인 근거"
}}
    """.strip()

    resp = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            response_mime_type="application/json",
            response_schema=VerifySchema
        ),
    )

    try:
        data = VerifySchema.model_validate_json(resp.text)
    except ValidationError as ve:
        raise RuntimeError(f"LLM verify structured output validation failed: {ve}") from ve

    return {"eligible": data.eligible, "reason": data.reason}


# ───────────────────────────────────────────────────────────────────────────────
# 5) 공개 함수: 제목 기반 해시태그 추출 (NEW)
# ───────────────────────────────────────────────────────────────────────────────
def extract_hashtags_from_title(title: str) -> Dict[str, List[str]]:
    """
    공지 제목을 소거법으로 분석하여 해시태그를 추출한다.
    
    반환 예:
      {"hashtags": ["#학사"]}
      {"hashtags": ["#취업", "#국제교류"]}
      {"hashtags": ["#일반"]}
    
    예외 발생 시 RuntimeError를 던진다(상위에서 캐치하고 기본값 처리 권장).
    
    AC 만족 사항:
    1. JSON 구조화 강제: {"hashtags": [...]} 형식만 반환
    2. 화이트리스트: _ALLOWED_HASHTAGS 내에서만 선택
    3. #일반 규칙: 단독일 때만 허용, 다른 태그와 함께면 제거
    4. 중복 제거 & 정렬: _ALLOWED_HASHTAGS 순서대로
    5. 오류 전파: RuntimeError로 상위 계층에 전달
    6. 한글/영문 혼용: 대괄호 [...] 내용은 주체로만 취급
    7. 단일 파일 수정: ai_processor.py만 변경
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    model = _get_model()

    prompt = f"""
너는 연세대학교 공지사항 제목을 '소거법'으로 분석해 해시태그를 선택하는 AI 분석가다.
반드시 아래 [카테고리 목록] 중에서만 선택하고, 결과는 JSON(키: "hashtags")으로만 반환하라.

[카테고리 목록]
- 학사: 수강신청, 졸업, 성적, 등록금, 각종 시험, 재입학, 휴학, 복학
- 장학: 교내/외 장학금, 학자금 대출, 근로장학생
- 행사: 특강, 워크숍, 설명회, 캠페인
- 취업: 채용, 인턴십, 창업 지원
- 국제교류: 교환학생, 해외 파견, 국제 계절학기
- 공모전/대회: 교내/외 공모전, 경진대회
- 일반: 다른 특정 카테고리에 속하지 않는 모든 공지

[작업 절차]
1) [분석]: 제목의 핵심 주제를 파악한다. (대괄호 [...] 는 주체 표시이므로 내용 자체로 분류하지 않는다)
2) [소거]: 위 카테고리 중 명백히 관련 없는 것을 제거한다.
3) [선택]: 남은 카테고리에서 가장 적합한 모든 태그를 선택한다.
4) [최종 판단]:
   - '#일반'을 제외한 다른 모든 카테고리가 소거되면 '#일반'만 선택한다.
   - '#일반'은 다른 태그와 함께 사용하지 않는다.
5) [출력]: 선택된 태그를 "#카테고리명" 형식으로 JSON에 담아라. 목록에 없는 태그를 만들지 말라.

[요청 제목]
{title}
    """.strip()

    resp = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            response_mime_type="application/json",
            response_schema=HashtagSchema
        ),
    )

    try:
        data = HashtagSchema.model_validate_json(resp.text)
    except ValidationError as ve:
        raise RuntimeError(f"LLM hashtag structured output validation failed: {ve}") from ve

    # 후처리: 화이트리스트/일반 규칙/중복 제거/정렬
    tags = _norm_hashtags(data.hashtags)
    
    return {"hashtags": tags}