"""
Gemini Flash 기반 '구조화 추출'과 '실시간 자격 검증' 모듈.
- extract_notice_info(title, body_text) -> dict
- verify_eligibility_ai(qualification_json, user_profile) -> dict
- extract_hashtags_from_title(title) -> dict
- generate_brief_summary(title, text) -> str  # NEW

의존:
  pip install google-generativeai pydantic
환경변수:
  GEMINI_API_KEY=...
  GEMINI_MODEL=gemini-2.0-flash-exp
  AI_TIMEOUT_S=20
  AI_ENABLE_SUMMARY=true
  SUMMARY_MAX_SENTENCES=3
  SUMMARY_MAX_CHARS=180
"""

from __future__ import annotations

# .env 자동 로딩
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import os
import re
import time
import datetime as dt
from typing import Any, Dict, Optional, List
import logging

import google.generativeai as genai
from pydantic import BaseModel, Field, ValidationError

# 로깅 설정
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────────────
# Pydantic v1/v2 호환 Base 스키마 (extra field 무시)
# ───────────────────────────────────────────────────────────────────────────────
try:
    # Pydantic v2
    from pydantic import ConfigDict
    
    class _BaseSchema(BaseModel):
        model_config = ConfigDict(extra="ignore")
except ImportError:
    # Pydantic v1
    class _BaseSchema(BaseModel):
        class Config:
            extra = "ignore"


# ───────────────────────────────────────────────────────────────────────────────
# 0) 환경설정 및 모델 초기화
# ───────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")
AI_TIMEOUT_S   = int(os.getenv("AI_TIMEOUT_S", "20"))
AI_ENABLE_SUMMARY = os.getenv("AI_ENABLE_SUMMARY", "true").lower() == "true"
SUMMARY_MAX_SENTENCES = int(os.getenv("SUMMARY_MAX_SENTENCES", "3"))
SUMMARY_MAX_CHARS = int(os.getenv("SUMMARY_MAX_CHARS", "180"))

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Lazy 모델 생성
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
# 1) Pydantic 스키마 (Structured Output 용) - extra field 무시
# ───────────────────────────────────────────────────────────────────────────────
class ExtractSchema(_BaseSchema):
    """공지 구조화 추출 결과 스키마"""
    category: str = Field(description="장학|채용|행사|수업|행정|기타 중 하나")
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD 또는 null")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD 또는 null")
    qualification: Any = None


class VerifySchema(_BaseSchema):
    """실시간 자격 검증 결과 스키마"""
    eligible: bool
    reason: str


class HashtagSchema(_BaseSchema):
    """해시태그 추출 결과 스키마"""
    hashtags: List[str] = Field(
        description="최종 선택된 해시태그 리스트. 예: ['#학사'] 또는 ['#취업','#국제교류'] 등"
    )


class SummarySchema(_BaseSchema):
    """요약 생성 결과 스키마"""
    summary: str = Field(description="최대 3문장, 180자 이내 핵심 요약")


# ───────────────────────────────────────────────────────────────────────────────
# 2) 유틸
# ───────────────────────────────────────────────────────────────────────────────
_ALLOWED_CATS = {"장학", "채용", "행사", "수업", "행정", "기타"}

# 해시태그 화이트리스트 및 정렬 순서
_ALLOWED_HASHTAGS = [
    "#학사", "#장학", "#행사", "#취업", "#국제교류", "#공모전/대회", "#일반"
]
_HASHTAG_ORDER = {tag: i for i, tag in enumerate(_ALLOWED_HASHTAGS)}

# 불용어 및 제거 패턴
_BOILERPLATE_PATTERNS = [
    r"자세한\s*내용은.*?참고.*?하시기\s*바랍니다",
    r"문의처\s*:.*?(\n|$)",
    r"첨부파일\s*:.*?(\n|$)",
    r"자세히\s*보기",
    r"더\s*보기",
    r"클릭\s*하세요",
    r"홈페이지.*?확인",
    r"이메일\s*:\s*[\w\.-]+@[\w\.-]+",
    r"전화\s*:\s*[\d\-\(\)]+",
    r"http[s]?://[^\s]+",
    r"www\.[^\s]+",
]

_KOREAN_STOPWORDS = {
    "이", "그", "저", "것", "등", "및", "또한", "그리고", "하지만", "그러나",
    "따라서", "그래서", "즉", "또", "만", "도", "을", "를", "이", "가", "은", "는"
}

def _clean_json_text(t: str) -> str:
    """
    LLM이 반환한 텍스트에서 ```json ... ``` 또는 ``` ... ``` 펜스 제거
    """
    if not t:
        return t
    # ```json ... ``` 패턴 찾기
    m = re.search(r"```json\s*(.*?)\s*```", t, re.S | re.I)
    if m:
        return m.group(1).strip()
    # ``` ... ``` 패턴 찾기
    m = re.search(r"```\s*(.*?)\s*```", t, re.S)
    if m:
        return m.group(1).strip()
    return t.strip()

def _strip_html(text: str) -> str:
    """HTML 태그 제거"""
    if not text:
        return ""
    # 간단한 HTML 태그 제거
    text = re.sub(r'<[^>]+>', ' ', text)
    # HTML 엔티티 변환
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    # 연속 공백 정리
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def _remove_boilerplate(text: str) -> str:
    """불필요한 정형 문구 제거"""
    for pattern in _BOILERPLATE_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.MULTILINE)
    return text.strip()

def _split_sentences(text: str) -> List[str]:
    """한국어 문장 분리"""
    # 간단한 문장 분리 (개선 가능)
    sentences = re.split(r'[.!?]\s+', text)
    return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

def _extract_keywords(title: str) -> List[str]:
    """제목에서 핵심 키워드 추출"""
    words = re.findall(r'[가-힣]+|[A-Za-z]+|\d+', title)
    keywords = [w for w in words if len(w) > 1 and w not in _KOREAN_STOPWORDS]
    return keywords[:5]  # 상위 5개만

def _normalize_punctuation(text: str) -> str:
    """문장부호 정규화"""
    # 연속된 마침표/느낌표/물음표 정리
    text = re.sub(r'[.!?]+', '.', text)
    # 문장 끝에 마침표 추가 (없는 경우)
    if text and not text[-1] in '.!?':
        text += '.'
    return text

def _truncate_to_limit(text: str, max_chars: int, max_sentences: int) -> str:
    """문자수/문장수 제한 적용"""
    sentences = _split_sentences(text)
    
    # 문장수 제한
    sentences = sentences[:max_sentences]
    
    result = ""
    for sent in sentences:
        test_result = result + (" " if result else "") + sent
        if not sent.endswith(('.', '!', '?')):
            test_result += '.'
        
        if len(test_result) > max_chars:
            # 현재 문장이 너무 길면 이전까지만 사용
            if result:
                return result
            # 첫 문장도 너무 길면 단어 단위로 자르기
            words = sent.split()
            truncated = ""
            for word in words:
                test = truncated + (" " if truncated else "") + word
                if len(test + "...") <= max_chars:
                    truncated = test
                else:
                    break
            return truncated + "..." if truncated else sent[:max_chars-3] + "..."
        
        result = test_result
    
    return result

def _qual_to_dict(q: Any) -> Dict[str, Any]:
    """
    qualification 값을 dict로 정규화
    - dict면 그대로 반환
    - str이면 {"raw": str} 형태로 변환
    - 기타는 빈 dict
    """
    if isinstance(q, dict):
        return q
    if isinstance(q, str):
        s = q.strip()
        if s:
            return {"raw": s}
        return {}
    return {}

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
    반환 dict 키:
      - category_ai: str ("장학|채용|행사|수업|행정|기타")
      - start_date_ai: str | None (YYYY-MM-DD)
      - end_date_ai: str | None (YYYY-MM-DD)
      - qualification_ai: dict
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
            response_mime_type="application/json"
        ),
    )

    try:
        raw = _clean_json_text(resp.text)
        data = ExtractSchema.model_validate_json(raw)
    except ValidationError as ve:
        raise RuntimeError(f"LLM structured output validation failed: {ve}") from ve

    # 최소 후처리(안정화)
    cat = _norm_category(data.category)
    s   = _iso_or_none(data.start_date)
    e   = _iso_or_none(data.end_date)
    qual = _qual_to_dict(data.qualification)

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
            response_mime_type="application/json"
        ),
    )

    try:
        raw = _clean_json_text(resp.text)
        data = VerifySchema.model_validate_json(raw)
    except ValidationError as ve:
        raise RuntimeError(f"LLM verify structured output validation failed: {ve}") from ve

    return {"eligible": data.eligible, "reason": data.reason}


# ───────────────────────────────────────────────────────────────────────────────
# 5) 공개 함수: 제목 기반 해시태그 추출
# ───────────────────────────────────────────────────────────────────────────────
def extract_hashtags_from_title(title: str) -> Dict[str, List[str]]:
    """
    공지 제목을 소거법으로 분석하여 해시태그를 추출한다.
    
    반환 예:
      {"hashtags": ["#학사"]}
      {"hashtags": ["#취업", "#국제교류"]}
      {"hashtags": ["#일반"]}
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
            response_mime_type="application/json"
        ),
    )

    try:
        raw = _clean_json_text(resp.text)
        data = HashtagSchema.model_validate_json(raw)
    except ValidationError as ve:
        raise RuntimeError(f"LLM hashtag structured output validation failed: {ve}") from ve

    # 후처리: 화이트리스트/일반 규칙/중복 제거/정렬
    tags = _norm_hashtags(data.hashtags)
    
    return {"hashtags": tags}


# ───────────────────────────────────────────────────────────────────────────────
# 6) 새 함수: 요약 생성
# ───────────────────────────────────────────────────────────────────────────────
def generate_brief_summary(
    title: str,
    text: str,
    locale: str = 'ko',
    max_sentences: int = None,
    max_chars: int = None
) -> str:
    """
    공지의 핵심 3포인트를 간결하게 요약한다.
    
    Args:
        title: 공지 제목
        text: 요약할 텍스트 (summary_raw 또는 body_text)
        locale: 언어 설정 (기본 'ko')
        max_sentences: 최대 문장 수 (기본값은 환경변수)
        max_chars: 최대 문자 수 (기본값은 환경변수)
    
    Returns:
        요약 문자열 (비어있을 수 없음, 실패시에도 최소 1문장 생성)
    """
    if max_sentences is None:
        max_sentences = SUMMARY_MAX_SENTENCES
    if max_chars is None:
        max_chars = SUMMARY_MAX_CHARS
    
    # 입력 전처리
    title = (title or "").strip()
    text = (text or "").strip()
    
    # HTML 제거 및 정리
    text = _strip_html(text)
    text = _remove_boilerplate(text)
    
    # 너무 짧은 입력 처리
    if not title and not text:
        logger.info("Empty input for summary generation")
        return "요약 정보가 없습니다."
    
    if not text or len(text) < 20:
        # 텍스트가 너무 짧으면 제목 기반 요약
        logger.info(f"Text too short, using title-based summary")
        if title:
            summary = title
            if not summary.endswith(('.', '!', '?')):
                summary += '.'
            return _truncate_to_limit(summary, max_chars, 1)
        return "내용이 부족하여 요약할 수 없습니다."
    
    # LLM 경로 (환경변수로 제어)
    if AI_ENABLE_SUMMARY and GEMINI_API_KEY:
        try:
            summary = _generate_summary_llm(title, text, locale, max_sentences, max_chars)
            if summary:
                logger.debug(f"LLM summary generated: {len(summary)} chars")
                return summary
        except Exception as e:
            logger.warning(f"LLM summary generation failed: {e}")
    
    # 룰 기반 폴백
    logger.info("Using rule-based fallback for summary")
    return _generate_summary_fallback(title, text, max_sentences, max_chars)


def _generate_summary_llm(
    title: str,
    text: str,
    locale: str,
    max_sentences: int,
    max_chars: int,
    max_retries: int = 3
) -> Optional[str]:
    """LLM을 사용한 요약 생성"""
    model = _get_model()
    
    # 입력 텍스트 제한 (토큰 절약)
    text_for_llm = text[:3000] if len(text) > 3000 else text
    
    prompt = f"""
대학 공지사항을 {max_sentences}문장, {max_chars}자 이내로 요약하세요.

요구사항:
1. 가장 중요한 정보 {max_sentences}가지만 포함
2. 날짜, 기한, 장소, 대상, 금액 등 구체적 정보 우선
3. 불필요한 문구 금지: "자세한 내용은", "문의처", "홈페이지 참고" 등
4. 각 문장은 핵심만 간결하게
5. 한국어로 자연스럽게 작성

제목: {title}
본문: {text_for_llm}

JSON 형식으로 요약을 반환하세요:
{{"summary": "요약 내용"}}
    """.strip()
    
    for attempt in range(max_retries):
        try:
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.3,  # 일관성 있는 요약
                    max_output_tokens=200,
                ),
                request_options={"timeout": AI_TIMEOUT_S}
            )
            
            raw = _clean_json_text(resp.text)
            data = SummarySchema.model_validate_json(raw)
            summary = data.summary
            
            # 후처리 및 검증
            summary = _normalize_punctuation(summary)
            summary = _truncate_to_limit(summary, max_chars, max_sentences)
            
            if summary and len(summary) > 10:  # 최소 길이 검증
                return summary
                
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                logger.warning(f"LLM quota exceeded, falling back")
                return None
            if attempt == max_retries - 1:
                logger.warning(f"LLM summary failed after {max_retries} attempts: {e}")
            else:
                time.sleep(1)  # 재시도 전 대기
    
    return None


def _generate_summary_fallback(
    title: str,
    text: str,
    max_sentences: int,
    max_chars: int
) -> str:
    """규칙 기반 폴백 요약 생성"""
    
    # 1. 제목에서 키워드 추출
    keywords = _extract_keywords(title) if title else []
    
    # 2. 본문에서 정보 밀도 높은 문장 선택
    sentences = _split_sentences(text)
    if not sentences:
        # 문장 분리 실패시 첫 부분만 사용
        summary = text[:max_chars-3] + "..." if len(text) > max_chars else text
        return _normalize_punctuation(summary)
    
    # 3. 키워드 관련성 + 숫자/날짜 포함 여부로 문장 점수 계산
    scored_sentences = []
    for sent in sentences[:10]:  # 처음 10문장만 고려
        score = 0
        sent_lower = sent.lower()
        
        # 키워드 매칭
        for kw in keywords:
            if kw.lower() in sent_lower:
                score += 2
        
        # 중요 정보 패턴
        if re.search(r'\d{4}[-년./]\d{1,2}[-월./]\d{1,2}', sent):  # 날짜
            score += 3
        if re.search(r'\d+[:시]\d+분?', sent):  # 시간
            score += 2
        if re.search(r'\d+[명원]', sent):  # 인원
            score += 2
        if re.search(r'\d+[만천백십]?\s?원', sent):  # 금액
            score += 3
        if any(word in sent for word in ['신청', '접수', '마감', '기한', '대상', '자격']):
            score += 2
        
        # 문장 길이 페널티 (너무 긴 문장 회피)
        if len(sent) > 100:
            score -= 1
        
        scored_sentences.append((score, sent))
    
    # 4. 점수 높은 순으로 정렬하되, 원문 순서 유지
    scored_sentences.sort(key=lambda x: (-x[0], sentences.index(x[1])))
    
    # 5. 상위 문장 선택 및 조합
    selected = []
    total_length = 0
    
    for score, sent in scored_sentences:
        if len(selected) >= max_sentences:
            break
        
        sent = _normalize_punctuation(sent)
        test_length = total_length + len(sent) + (1 if selected else 0)
        
        if test_length <= max_chars:
            selected.append(sent)
            total_length = test_length
    
    # 6. 결과 조합
    if selected:
        summary = ' '.join(selected)
    else:
        # 아무것도 선택되지 않은 경우 제목 + 첫 문장
        if title:
            summary = title
            if sentences and len(summary) < max_chars - 20:
                first_sent = _normalize_punctuation(sentences[0])
                if len(summary) + len(first_sent) + 2 <= max_chars:
                    summary += ". " + first_sent
        else:
            summary = _normalize_punctuation(sentences[0]) if sentences else "요약할 수 없습니다."
    
    return _truncate_to_limit(summary, max_chars, max_sentences)