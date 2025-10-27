# crawler_apify.py (최신 데이터 가져오기 + 최신 AI 처리 방식 통합)
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

# AI processor import 수정 (최신 함수 사용)
from ai_processor import (
    classify_notice_category,
    extract_structured_info,
)
# _to_utc_ts 함수는 main.py 또는 backfill_ai.py 등 다른 곳에서 가져오거나 여기에 정의해야 합니다.
# 여기서는 backfill_ai.py의 함수를 가져옵니다.
from backfill_ai import _to_utc_ts # _to_utc_ts 임포트 또는 직접 정의 필요

load_dotenv(encoding="utf-8")

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
AI_IN_PIPELINE = os.getenv("AI_IN_PIPELINE", "true").lower() == "true"
AI_SLEEP_SEC = float(os.getenv("AI_SLEEP_SEC", "0.8"))
AI_MAX_PER_COLLEGE = int(os.getenv("AI_MAX_PER_COLLEGE", "999999")) # AI 호출 제한

if not APIFY_TOKEN:
    raise RuntimeError("APIFY_TOKEN not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

# AI 필드 포함된 UPSERT SQL (search_vector 포함하여 main.py와 유사하게)
UPSERT_SQL = """
INSERT INTO notices (
    college_key, title, url, summary_raw, body_html, body_text,
    published_at, source_site, content_hash,
    category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai,
    search_vector -- search_vector 추가
) VALUES (
    %(college_key)s, %(title)s, %(url)s, %(summary_raw)s,
    %(body_html)s, %(body_text)s, %(published_at)s,
    %(source_site)s, %(content_hash)s,
    %(category_ai)s, %(start_at_ai)s, %(end_at_ai)s, %(qualification_ai)s, %(hashtags_ai)s,
    setweight(to_tsvector('simple', coalesce(%(title)s, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(array_to_string(%(hashtags_ai)s, ' '), '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(%(body_text)s, '')), 'C') -- search_vector 값 생성 로직 추가
)
ON CONFLICT (content_hash)
DO UPDATE SET
    title = EXCLUDED.title, -- title 등 다른 필드도 업데이트되도록 수정 (main.py 참고)
    url = EXCLUDED.url,
    summary_raw = EXCLUDED.summary_raw,
    body_html = EXCLUDED.body_html,
    body_text = EXCLUDED.body_text,
    published_at = EXCLUDED.published_at,
    category_ai = EXCLUDED.category_ai,
    start_at_ai = EXCLUDED.start_at_ai,
    end_at_ai = EXCLUDED.end_at_ai,
    qualification_ai = EXCLUDED.qualification_ai,
    hashtags_ai = EXCLUDED.hashtags_ai,
    updated_at = CURRENT_TIMESTAMP,
    search_vector = setweight(to_tsvector('simple', coalesce(EXCLUDED.title, '')), 'A') ||
                    setweight(to_tsvector('simple', coalesce(array_to_string(EXCLUDED.hashtags_ai, ' '), '')), 'B') ||
                    setweight(to_tsvector('simple', coalesce(EXCLUDED.body_text, '')), 'C') -- search_vector 업데이트 로직 추가
"""

# --- 기존 헬퍼 함수들 (clean_text, extract_text_from_html 등) ---
# (이전 코드와 동일하므로 생략, 필요시 이전 코드 블록에서 복사)
def clean_text(text: Optional[str], max_length: Optional[int] = None) -> str:
    """텍스트 정리 및 정규화"""
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    if max_length and len(text) > max_length:
        text = text[:max_length-3] + "..."
    return text

def extract_text_from_html(html: Optional[str]) -> str:
    """HTML에서 텍스트 추출 (개선된 버전 유지)"""
    if not html:
        return ""
    try:
        content_pattern = r'게시글 내용(.*?)목록'
        content_match = re.search(content_pattern, html, re.DOTALL)
        if content_match:
            extracted_text = content_match.group(1).strip()
            soup = BeautifulSoup(extracted_text, 'html.parser')
            for tag in soup(['script', 'style', 'meta', 'link']):
                tag.decompose()
            text = soup.get_text(separator=' ', strip=True)
            return clean_text(text)
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'meta', 'link']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        return clean_text(text)
    except Exception as e:
        print(f"  ⚠️ HTML parsing error: {e}")
        return ""

def normalize_url(url: Optional[str], base_url: Optional[str] = None) -> str:
    """URL 정규화 및 절대 경로 변환"""
    if not url: return ""
    url = url.strip()
    if base_url and not url.startswith(('http://', 'https://', '//')):
        url = urljoin(base_url, url)
    if url.startswith('//'): url = 'https:' + url
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc: return ""
    return url

def parse_dt(v: Any) -> Optional[datetime]:
    """다양한 형식의 날짜/시간 파싱 (개선된 버전 유지)"""
    if not v: return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        try:
            ts = v / 1000 if v > 10_000_000_000 else v
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError): return None
    if isinstance(v, str):
        v = v.strip()
        if not v: return None
        v = v.replace("Z", "+00:00")
        try:
            dt_obj = datetime.fromisoformat(v)
            return dt_obj if dt_obj.tzinfo else dt_obj.replace(tzinfo=timezone.utc)
        except ValueError: pass
        date_formats = [
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
            "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d",
            "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y",
            "%Y년 %m월 %d일 %H:%M", "%Y년 %m월 %d일",
        ]
        for fmt in date_formats:
            try:
                dt_obj = datetime.strptime(v, fmt)
                return dt_obj.replace(tzinfo=timezone.utc)
            except ValueError: continue
    return None

def extract_field(item: Dict[str, Any], field_names: List[str], default: Any = "") -> Optional[Any]:
    """여러 가능한 필드명에서 값 추출"""
    for field in field_names:
        if '.' in field:
            parts = field.split('.')
            value = item
            for part in parts:
                if isinstance(value, dict): value = value.get(part)
                else: value = None; break
            if value is not None: return value # 값 자체가 None일 수 있으므로 None과 비교
        else:
            value = item.get(field)
            if value is not None: return value
    return default

def normalize_item(item: dict, base_url: Optional[str] = None) -> dict:
    """아이템 정규화 (개선된 버전 유지)"""
    title = clean_text(extract_field(item, ["title", "name", "subject", "headline", "meta.title", "og:title", "titleText"]), max_length=500)
    url = normalize_url(extract_field(item, ["url", "link", "href", "permalink", "canonical", "meta.url", "og:url"]), base_url)
    summary_raw = clean_text(extract_field(item, ["summary", "description", "excerpt", "preview", "meta.description", "og:description", "abstract"]), max_length=1000)
    body_html = extract_field(item, ["html", "content_html", "body_html", "htmlContent"])
    body_text = extract_field(item, ["text", "content", "body", "body_text", "plainText"])
    if body_html and not body_text: body_text = extract_text_from_html(body_html)
    if body_text and not summary_raw: summary_raw = clean_text(body_text[:500])
    published_at = None
    date_fields = ["publishedAt", "published_at", "pubDate", "date", "datetime", "time", "createdAt", "created_at", "timestamp", "postDate", "releaseDate"]
    for field in date_fields:
        value = item.get(field)
        if value:
            published_at = parse_dt(value)
            if published_at: break
    result = {
        "title": title, "url": url, "summary_raw": summary_raw,
        "body_html": body_html, "body_text": body_text, "published_at": published_at,
    }
    category = extract_field(item, ["category", "categories", "tag", "tags", "section"])
    author = extract_field(item, ["author", "writer", "creator", "by"])
    if category: result["category"] = clean_text(str(category))
    if author: result["author"] = clean_text(str(author))
    return result

def validate_normalized_item(item: dict) -> bool:
    """정규화된 아이템의 유효성 검증"""
    if not item.get("title") or not item.get("url"): return False
    if not item["url"].startswith(('http://', 'https://')): return False
    if len(item["title"]) < 3: return False
    if item.get("published_at") and item["published_at"] > datetime.now(timezone.utc): return False
    return True

def content_hash(college_key: str, title: str, url: str, published_at: Optional[datetime]) -> str:
    """컨텐츠 해시 생성 (개선된 버전 유지)"""
    url = url.rstrip('/')
    date_str = published_at.date().isoformat() if published_at else ""
    title_normalized = re.sub(r'\s+', ' ', title.lower().strip())
    base = f"{college_key}|{title_normalized}|{url}|{date_str}"
    return hashlib.sha256(base.encode('utf-8')).hexdigest()
# --- 기존 헬퍼 함수 종료 ---

# ==============================================================================
# Apify API 헬퍼 함수 (최신 실행 결과 가져오기)
# ==============================================================================
def get_latest_run_for_task(task_id: str, timeout=30):
    """GET /v2/actor-tasks/{taskId}/runs - 가장 최근 성공한 실행 1개만 가져오기"""
    url = f"https://api.apify.com/v2/actor-tasks/{task_id}/runs"
    params = {"token": APIFY_TOKEN, "limit": 1, "desc": "true"}
    try:
        resp = SESSION.get(url, params=params, timeout=timeout)
        resp.raise_for_status() # HTTP 오류 발생 시 예외 발생
        data = resp.json()
        runs = data.get("data", {}).get("items", [])
    except requests.RequestException as e:
        print(f"  ❌ get runs error for task {task_id}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"  ⚠️ get runs: invalid JSON response for task {task_id}")
        return None

    if not runs:
        print(f"  ⚠️ No recent run found for task {task_id}")
        return None

    latest_run = runs[0]
    if latest_run.get("status") == "SUCCEEDED":
        return latest_run
    else:
        status = latest_run.get("status", "UNKNOWN")
        print(f"  ⚠️ Latest run for task {task_id} was not successful (status: {status})")
        return None

def fetch_dataset_items(dataset_id: str, timeout=300):
    """데이터셋 아이템 가져오기"""
    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    params = {"token": APIFY_TOKEN, "format": "json", "clean": "true"}
    try:
        resp = SESSION.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        # Apify 응답 형식이 리스트가 아닐 경우 처리
        if isinstance(data, dict) and 'items' in data:
            return data['items']
        elif isinstance(data, list):
             return data
        else:
             print(f"  ⚠️ Unexpected data format from dataset {dataset_id}: {type(data)}")
             return []
    except requests.RequestException as e:
        print(f"  ⚠️ items fetch error for dataset {dataset_id}: {e}")
    except json.JSONDecodeError:
        print(f"  ⚠️ items JSON decode error for dataset {dataset_id}")
    return []

# ==============================================================================
# 메인 실행 함수 (최신 데이터 가져오기 + 최신 AI 처리)
# ==============================================================================
def run():
    total_upserted = 0
    total_skipped = 0

    print(f"🤖 AI_IN_PIPELINE: {AI_IN_PIPELINE}")
    print(f"⏱️ AI_SLEEP_SEC: {AI_SLEEP_SEC}")
    print(f"🔢 AI_MAX_PER_COLLEGE: {AI_MAX_PER_COLLEGE}")

    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False # 명시적 커밋 사용

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for ck, meta in COLLEGES.items():
                name = meta["name"]
                task_id = meta["task_id"]
                site = meta.get("url")

                print(f"\n🔍 Fetching latest run for: {name} ({ck})")

                run_data = get_latest_run_for_task(task_id)
                if not run_data:
                    continue

                run_id = run_data.get("id")
                ds_id = run_data.get("defaultDatasetId")
                finished_at = run_data.get("finishedAt", "unknown")

                if not ds_id:
                    print(f"  ❌ no datasetId for run {run_id} ({ck})")
                    continue

                print(f"  📅 Using run {run_id} finished at {finished_at}")

                items = fetch_dataset_items(ds_id)
                if not items:
                     print(f"  ⚠️ No items found in dataset {ds_id}")
                     continue
                print(f"  📦 Items fetched: {len(items)}")

                college_upserted = 0
                college_skipped = 0
                ai_call_count = 0

                for rec in items:
                    norm = normalize_item(rec, base_url=site)

                    if not validate_normalized_item(norm):
                        college_skipped += 1
                        continue

                    # ============================================
                    # AI 처리 로직 수정 (최신 ai_processor 사용)
                    # ============================================
                    if AI_IN_PIPELINE and ai_call_count < AI_MAX_PER_COLLEGE:
                        time.sleep(AI_SLEEP_SEC) # API 호출 전 지연
                        try:
                            title_for_ai = norm.get("title", "").strip()
                            body_for_ai = norm.get("body_text", "").strip()

                            # 1단계: 카테고리 분류
                            category_ai = classify_notice_category(title=title_for_ai, body=body_for_ai)
                            norm["category_ai"] = category_ai

                            # 2단계: 구조화된 정보 추출 (분류된 카테고리 사용)
                            structured_info = extract_structured_info(title=title_for_ai, body=body_for_ai, category=category_ai)

                            # --- structured_info에서 필드 추출 (main.py 로직 참고) ---
                            # 현재 start_at/end_at은 structured_info에서 직접 파싱하지 않음
                            start_at_ai = None # 필요 시 calendar_utils 등 활용 로직 추가
                            end_at_ai = None   # 필요 시 calendar_utils 등 활용 로직 추가

                            # qualification_ai는 JSON 객체 또는 빈 딕셔너리
                            # structured_info 자체가 qualification일 수 있으므로 확인
                            qualification_ai = structured_info.get("qualifications",
                                                                 structured_info if isinstance(structured_info, dict) and "error" not in structured_info else {})

                            # hashtags_ai는 category_ai 기반 리스트 (main.py와 동일하게, #일반 제외)
                            hashtags_ai = [category_ai] if category_ai and category_ai != "#일반" else None

                            norm["start_at_ai"] = start_at_ai
                            norm["end_at_ai"] = end_at_ai
                            norm["qualification_ai"] = qualification_ai
                            norm["hashtags_ai"] = hashtags_ai

                            ai_call_count += 1

                        except Exception as e:
                            # 429 감지 시 추가 슬립
                            if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 429:
                                print(f"  ⚠️ 429 Rate limit detected, sleeping 5 seconds...")
                                time.sleep(5.0)
                            elif "429" in str(e): # Gemini API의 경우 다른 형태일 수 있음
                                print(f"  ⚠️ 429 Rate limit detected (non-request), sleeping 5 seconds...")
                                time.sleep(5.0)

                            print(f"  ⚠️ AI extraction failed for '{norm.get('title', 'N/A')[:50]}...': {e}. Proceeding without AI data.")
                            # 실패 시 AI 필드는 None/기본값으로 설정
                            norm["category_ai"] = None
                            norm["start_at_ai"] = None
                            norm["end_at_ai"] = None
                            norm["qualification_ai"] = {}
                            norm["hashtags_ai"] = None
                    else:
                        # AI 비활성화 또는 배치 제한 초과 시
                        norm["category_ai"] = None
                        norm["start_at_ai"] = None
                        norm["end_at_ai"] = None
                        norm["qualification_ai"] = {}
                        norm["hashtags_ai"] = None

                    h = content_hash(ck, norm["title"], norm["url"], norm.get("published_at"))

                    # DB 저장 (AI 필드 및 search_vector 포함)
                    try:
                        cur.execute(UPSERT_SQL, {
                            "college_key": ck,
                            "title": norm["title"],
                            "url": norm["url"],
                            "summary_raw": norm.get("summary_raw"),
                            "body_html": norm.get("body_html"),
                            "body_text": norm.get("body_text"),
                            "published_at": norm.get("published_at"),
                            "source_site": site,
                            "content_hash": h,
                            "category_ai": norm.get("category_ai"),
                            "start_at_ai": norm.get("start_at_ai"),
                            "end_at_ai": norm.get("end_at_ai"),
                            # qualification_ai는 Json()으로 감싸서 전달
                            "qualification_ai": Json(norm.get("qualification_ai") or {}),
                            "hashtags_ai": norm.get("hashtags_ai"),
                        })
                        if cur.rowcount > 0:
                            college_upserted += 1
                        # else: # 이미 존재하여 업데이트되지 않은 경우 (rowcount가 0일 수 있음)
                        #     pass

                    except psycopg2.Error as db_err:
                        conn.rollback() # 현재 아이템 롤백
                        print(f"  ❌ DB error upserting '{norm.get('title', 'N/A')[:50]}...': {db_err}")
                        college_skipped += 1
                        # 다음 아이템 처리를 위해 autocommit 상태 복원 (필요 시)
                        # conn.autocommit = False # 루프 시작 시 설정했으므로 불필요할 수 있음
                    except Exception as general_err:
                         conn.rollback() # 예기치 않은 오류 시 롤백
                         print(f"  ❌ Unexpected error during upsert for '{norm.get('title', 'N/A')[:50]}...': {general_err}")
                         college_skipped += 1

                # 한 대학 처리 후 커밋
                conn.commit()
                print(f"  ✅ {name}: upserted={college_upserted}, skipped={college_skipped}, ai_calls={ai_call_count}")

                total_upserted += college_upserted
                total_skipped += college_skipped

    except psycopg2.Error as db_conn_err:
        print(f"\n❌ Database connection error: {db_conn_err}")
    except KeyboardInterrupt:
        print("\n🚫 Operation cancelled by user.")
        if conn: conn.rollback() # 중단 시 롤백 시도
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")
        if conn: conn.rollback() # 예외 발생 시 롤백 시도
    finally:
        if conn:
            conn.close() # 커넥션 반환

    print(f"\n✨ Total: upserted={total_upserted}, skipped={total_skipped}")

if __name__ == "__main__":
    run()