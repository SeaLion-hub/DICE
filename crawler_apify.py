# crawler_apify.py (raw_text 저장 및 정제 로직 포함)
import os
import time
import json
import hashlib
import requests
import psycopg2
import re
import datetime as dt
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, urljoin
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv
from typing import Optional, Dict, Any, List, Tuple
from html import unescape
from bs4 import BeautifulSoup
from colleges import COLLEGES
import logging # 로깅 임포트 추가

# AI processor import 수정 (배치 분류 함수 사용)
from ai_processor import (
    classify_hashtags_from_title_batch,
    extract_structured_info,
    extract_detailed_hashtags,
)
# _to_utc_ts 함수 import
try:
    from main import _to_utc_ts
except ImportError:
    print("Warning: Could not import _to_utc_ts from main.py. Defining locally.")
    def _to_utc_ts(date_yyyy_mm_dd: str | None):
        if not date_yyyy_mm_dd:
            return None
        try:
            d = dt.date.fromisoformat(date_yyyy_mm_dd)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            print(f"Warning: Invalid date format: {date_yyyy_mm_dd}. Returning None.")
            return None

# [변경 없음] clean_body_text 함수는 이미 raw_text를 받아 정제하도록 되어 있음
def clean_body_text(raw_text: str, college_key: Optional[str] = None) -> str:
    """
    Apify에서 크롤링한 원본 body_text에서 불필요한
    CDATA 스크립트, HTML 태그, 헤더, 푸터 정보를 제거하여 순수 본문만 추출합니다.
    (사용자 요청에 따라 "게시글 내용"과 푸터 마커 사이를 추출하도록 수정)
    """
    if not raw_text:
        return ""

    # 1. HTML 엔티티 복원 (e.g., &lt; -> <)
    text = unescape(raw_text)

    # 2. JavaScript CDATA 블록 제거 (사용자 예시 패턴)
    text = re.sub(r'//<!\[CDATA\[.*?//\]\]>', '', text, flags=re.DOTALL)

    # 3. BeautifulSoup을 사용하여 HTML 태그 제거 및 텍스트만 추출
    soup = BeautifulSoup(text, 'html.parser')
    text = soup.get_text(separator='\n', strip=True)

    # 4. 헤더(Header) 정보 제거
    #    사용자 요청: "게시글 내용"을 시작 마커로 사용
    start_marker = r'게시글 내용'
    start_match = re.search(start_marker, text, re.IGNORECASE)
    
    start_index = 0
    if start_match:
        start_index = start_match.end() # "게시글 내용" *이후* 부터
    else:
        # "게시글 내용"이 없으면, 기존의 다른 헤더 마커로 대체 (안전장치)
        header_end_patterns = [
            r'조회수\s+\d+',
            # '.xlsx', '.pdf' 등 첨부파일 링크 (공백이나 줄바꿈으로 끝남)
            r'\.(xlsx|pdf|hwp|doc|docx|zip|jpg|png|jpeg|gif)(\s|\n|$)',
        ]
        last_header_end_index = -1
        for pattern in header_end_patterns:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            if matches:
                # 마지막 일치 항목의 끝 위치를 찾음
                last_match_end = matches[-1].end()
                if last_match_end > last_header_end_index:
                    last_header_end_index = last_match_end

        if last_header_end_index != -1 and last_header_end_index < len(text):
            start_index = last_header_end_index # 다른 헤더 마커 위치
        # else: start_index는 0 유지 (처음부터)
    
    # "게시글 내용" 마커를 찾았든 못 찾았든, start_index부터 텍스트를 자름
    text = text[start_index:]


    # 5. 푸터(Footer) 정보 제거
    #    사용자 요청에 따라 마커 우선순위 및 규칙 변경

    # 5a. college_key에 따른 예외 처리 (의과대학)
    # (colleges.py의 'med' 키라고 가정)
    if college_key == 'med':
        primary_footer_markers = [
            r'연세대학교 의과대학 TAG',  # 의대 우선
            r'\sTAG\s',               # 의대 우선
        ]
    else:
        # 5b. 일반 규칙
        primary_footer_markers = [
            r'목록\s+이전글' # '목록 이전글', '목록  이전글'
        ]

    # 5c. (Fallback) 기존의 다른 푸터 마커들
    fallback_footer_markers = [
        r'연세대학교 관련사이트',
        r'COPYRIGHT©',
        r'채용공고\s+입찰공고'
    ]
    
    # college_key 조건에 따라 fallback 마커 목록을 조정
    if college_key == 'med':
        # 의대인 경우, '목록 이전글'을 fallback에 추가
        fallback_footer_markers.append(r'목록\s+이전글')
    else:
        # 의대가 아닌 경우, 'TAG' 관련을 fallback에 추가
        fallback_footer_markers.extend([r'연세대학교 의과대학 TAG', r'\sTAG\s'])

    # 5d. 푸터 패턴 컴파일 및 검색 (우선순위 마커 + fallback 마커)
    all_footer_markers = primary_footer_markers + fallback_footer_markers
    footer_pattern = re.compile('|'.join(all_footer_markers), re.IGNORECASE | re.DOTALL)
    
    match = footer_pattern.search(text) # (이제 text는 start_index 이후의 내용임)
    if match:
        # 푸터 마커가 시작되는 위치의 텍스트만 사용
        text = text[:match.start()]

    # 6. 최종 정리: 앞뒤 공백 및 불필요한 개행 문자 정돈
    text = re.sub(r'(\n\s*){3,}', '\n\n', text) # 3줄 이상의 개행을 2줄로
    
    return text.strip()


load_dotenv(encoding="utf-8")

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
AI_IN_PIPELINE = os.getenv("AI_IN_PIPELINE", "true").lower() == "true"
AI_SLEEP_SEC = float(os.getenv("AI_SLEEP_SEC", "1.0"))
AI_BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", "10"))
AI_STEP2_SLEEP_SEC = float(os.getenv("AI_STEP2_SLEEP_SEC", str(AI_SLEEP_SEC)))
AI_STEP3_SLEEP_SEC = float(os.getenv("AI_STEP3_SLEEP_SEC", str(AI_SLEEP_SEC)))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2"))

if not APIFY_TOKEN:
    raise RuntimeError("APIFY_TOKEN not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# 로거 설정 추가
logger = logging.getLogger(__name__) # __name__으로 로거 이름 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

# ⭐️ [수정] UPSERT SQL: detailed_hashtags 컬럼 추가
UPSERT_SQL = """
INSERT INTO notices (
    college_key, title, url, body_html, body_text, raw_text,
    published_at, source_site, content_hash,
    category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai,
    detailed_hashtags -- [수정 1] INSERT 목록에 컬럼 추가
) VALUES (
    %(college_key)s, %(title)s, %(url)s,
    %(body_html)s, %(body_text)s, %(raw_text)s,
    %(published_at)s, %(source_site)s, %(content_hash)s,
    %(category_ai)s, %(start_at_ai)s, %(end_at_ai)s, %(qualification_ai)s, %(hashtags_ai)s,
    %(detailed_hashtags)s -- [수정 2] VALUES 목록에 파라미터 추가
)
ON CONFLICT (content_hash)
DO UPDATE SET
    title = EXCLUDED.title,
    url = EXCLUDED.url,
    body_html = EXCLUDED.body_html,
    body_text = EXCLUDED.body_text,
    raw_text = EXCLUDED.raw_text,
    published_at = EXCLUDED.published_at,
    category_ai = EXCLUDED.category_ai,
    start_at_ai = EXCLUDED.start_at_ai,
    end_at_ai = EXCLUDED.end_at_ai,
    qualification_ai = EXCLUDED.qualification_ai,
    hashtags_ai = EXCLUDED.hashtags_ai,
    detailed_hashtags = EXCLUDED.detailed_hashtags, -- [수정 3] 이 참조가 이제 유효함
    updated_at = CURRENT_TIMESTAMP
RETURNING id;
"""

# --- 기존 헬퍼 함수들 (clean_text, extract_text_from_html, ...) ---
def clean_text(text: Optional[str], max_length: Optional[int] = None) -> str:
    """텍스트 정리 (줄바꿈은 유지하도록 수정)"""
    if not text: return ""
    text = unescape(text) # HTML 엔티티 디코딩
    
    # 여러 줄의 공백/개행을 최대 2줄로
    text = re.sub(r'(\n\s*){3,}', '\n\n', text)
    # 일반적인 연속 공백 (줄바꿈 제외)을 하나로
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    
    text = text.strip() # 앞뒤 공백 제거
    if max_length and len(text) > max_length: # 길이 제한
        text = text[:max_length-3] + "..."
    return text

def extract_text_from_html(html: Optional[str]) -> str:
    """HTML에서 주요 텍스트 내용만 추출 시도 (오류나는 대학 패턴 수정)"""
    if not html: return ""
    try:
        # 1. CDATA 스크립트 먼저 제거 (파싱 오류 방지)
        text_content = re.sub(r'//<!\[CDATA\[.*?//\]\]>', '', html, flags=re.DOTALL)
        
        # 2. BeautifulSoup으로 파싱
        soup = BeautifulSoup(text_content, 'html.parser')

        # 3. 불필요한 태그 제거 (기존 로직 유지)
        for tag in soup(['script', 'style', 'meta', 'link', 'header', 'footer', 'nav', 'aside', 'form']):
            tag.decompose()

        # 4. 텍스트 추출 (줄바꿈 유지)
        text = soup.get_text(separator='\n', strip=True)

        # 5. 헤더(Header) 정보 제거
        #    '게시글 내용' 마커는 불안정하므로 제거
        header_end_patterns = [
            r'조회수\s+\d+',
            r'\.(xlsx|pdf|hwp|doc|docx|zip|jpg|png|jpeg|gif)(\s|\n|$)', # 첨부파일
        ]
        
        last_header_end_index = -1
        for pattern in header_end_patterns:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            if matches:
                # 마지막 일치 항목의 끝 위치를 찾음
                last_match_end = matches[-1].end()
                if last_match_end > last_header_end_index:
                    last_header_end_index = last_match_end

        if last_header_end_index != -1 and last_header_end_index < len(text):
            text = text[last_header_end_index:] # 헤더 마커 *이후*의 텍스트

        # 6. 푸터(Footer) 정보 제거
        #    '목록' 관련 마커는 불안정하므로 제거
        footer_markers = [
            r'연세대학교 의과대학 TAG',
            r'\sTAG\s',
            r'연세대학교 관련사이트',
            r'COPYRIGHT©',
            r'채용공고\s+입찰공고',
            r'개인정보처리방침',
            # r'목록 이전글', # <-- 이 마커가 문제를 일으킴 (제거)
        ]
        footer_pattern = re.compile('|'.join(footer_markers), re.IGNORECASE | re.DOTALL)
        match = footer_pattern.search(text)
        if match:
            text = text[:match.start()] # 푸터 마커 *이전*의 텍스트

        # 7. '게시글 내용' 텍스트가 남아있다면 직접 제거
        text = text.replace("게시글 내용", "")
        
        # 8. 마지막으로 1단계에서 수정한 clean_text 함수로 최종 정리
        return clean_text(text)
        
    except Exception as e:
        logger.warning(f"  ⚠️ HTML parsing error: {e}")
        return "" # 오류 발생 시 빈 문자열 반환

def normalize_url(url: Optional[str], base_url: Optional[str] = None) -> str:
    """URL 정규화 (절대 경로 변환, fragment 제거 등)"""
    if not url: return ""
    url = url.strip()

    # 상대 경로 -> 절대 경로 변환
    if base_url and not url.startswith(('http://', 'https://', '//', 'javascript:')):
        try:
            url = urljoin(base_url, url)
        except ValueError:
             logger.warning(f"  ⚠️ Could not join base_url '{base_url}' and relative url '{url}'")
             return "" # URL 결합 실패 시 빈 문자열

    # 프로토콜 없는 URL 처리 (예: //example.com)
    if url.startswith('//'):
        url = 'https:' + url

    # javascript: 링크 무시
    if url.startswith('javascript:'):
        return ""

    # URL 파싱하여 유효성 검사 (scheme, netloc 확인)
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        logger.debug(f"  ⚠️ Invalid URL structure: {url}")
        return ""

    # Fragment 제거 (예: #section1)
    url = url.split('#')[0]

    return url

def parse_dt(v: Any) -> Optional[datetime]:
    """다양한 형식의 날짜/시간 값을 UTC datetime 객체로 파싱"""
    if not v: return None

    # 이미 datetime 객체인 경우
    if isinstance(v, datetime):
        # 타임존 정보가 없으면 UTC로 간주
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    # 타임스탬프 (초 또는 밀리초)인 경우
    if isinstance(v, (int, float)):
        try:
            # 밀리초인지 초인지 추정
            ts = v / 1000 if v > 10_000_000_000 else v
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError): # 유효하지 않은 타임스탬프
            logger.warning(f"  ⚠️ Invalid timestamp: {v}")
            return None

    # 문자열인 경우
    if isinstance(v, str):
        v = v.strip()
        if not v: return None

        # ISO 8601 형식 (+/-HH:MM 또는 Z 포함) 우선 처리
        if re.search(r'[+-]\d{2}:\d{2}$|Z$', v):
            v = v.replace("Z", "+00:00") # Z를 UTC 오프셋으로 변경
            try:
                # 파싱 전 마이크로초 부분 길이 조정 (최대 6자리)
                if '.' in v and ('+' in v or ('-' in v and v.rfind('-') > v.find('T'))):
                    parts = v.rsplit('+', 1) if '+' in v else v.rsplit('-', 1)
                    time_part = parts[0]
                    tz_part = parts[1]
                    if '.' in time_part:
                       time_part = time_part[:time_part.find('.')+7] # 마이크로초 6자리까지만
                    v = f"{time_part}{'+' if '+' in v else '-'}{tz_part}"

                dt_obj = datetime.fromisoformat(v)
                return dt_obj.astimezone(timezone.utc) # UTC로 변환
            except ValueError as e:
                logger.debug(f"  ⚠️ ISO format parse error for '{v}': {e}")
                pass # 다른 형식 시도

        # 다양한 일반 날짜/시간 형식 시도
        date_formats = [
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
            "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d", # 점 구분
            "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", # DD/MM/YYYY 형식
            "%Y년 %m월 %d일 %H:%M", "%Y년 %m월 %d일", # 한국어 형식
        ]
        for fmt in date_formats:
            try:
                dt_obj = datetime.strptime(v, fmt)
                # 타임존 정보가 없으므로 UTC로 설정
                return dt_obj.replace(tzinfo=timezone.utc)
            except ValueError:
                continue # 다음 형식 시도

    # 어떤 형식에도 맞지 않으면 None 반환
    logger.debug(f"  ⚠️ Unparseable date format: {v} (type: {type(v)})")
    return None

def extract_field(item: Dict[str, Any], field_names: List[str], default: Any = "") -> Optional[Any]:
    """여러 필드 이름 후보 중 첫 번째로 찾은 값을 반환 (점 표기법 지원)"""
    for field in field_names:
        if '.' in field: # 중첩된 필드 접근 (예: 'meta.title')
            parts = field.split('.')
            value = item
            valid_path = True
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                    # 리스트 인덱스 접근 (제한적 지원)
                    value = value[int(part)]
                else:
                    value = None
                    valid_path = False
                    break
            if valid_path and value is not None:
                return value
        else: # 일반 필드 접근
            value = item.get(field)
            if value is not None:
                return value
    # 모든 후보 필드에 값이 없으면 기본값 반환
    return default

# ⭐️ [수정 2] normalize_item: raw_text를 반환 딕셔너리에 추가
def normalize_item(item: dict, base_url: Optional[str] = None, college_key: Optional[str] = None) -> dict:
    """Apify 크롤링 결과 item을 표준 형식으로 정규화"""
    # 제목 추출 (여러 필드명 후보 사용)
    title = clean_text(extract_field(item, ["title", "name", "subject", "headline", "meta.title", "og:title", "titleText", "h1", "h2"], default=""), max_length=500)
    # URL 추출 및 정규화
    url = normalize_url(extract_field(item, ["url", "link", "href", "permalink", "canonical", "meta.url", "og:url"], default=""), base_url)
    # HTML 본문 추출
    body_html = extract_field(item, ["html", "content_html", "body_html", "htmlContent", "content", "text"], default=None) # HTML은 그대로 유지 시도
    
    # 원본 'content'/'text' 필드 추출 (이것이 raw_text가 됨)
    raw_text = extract_field(item, ["text", "content", "body", "body_text", "plainText"], default=None)
    
    # raw_text를 기반으로 body_text 정제
    body_text = clean_body_text(raw_text, college_key=college_key)

    # (비상 로직) 만약 raw_text에 내용이 없고 body_html에만 내용이 있는 경우,
    # body_html을 정제 시도
    if not body_text and body_html:
         # 이 경우, 원본 텍스트가 body_html이므로 raw_text도 body_html로 설정
         if not raw_text: 
             raw_text = body_html
         body_text = clean_body_text(body_html, college_key=college_key)

    # 발행일 추출 (여러 필드명 후보 사용 및 파싱)
    published_at = None
    date_fields = ["publishedAt", "published_at", "createdAt", "created_at", "datetime", "timestamp", "pubDate", "date", "time", "postDate", "releaseDate"]
    for field in date_fields:
        value = extract_field(item, [field], default=None) # 중첩 필드도 가능하게 extract_field 사용
        if value:
            parsed = parse_dt(value)
            if parsed:
                # 너무 오래된 날짜는 오류 가능성 있으므로 로그 남기고 무시 (예: 1990년 이전)
                if parsed.year >= 1990:
                    published_at = parsed
                    break # 첫 번째 성공한 파싱 결과 사용
                else:
                     logger.debug(f"  ⚠️ Skipping date parse due to unlikely year: {parsed} from field '{field}'")

    # 결과 딕셔너리 반환 (raw_text 추가)
    result = {
        "title": title,
        "url": url,
        "body_html": body_html,
        "raw_text": raw_text, # ⭐️ 원본 텍스트(content)
        "body_text": body_text, # ⭐️ 정제된 텍스트
        "published_at": published_at, # 파싱된 datetime 객체 또는 None
    }
    return result

def validate_normalized_item(item: dict) -> bool:
    """정규화된 item의 필수 필드 및 유효성 검증"""
    # 제목과 URL은 필수
    if not item.get("title") or not item.get("url"):
        logger.warning(f"  ⚠️ Skipping item due to missing title or URL: {item.get('url') or 'No URL'}")
        return False

    # URL 형식 검증 (scheme, netloc)
    parsed_url = urlparse(item["url"])
    if not parsed_url.scheme or not parsed_url.netloc:
        logger.warning(f"  ⚠️ Skipping item due to invalid URL: {item['url']}")
        return False

    # 너무 짧은 제목 무시 (오류 가능성)
    if len(item["title"]) < 3:
         logger.debug(f"  ⚠️ Skipping item due to short title: {item['title']}")
         return False

    # 발행일 유효성 검증 (datetime 객체이고, 너무 오래되지 않음)
    if item.get("published_at"):
        pub_dt = item["published_at"]
        # datetime 객체가 아니면 실패
        if not isinstance(pub_dt, datetime):
             logger.warning(f"  ⚠️ Skipping item due to invalid date type: {type(pub_dt)}")
             return False
        # 타임존 정보 강제 (UTC)
        if pub_dt.tzinfo is None or pub_dt.tzinfo.utcoffset(pub_dt) is None:
             item["published_at"] = pub_dt.replace(tzinfo=timezone.utc) # 원본 item 딕셔너리 직접 수정
        # 너무 오래된 날짜 무시
        if item["published_at"].year < 1990:
             logger.debug(f"  ⚠️ Skipping item due to very old date: {item['published_at']}")
             return False

    # 모든 검증 통과
    return True

def content_hash(college_key: str, title: str, url: str, published_at: Optional[datetime]) -> str:
    """공지사항 내용 기반 고유 해시 생성 (중복 방지용)"""
    url = url.rstrip('/') # URL 끝 '/' 제거하여 정규화
    date_str = published_at.strftime('%Y-%m-%d') if published_at else "" # 날짜 부분만 사용 (시간 제외)
    # 제목 정규화: 특정 문자 제거, 소문자 변환, 공백 정리
    title_normalized = re.sub(r'[\'\"\[\]''"""]', '', title) # 따옴표, 대괄호 제거
    title_normalized = re.sub(r'\s+', ' ', title_normalized.lower().strip()) # 소문자, 공백 정리

    # 해시 생성 기준 문자열 조합
    base = f"{college_key}|{title_normalized}|{url}|{date_str}"
    h = hashlib.sha256(base.encode('utf-8')).hexdigest()
    return h


# --- 기존 Apify 헬퍼 함수 (get_latest_run_for_task, fetch_dataset_items) ---
def get_latest_run_for_task(task_id: str, timeout=60):
    """Apify Task의 가장 최근 성공한 Run 정보 가져오기"""
    url = f"https://api.apify.com/v2/actor-tasks/{task_id}/runs"
    params = {"token": APIFY_TOKEN, "limit": 1, "desc": "true"} # 최신 1개만
    try:
        resp = SESSION.get(url, params=params, timeout=timeout)
        resp.raise_for_status() # HTTP 오류 발생 시 예외 발생
        data = resp.json()
        runs = data.get("data", {}).get("items", [])
    except requests.RequestException as e:
        logger.error(f"  ❌ Error fetching runs for task {task_id}: {e}")
        return None
    except json.JSONDecodeError:
        logger.warning(f"  ⚠️ Invalid JSON response for task {task_id} runs")
        return None

    if not runs:
        logger.warning(f"  ⚠️ No recent run found for task {task_id}")
        return None

    latest_run = runs[0]
    status = latest_run.get("status", "UNKNOWN")
    # 성공한 Run만 반환
    if status == "SUCCEEDED":
        return latest_run
    else:
        run_id = latest_run.get("id", "N/A")
        logger.warning(f"  ⚠️ Latest run {run_id} for task {task_id} status: {status}. Skipping.")
        return None

def fetch_dataset_items(dataset_id: str, timeout=300):
    """Apify Dataset의 모든 아이템 가져오기 (페이징 처리)"""
    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    params = {"token": APIFY_TOKEN, "format": "json", "clean": "true", "limit": 1000} # 한 번에 1000개씩
    all_items = []
    offset = 0
    max_items_limit = 5000 # 최대 5000개 아이템 제한 (너무 많은 데이터 방지)

    while True:
        try:
            current_params = params.copy()
            current_params["offset"] = offset
            logger.info(f"  Fetching items... offset={offset}, limit={current_params.get('limit')}")

            resp = SESSION.get(url, params=current_params, timeout=timeout)
            resp.raise_for_status()
            items_data = resp.json() # JSON 파싱

            # 응답 데이터 형식 확인 (리스트 또는 딕셔너리)
            current_batch = []
            if isinstance(items_data, list):
                current_batch = items_data
            elif isinstance(items_data, dict) and 'items' in items_data:
                # Apify 최신 API는 'items' 키를 포함할 수 있음
                current_batch = items_data['items']
                if not isinstance(current_batch, list):
                    logger.warning(f"  ⚠️ 'items' key found but not a list in dataset {dataset_id}")
                    break
            else:
                 logger.warning(f"  ⚠️ Unexpected data format from dataset {dataset_id}: {type(items_data)}")
                 break

            if not current_batch:
                 logger.info("  No more items found in this batch.")
                 break # 더 이상 아이템이 없으면 종료

            all_items.extend(current_batch)
            logger.info(f"  Fetched {len(current_batch)} items. Total now: {len(all_items)}")

            # 다음 페이징 오프셋 설정
            offset += len(current_batch) # 실제 가져온 개수만큼 증가

            # 최대 아이템 제한 확인
            if len(all_items) >= max_items_limit:
                 logger.warning(f"  ⚠️ Reached max items limit ({max_items_limit}). Stopping fetch.")
                 break

            # API 호출 간 짧은 지연 (Rate limit 방지)
            time.sleep(0.5)

        except requests.RequestException as e:
            logger.error(f"  ⚠️ Items fetch error for dataset {dataset_id} at offset {offset}: {e}")
            break # 네트워크 오류 시 중단
        except json.JSONDecodeError:
            logger.error(f"  ⚠️ Items JSON decode error for dataset {dataset_id} at offset {offset}")
            break # JSON 파싱 오류 시 중단
        except Exception as e:
            logger.error(f"  ⚠️ Unexpected error fetching items for dataset {dataset_id}: {e}")
            break # 예상치 못한 오류 시 중단

    logger.info(f"  Total items fetched for dataset {dataset_id}: {len(all_items)}")
    return all_items


# --- 메인 실행 함수 (run) ---
def run(
    job_dataset_id: Optional[str] = None,
    job_task_id: Optional[str] = None,
    job_run_id: Optional[str] = None,
    job_finished_at: Optional[str] = None,
):
    total_upserted = 0
    total_skipped = 0
    total_ai_batches = 0 # AI 배치 호출 횟수

    print(f"🤖 AI_IN_PIPELINE: {AI_IN_PIPELINE} (Title-based Hashtag Classification - Batch API calls)")
    print(f"⏱️ AI_SLEEP_SEC (between batches): {AI_SLEEP_SEC}")
    print(f"🔢 AI_BATCH_SIZE: {AI_BATCH_SIZE}")

    conn = None
    try:
        # 데이터베이스 연결
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False # 트랜잭션 관리를 위해 autocommit 비활성화

        # RealDictCursor 사용: 결과를 딕셔너리 형태로 받음
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # colleges.py에 정의된 각 대학별로 처리
            if job_dataset_id and not job_task_id:
                logger.warning("  ⚠️ Queue job provided dataset_id without actor_task_id. Processing all colleges.")

            matched_college = False

            for ck, meta in COLLEGES.items():
                college_name = meta.get("name", "Unknown College")
                task_id = meta.get("task_id")
                site = meta.get("url") # 대학별 기본 URL

                # task_id가 없으면 해당 대학 건너뛰기
                if not task_id:
                    logger.warning(f"Skipping college {college_name} ({ck}) due to missing task_id.")
                    continue

                if job_task_id and task_id != job_task_id:
                    continue

                print(f"\n🔍 Processing college: {college_name} ({ck})")

                run_id = None
                ds_id = None
                finished_at_display = "unknown"

                if job_dataset_id and (not job_task_id or task_id == job_task_id):
                    matched_college = True
                    run_id = job_run_id
                    ds_id = job_dataset_id
                    finished_at_display = job_finished_at or "from queue"
                else:
                    # 가장 최근 성공한 Apify Run 정보 가져오기
                    run_data = get_latest_run_for_task(task_id)
                    if not run_data:
                        # 최근 성공 Run이 없으면 건너뛰기
                        continue

                    run_id = run_data.get("id")
                    ds_id = run_data.get("defaultDatasetId")
                    finished_at_str = run_data.get("finishedAt", "unknown time")

                    # Run 완료 시간 표시 (파싱 시도)
                    try:
                         finished_at_dt = datetime.fromisoformat(finished_at_str.replace("Z", "+00:00"))
                         finished_at_display = finished_at_dt.strftime('%Y-%m-%d %H:%M:%S %Z')
                    except:
                         finished_at_display = finished_at_str

                if not ds_id:
                    logger.error(f"  ❌ No datasetId available for task {task_id} / college {ck}")
                    continue

                logger.info(f"  📅 Using data from run {run_id} (Finished: {finished_at_display})")

                # 데이터셋 아이템 가져오기
                items = fetch_dataset_items(ds_id)
                if not items:
                     logger.warning(f"  ⚠️ No items fetched from dataset {ds_id}. Skipping college.")
                     continue
                logger.info(f"  📦 Total items retrieved: {len(items)}")

                college_upserted = 0
                college_skipped = 0
                ai_call_count_batch = 0 # 현재 대학의 AI 배치 호출 수
                processed_items_data = [] # 정규화 및 유효성 검사 통과한 아이템 저장
                items_to_process_ai = [] # AI 처리가 필요한 아이템 정보 저장

                # --- 1단계: 아이템 정규화, 유효성 검사, 해시 생성 및 AI 처리 대상 선별 ---
                logger.info("  Preprocessing items...")
                processed_hashes_in_run = set() # 현재 Run 내에서 중복 해시 방지

                for item_index, rec in enumerate(items):
                    # 아이템 정규화 시도 (ck 전달)
                    try:
                        norm = normalize_item(rec, base_url=site, college_key=ck)
                    except Exception as norm_err:
                        logger.error(f"  ❌ Error normalizing item {item_index+1}: {norm_err}")
                        college_skipped += 1
                        continue

                    # 유효성 검사
                    if not validate_normalized_item(norm):
                        college_skipped += 1
                        continue

                    # 콘텐츠 해시 생성 및 중복 확인
                    try:
                        h = content_hash(ck, norm["title"], norm["url"], norm.get("published_at"))
                        # 현재 Run 내에서 이미 처리된 해시면 건너뛰기
                        if h in processed_hashes_in_run:
                             logger.debug(f"  ⚠️ Skipping duplicate hash within this run: {norm['title'][:30]}...")
                             college_skipped += 1
                             continue
                        processed_hashes_in_run.add(h) # 처리된 해시로 추가

                        # DB 저장을 위해 필요한 정보 추가
                        norm['hash'] = h
                        norm['college_key'] = ck
                        norm['source_site'] = site # source_site 추가
                        processed_items_data.append(norm)

                        # AI 처리 대상 선별 (AI_IN_PIPELINE 활성화 및 제목 존재 시)
                        if AI_IN_PIPELINE and norm.get("title", "").strip():
                            body_for_ai = norm.get("body_text") or norm.get("raw_text") or ""
                            body_for_ai = clean_text(body_for_ai, max_length=1200)
                            items_to_process_ai.append({
                                "id": h, # 해시값을 AI 결과 매핑용 ID로 사용
                                "title": norm["title"],
                                "college_name": college_name, # 단과대 이름도 AI 컨텍스트로 제공
                                "body": body_for_ai,
                            })
                    except Exception as hash_err:
                        logger.error(f"  ❌ Error generating hash for item {item_index+1} ('{norm.get('title', 'N/A')[:30]}...'): {hash_err}")
                        college_skipped += 1
                        continue

                logger.info(f"  Preprocessing done. Valid items: {len(processed_items_data)}, AI targets: {len(items_to_process_ai)}")

                # --- 2단계: 제목 기반 해시태그 배치 처리 ---
                ai_results_map = {} # { "hash_id": ["#태그1", "#태그2"], ... } 형태로 결과 저장
                if AI_IN_PIPELINE and items_to_process_ai:
                    logger.info(f"  Starting AI batch classification (Batch size: {AI_BATCH_SIZE})...")
                    num_batches = (len(items_to_process_ai) + AI_BATCH_SIZE - 1) // AI_BATCH_SIZE
                    total_ai_batches += num_batches # 전체 배치 수 누적

                    for i in range(num_batches):
                        batch_start_index = i * AI_BATCH_SIZE
                        batch_end_index = batch_start_index + AI_BATCH_SIZE
                        current_batch_input = items_to_process_ai[batch_start_index:batch_end_index]

                        if not current_batch_input:
                            continue

                        logger.info(f"  Processing AI Batch {i+1}/{num_batches} ({len(current_batch_input)} items)...")
                        ai_call_count_batch += 1 # 현재 대학 배치 호출 수 증가

                        # --- API 호출 (재시도 로직 포함) ---
                        retry_count = 0
                        max_retries = AI_MAX_RETRIES # 재시도 횟수 (환경변수로 조정 가능)
                        batch_success = False
                        while retry_count <= max_retries:
                            try:
                                # 첫 배치가 아니거나 재시도 시 지연
                                if i > 0 or retry_count > 0:
                                    sleep_duration = AI_SLEEP_SEC * (retry_count + 1) # 재시도 시 더 길게 대기
                                    logger.debug(f"    Sleeping for {sleep_duration:.1f}s before AI call...")
                                    time.sleep(sleep_duration)

                                # 배치 분류 함수 호출
                                batch_result = classify_hashtags_from_title_batch(current_batch_input)
                                ai_results_map.update(batch_result) # 결과 맵에 추가
                                batch_success = True
                                logger.info(f"  Batch {i+1} completed successfully.")
                                break # 성공 시 재시도 루프 탈출

                            except Exception as e:
                                # Rate limit 오류 (HTTP 429) 처리
                                if "429" in str(e) or "rate limit" in str(e).lower():
                                    retry_count += 1
                                    if retry_count <= max_retries:
                                        wait_time = (2 ** retry_count) * 5 # Exponential backoff (5s, 10s, 20s)
                                        logger.warning(f"  ⚠️ Rate limit on Batch {i+1}. Retrying in {wait_time}s... ({retry_count}/{max_retries})")
                                        time.sleep(wait_time)
                                    else:
                                         logger.error(f"  ❌ Max retries ({max_retries}) reached for Batch {i+1} due to rate limit. Skipping AI for this batch.")
                                         break # 최대 재시도 도달 시 포기
                                else:
                                    # 그 외 AI 오류
                                    logger.error(f"  ❌ AI batch classification failed for Batch {i+1}: {e}. Skipping AI for this batch.")
                                    break # 복구 불가능 오류 시 포기

                        # --- 재시도 로직 종료 ---
                        if not batch_success:
                            # 배치 처리에 실패한 아이템들에 대해 빈 리스트([]) 결과 설정 (DB 오류 방지)
                            for item_info in current_batch_input:
                                if item_info['id'] not in ai_results_map:
                                    ai_results_map[item_info['id']] = []


                logger.info("  AI Step 1 finished.")

                # --- 3단계: AI 후처리 결과 매핑 준비 ---
                category_map: Dict[str, Optional[str]] = {}
                structured_info_map: Dict[str, Dict[str, Any]] = {}
                detailed_hashtags_map: Dict[str, List[str]] = {}

                for norm_item in processed_items_data:
                    item_hash = norm_item.get("hash")
                    if not item_hash:
                        continue

                    hashtags_ai_raw = ai_results_map.get(item_hash, [])
                    if not isinstance(hashtags_ai_raw, list):
                        logger.warning(
                            f"  ⚠️ Hashtags for {item_hash} is not a list ({type(hashtags_ai_raw)}), forcing to []."
                        )
                        hashtags_ai_raw = []

                    if hashtags_ai_raw == ["#일반"]:
                        main_category = "#일반"
                    elif hashtags_ai_raw:
                        main_category = hashtags_ai_raw[0]
                    else:
                        main_category = None

                    category_map[item_hash] = main_category
                    structured_info_map[item_hash] = {}
                    detailed_hashtags_map[item_hash] = []

                # --- 4단계: 자격요건 추출 ---
                if AI_IN_PIPELINE and processed_items_data:
                    logger.info("  AI Step 2 (qualification extraction) starting...")
                    step2_processed = 0

                    for idx, norm_item in enumerate(processed_items_data):
                        item_hash = norm_item.get("hash")
                        if not item_hash:
                            continue

                        title_for_ai = norm_item.get("title") or ""
                        body_for_ai = (
                            norm_item.get("body_text")
                            or norm_item.get("raw_text")
                            or ""
                        )

                        if not (title_for_ai.strip() or body_for_ai.strip()):
                            continue

                        main_category = category_map.get(item_hash) or "#일반"

                        attempt = 0
                        extracted_info: Optional[Dict[str, Any]] = None
                        while attempt <= AI_MAX_RETRIES:
                            if attempt > 0:
                                wait_time = max(AI_STEP2_SLEEP_SEC, (2 ** attempt) * AI_STEP2_SLEEP_SEC)
                                logger.warning(
                                    f"    ⚠️ Step 2 retry for item {item_hash[:8]}..., waiting {wait_time:.1f}s ({attempt}/{AI_MAX_RETRIES})"
                                )
                                if wait_time > 0:
                                    time.sleep(wait_time)
                            else:
                                if idx > 0 and AI_STEP2_SLEEP_SEC > 0:
                                    time.sleep(AI_STEP2_SLEEP_SEC)

                            try:
                                result = extract_structured_info(title_for_ai, body_for_ai, main_category)
                                if isinstance(result, dict) and "error" not in result:
                                    extracted_info = result
                                else:
                                    extracted_info = {}
                                break
                            except Exception as e:
                                if "429" in str(e) or "rate limit" in str(e).lower():
                                    attempt += 1
                                    continue
                                logger.error(
                                    f"    ❌ Step 2 extraction failed for item {item_hash[:8]}...: {e}"
                                )
                                extracted_info = {}
                                break

                        if extracted_info is not None:
                            structured_info_map[item_hash] = extracted_info
                        step2_processed += 1

                    logger.info(f"  AI Step 2 processed {step2_processed} items.")

                # --- 5단계: 세부 해시태그 추출 ---
                if AI_IN_PIPELINE and processed_items_data:
                    logger.info("  AI Step 3 (detailed hashtags) starting...")
                    step3_processed = 0

                    for idx, norm_item in enumerate(processed_items_data):
                        item_hash = norm_item.get("hash")
                        if not item_hash:
                            continue

                        main_category = category_map.get(item_hash)
                        if not main_category or main_category == "#일반":
                            continue

                        title_for_ai = norm_item.get("title") or ""
                        body_for_ai = (
                            norm_item.get("body_text")
                            or norm_item.get("raw_text")
                            or ""
                        )

                        if not (title_for_ai.strip() or body_for_ai.strip()):
                            continue

                        attempt = 0
                        detailed_result: List[str] = []
                        while attempt <= AI_MAX_RETRIES:
                            if attempt > 0:
                                wait_time = max(AI_STEP3_SLEEP_SEC, (2 ** attempt) * AI_STEP3_SLEEP_SEC)
                                logger.warning(
                                    f"    ⚠️ Step 3 retry for item {item_hash[:8]}..., waiting {wait_time:.1f}s ({attempt}/{AI_MAX_RETRIES})"
                                )
                                if wait_time > 0:
                                    time.sleep(wait_time)
                            else:
                                if idx > 0 and AI_STEP3_SLEEP_SEC > 0:
                                    time.sleep(AI_STEP3_SLEEP_SEC)

                            try:
                                detailed_result = extract_detailed_hashtags(
                                    title_for_ai,
                                    body_for_ai,
                                    main_category,
                                ) or []
                                break
                            except Exception as e:
                                if "429" in str(e) or "rate limit" in str(e).lower():
                                    attempt += 1
                                    continue
                                logger.error(
                                    f"    ❌ Step 3 extraction failed for item {item_hash[:8]}...: {e}"
                                )
                                detailed_result = []
                                break

                        if detailed_result:
                            detailed_hashtags_map[item_hash] = detailed_result
                        step3_processed += 1

                    logger.info(f"  AI Step 3 processed {step3_processed} items.")

                # --- 6단계: DB 저장 루프 (오류 수정 및 로깅 강화) ---
                logger.info("  Upserting data into database...")
                for norm_item in processed_items_data:
                    item_hash = norm_item.get('hash')
                    # 해시 없으면 처리 불가
                    if not item_hash:
                        logger.warning(f"  ⚠️ Skipping item due to missing hash (should not happen): {norm_item.get('title', 'N/A')[:30]}...")
                        college_skipped += 1
                        continue

                    # AI 결과 가져오기 (기본값 설정 강화)
                    hashtags_ai = ai_results_map.get(item_hash, []) # 기본값 빈 리스트
                    if not isinstance(hashtags_ai, list):
                        logger.warning(f"  ⚠️ Hashtags for {item_hash} is not a list ({type(hashtags_ai)}), forcing to []. AI Map: {ai_results_map.get(item_hash)}")
                        hashtags_ai = []

                    main_category = category_map.get(item_hash)

                    # 카테고리 설정 (해시태그 리스트 기반)
                    category_ai = main_category if main_category and main_category != "#일반" else None

                    # 일정 필드 (현재는 파싱 미적용)
                    start_at_ai = None
                    end_at_ai = None

                    # 자격요건/세부태그 결과
                    qualification_ai = structured_info_map.get(item_hash, {})
                    if not isinstance(qualification_ai, dict):
                        qualification_ai = {}

                    detailed_hashtags = detailed_hashtags_map.get(item_hash, [])
                    if not isinstance(detailed_hashtags, list):
                        detailed_hashtags = []
                    detailed_hashtags_db = detailed_hashtags if detailed_hashtags else None

                    # ⭐️ [수정] DB 저장 시도: 파라미터에서 search_vector 관련 제거
                    try:
                        cur.execute(UPSERT_SQL, {
                            "college_key": norm_item.get('college_key'), # college_key 확인
                            "title": norm_item.get("title"),
                            "url": norm_item.get("url"),
                            "body_html": norm_item.get("body_html"),
                            "body_text": norm_item.get("body_text"), # 정제된 텍스트
                            "raw_text": norm_item.get("raw_text"), # ⭐️ 원본 텍스트
                            "published_at": norm_item.get("published_at"),
                            "source_site": norm_item.get('source_site'), # source_site 확인
                            "content_hash": item_hash,
                            "category_ai": category_ai,
                            "start_at_ai": start_at_ai,
                            "end_at_ai": end_at_ai,
                            "qualification_ai": Json(qualification_ai), # Json() 사용 (이제 qualification_ai는 dict)
                            "hashtags_ai": hashtags_ai, # 리스트 또는 빈 리스트
                            "detailed_hashtags": detailed_hashtags_db,
                        })
                        # cur.rowcount > 0 이면 실제로 INSERT 또는 UPDATE 발생
                        # logger.debug(f"Upsert executed for hash {item_hash}. Row count: {cur.rowcount}")
                        college_upserted += 1 # 실행 자체를 성공으로 카운트 (ON CONFLICT DO UPDATE도 포함)

                    except psycopg2.Error as db_err:
                        conn.rollback() # 현재 아이템 롤백 (트랜잭션 유지)
                        # 상세한 DB 오류 로그 출력
                        pgcode = getattr(db_err, 'pgcode', 'N/A')
                        pgerror = getattr(db_err, 'pgerror', str(db_err)).strip()
                        diag = getattr(db_err, 'diag', None)
                        diag_message = diag.message_detail if diag and hasattr(diag, 'message_detail') else pgerror

                        logger.error(f"  ❌ DB error upserting '{norm_item.get('title', 'N/A')[:30]}...' (Hash: {item_hash}):")
                        logger.error(f"     Code: {pgcode}, Detail: {diag_message}")
                        college_skipped += 1
                        # 여기서 continue 또는 break 결정 가능 (일단 계속 진행)
                    except Exception as general_err:
                         conn.rollback() # 현재 아이템 롤백
                         logger.error(f"  ❌ Unexpected error during upsert for '{norm_item.get('title', 'N/A')[:30]}...': {general_err}")
                         college_skipped += 1
                         # 여기서 continue 또는 break 결정 가능 (일단 계속 진행)

                # --- DB 저장 루프 종료 ---

                # 한 대학 처리 후 커밋 (오류 발생 시 롤백되었으므로 성공한 것만 커밋됨)
                conn.commit()
                logger.info(f"  ✅ Finished {college_name}: Upserted attempts={college_upserted}, Skipped={college_skipped}, AI Batches={ai_call_count_batch}")

                # 큐 작업으로 실행된 경우, 대상 단과대 처리 후 종료
                if job_task_id and task_id == job_task_id:
                    break

            if job_task_id and not matched_college:
                logger.warning(f"  ⚠️ No college matched actor_task_id={job_task_id}.")

                total_upserted += college_upserted
                total_skipped += college_skipped

            # --- 모든 대학 처리 루프 종료 ---

    except psycopg2.Error as db_conn_err:
        # 데이터베이스 연결 자체의 문제
        logger.critical(f"\n❌ Database connection error: {db_conn_err}")
        # 이 경우 추가 처리가 어려우므로 종료
    except KeyboardInterrupt:
        # 사용자가 Ctrl+C 등으로 중단 시
        logger.warning("\n🚫 Operation cancelled by user.")
        if conn:
            conn.rollback() # 진행 중이던 트랜잭션 롤백
    except Exception as e:
        # 그 외 예상치 못한 오류
        logger.exception(f"\n❌ An unexpected error occurred during the run: {e}") # 스택 트레이스 포함
        if conn:
            conn.rollback() # 진행 중이던 트랜잭N 롤백
    finally:
        # 항상 데이터베이스 연결 종료
        if conn:
            conn.close()
            logger.info("Database connection closed.")

    # 최종 결과 출력
    print(f"\n✨ Script finished.")
    print(f"Total upsert attempts: {total_upserted}")
    print(f"Total skipped items: {total_skipped}")
    print(f"Total AI batch calls: {total_ai_batches}")

# 스크립트 실행 시작점
if __name__ == "__main__":
    run()