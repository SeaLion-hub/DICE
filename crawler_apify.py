# crawler_apify.py (수정된 run 함수 포함)
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
    clean_json_string # clean_json_string 임포트 추가 (ai_processor.py에 수정된 함수가 있다고 가정)
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


load_dotenv(encoding="utf-8")

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
AI_IN_PIPELINE = os.getenv("AI_IN_PIPELINE", "true").lower() == "true"
AI_SLEEP_SEC = float(os.getenv("AI_SLEEP_SEC", "1.0"))
AI_BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", "10"))

if not APIFY_TOKEN:
    raise RuntimeError("APIFY_TOKEN not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# 로거 설정 추가
logger = logging.getLogger(__name__) # __name__으로 로거 이름 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

# AI 필드 포함된 UPSERT SQL (기존과 동일)
UPSERT_SQL = """
INSERT INTO notices (
    college_key, title, url, summary_raw, body_html, body_text,
    published_at, source_site, content_hash,
    category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai,
    search_vector
) VALUES (
    %(college_key)s, %(title)s, %(url)s, %(summary_raw)s,
    %(body_html)s, %(body_text)s, %(published_at)s,
    %(source_site)s, %(content_hash)s,
    %(category_ai)s, %(start_at_ai)s, %(end_at_ai)s, %(qualification_ai)s, %(hashtags_ai)s,
    setweight(to_tsvector('simple', coalesce(%(title)s, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(array_to_string(%(hashtags_ai)s, ' '), '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(%(body_text)s, '')), 'C')
)
ON CONFLICT (content_hash)
DO UPDATE SET
    title = EXCLUDED.title,
    url = EXCLUDED.url,
    summary_raw = EXCLUDED.summary_raw,
    body_html = EXCLUDED.body_html,
    body_text = EXCLUDED.body_text,
    published_at = EXCLUDED.published_at,
    category_ai = EXCLUDED.category_ai, -- category_ai 추가
    start_at_ai = EXCLUDED.start_at_ai, -- start_at_ai 추가
    end_at_ai = EXCLUDED.end_at_ai,   -- end_at_ai 추가
    qualification_ai = EXCLUDED.qualification_ai, -- qualification_ai 추가
    hashtags_ai = EXCLUDED.hashtags_ai, -- hashtags_ai 추가
    updated_at = CURRENT_TIMESTAMP,
    search_vector = setweight(to_tsvector('simple', coalesce(EXCLUDED.title, '')), 'A') ||
                    setweight(to_tsvector('simple', coalesce(array_to_string(EXCLUDED.hashtags_ai, ' '), '')), 'B') ||
                    setweight(to_tsvector('simple', coalesce(EXCLUDED.body_text, '')), 'C')
RETURNING id; -- Optional: 반환 값 추가하여 업데이트 확인 가능
"""

# --- 기존 헬퍼 함수들 (clean_text, extract_text_from_html, ...) ---
# 여기에 기존 헬퍼 함수들이 있다고 가정합니다. (코드가 너무 길어져 생략)
def clean_text(text: Optional[str], max_length: Optional[int] = None) -> str:
    if not text: return ""
    text = unescape(text) # HTML 엔티티 디코딩
    text = re.sub(r'\s+', ' ', text) # 연속 공백을 하나로
    text = text.strip() # 앞뒤 공백 제거
    if max_length and len(text) > max_length: # 길이 제한
        text = text[:max_length-3] + "..."
    return text

def extract_text_from_html(html: Optional[str]) -> str:
    """HTML에서 주요 텍스트 내용만 추출 시도"""
    if not html: return ""
    try:
        # 특정 패턴('게시글 내용' ~ '목록') 사이 내용 우선 추출 시도
        content_pattern = r'게시글 내용(.*?)목록'
        content_match = re.search(content_pattern, html, re.DOTALL)
        soup_text = html
        if content_match:
            soup_text = content_match.group(1).strip()

        # BeautifulSoup으로 파싱
        soup = BeautifulSoup(soup_text, 'html.parser')

        # 불필요한 태그 제거
        for tag in soup(['script', 'style', 'meta', 'link', 'header', 'footer', 'nav', 'aside']):
            tag.decompose()

        # 텍스트 추출 및 정리
        text = soup.get_text(separator=' ', strip=True)
        # 불필요한 파이프 문자 정리 (예: ' | ')
        text = re.sub(r'(\s*\|\s*)+', ' ', text)
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

def normalize_item(item: dict, base_url: Optional[str] = None) -> dict:
    """Apify 크롤링 결과 item을 표준 형식으로 정규화"""
    # 제목 추출 (여러 필드명 후보 사용)
    title = clean_text(extract_field(item, ["title", "name", "subject", "headline", "meta.title", "og:title", "titleText", "h1", "h2"], default=""), max_length=500)
    # URL 추출 및 정규화
    url = normalize_url(extract_field(item, ["url", "link", "href", "permalink", "canonical", "meta.url", "og:url"], default=""), base_url)
    # 요약 추출
    summary_raw = clean_text(extract_field(item, ["summary", "description", "excerpt", "preview", "meta.description", "og:description", "abstract"], default=""), max_length=1000)
    # HTML 본문 추출
    body_html = extract_field(item, ["html", "content_html", "body_html", "htmlContent", "content", "text"], default=None) # HTML은 그대로 유지 시도
    # 텍스트 본문 추출 (raw text 우선, 없으면 HTML에서 추출)
    body_text_raw = extract_field(item, ["text", "content", "body", "body_text", "plainText"], default=None)
    body_text = clean_text(body_text_raw) # raw text 클리닝

    # HTML이 있고, 거기서 추출한 텍스트가 더 길거나 raw text가 없으면 HTML 기반 텍스트 사용
    if body_html:
        body_text_from_html = extract_text_from_html(body_html)
        if len(body_text_from_html) > len(body_text):
            body_text = body_text_from_html
        elif not body_text and body_text_from_html: # raw text가 없고 HTML text만 있을 때
            body_text = body_text_from_html

    # 요약이 없고 본문 텍스트가 있으면 본문 앞부분을 요약으로 사용
    if body_text and not summary_raw:
        summary_raw = clean_text(body_text[:500]) # 500자로 제한

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

    # 결과 딕셔너리 반환
    result = {
        "title": title,
        "url": url,
        "summary_raw": summary_raw,
        "body_html": body_html,
        "body_text": body_text,
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
    title_normalized = re.sub(r'[\'\"\[\]‘’“”]', '', title) # 따옴표, 대괄호 제거
    title_normalized = re.sub(r'\s+', ' ', title_normalized.lower().strip()) # 소문자, 공백 정리

    # 해시 생성 기준 문자열 조합
    base = f"{college_key}|{title_normalized}|{url}|{date_str}"
    h = hashlib.sha256(base.encode('utf-8')).hexdigest()
    return h


# --- 기존 Apify 헬퍼 함수 (get_latest_run_for_task, fetch_dataset_items) ---
# 여기에 기존 Apify 관련 함수들이 있다고 가정합니다. (코드가 너무 길어져 생략)
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

            # 다음 페이지 오프셋 설정
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
def run():
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
            for ck, meta in COLLEGES.items():
                college_name = meta.get("name", "Unknown College")
                task_id = meta.get("task_id")
                site = meta.get("url") # 대학별 기본 URL

                # task_id가 없으면 해당 대학 건너뛰기
                if not task_id:
                    logger.warning(f"Skipping college {college_name} ({ck}) due to missing task_id.")
                    continue

                print(f"\n🔍 Processing college: {college_name} ({ck})")

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
                    logger.error(f"  ❌ No datasetId found for the latest successful run {run_id}")
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
                    # 아이템 정규화 시도
                    try:
                        norm = normalize_item(rec, base_url=site)
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
                            items_to_process_ai.append({
                                "id": h, # 해시값을 AI 결과 매핑용 ID로 사용
                                "title": norm["title"],
                                "college_name": college_name # 단과대 이름도 AI 컨텍스트로 제공
                            })
                    except Exception as hash_err:
                        logger.error(f"  ❌ Error generating hash for item {item_index+1} ('{norm.get('title', 'N/A')[:30]}...'): {hash_err}")
                        college_skipped += 1
                        continue

                logger.info(f"  Preprocessing done. Valid items: {len(processed_items_data)}, AI targets: {len(items_to_process_ai)}")

                # --- 2단계: AI 배치 처리 ---
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
                        max_retries = 2 # 재시도 횟수 증가 (최대 2번)
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


                logger.info("  AI processing finished.")

                # --- 3단계: DB 저장 루프 (오류 수정 및 로깅 강화) ---
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
                    # 결과가 리스트가 아니면 빈 리스트로 강제 변환
                    if not isinstance(hashtags_ai, list):
                        logger.warning(f"  ⚠️ Hashtags for {item_hash} is not a list ({type(hashtags_ai)}), forcing to []. AI Map: {ai_results_map.get(item_hash)}")
                        hashtags_ai = []

                    # 카테고리 설정 (해시태그 리스트 기반)
                    category_ai = hashtags_ai[0] if hashtags_ai and hashtags_ai != ["#일반"] else None # #일반 태그만 있으면 카테고리는 None

                    # 기타 AI 필드 (현재 로직에서는 None 또는 빈 dict)
                    start_at_ai = None
                    end_at_ai = None
                    # qualification_ai 처리 (항상 dict 보장)
                    # 현재 AI 배치 결과에는 qualification_ai가 없으므로 빈 dict 사용
                    raw_qualification_ai = {} # <<-- 이 부분은 나중에 자격요건 추출 로직 추가 시 수정 필요
                    if not isinstance(raw_qualification_ai, dict):
                        logger.warning(f"  ⚠️ Qualification AI result for {item_hash} was not a dict (type: {type(raw_qualification_ai)}), using empty dict.")
                        qualification_ai = {}
                    else:
                        qualification_ai = raw_qualification_ai

                    # DB 저장 시도 (try-except 블록 강화)
                    try:
                        cur.execute(UPSERT_SQL, {
                            "college_key": norm_item.get('college_key'), # college_key 확인
                            "title": norm_item.get("title"),
                            "url": norm_item.get("url"),
                            "summary_raw": norm_item.get("summary_raw"),
                            "body_html": norm_item.get("body_html"),
                            "body_text": norm_item.get("body_text"),
                            "published_at": norm_item.get("published_at"),
                            "source_site": norm_item.get('source_site'), # source_site 확인
                            "content_hash": item_hash,
                            "category_ai": category_ai,
                            "start_at_ai": start_at_ai,
                            "end_at_ai": end_at_ai,
                            "qualification_ai": Json(qualification_ai), # Json() 사용 (이제 qualification_ai는 dict)
                            "hashtags_ai": hashtags_ai, # 리스트 또는 빈 리스트
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
            conn.rollback() # 진행 중이던 트랜잭션 롤백
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