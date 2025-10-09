# crawler_apify.py (AI 필드 처리 통합 버전)
import os, time, json, hashlib, requests, psycopg2
import re
import datetime as dt
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, urljoin
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv
from typing import Optional, Dict, Any, List
from html import unescape
from bs4 import BeautifulSoup
from colleges import COLLEGES

# AI processor import 추가
from ai_processor import (
    extract_hashtags_from_title,
    extract_notice_info,
)

load_dotenv(encoding="utf-8")

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
AI_IN_PIPELINE = os.getenv("AI_IN_PIPELINE", "true").lower() == "true"

if not APIFY_TOKEN:
    raise RuntimeError("APIFY_TOKEN not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

# AI 필드 포함된 UPSERT SQL (main.py와 동일)
UPSERT_SQL = """
INSERT INTO notices (
    college_key, title, url, summary_raw, body_html, body_text, 
    published_at, source_site, content_hash,
    category_ai, start_at_ai, end_at_ai, qualification_ai, hashtags_ai
) VALUES (
    %(college_key)s, %(title)s, %(url)s, %(summary_raw)s, 
    %(body_html)s, %(body_text)s, %(published_at)s, 
    %(source_site)s, %(content_hash)s,
    %(category_ai)s, %(start_at_ai)s, %(end_at_ai)s, %(qualification_ai)s, %(hashtags_ai)s
)
ON CONFLICT (content_hash) 
DO UPDATE SET
    summary_raw = EXCLUDED.summary_raw,
    body_html = EXCLUDED.body_html,
    body_text = EXCLUDED.body_text,
    category_ai = EXCLUDED.category_ai,
    start_at_ai = EXCLUDED.start_at_ai,
    end_at_ai = EXCLUDED.end_at_ai,
    qualification_ai = EXCLUDED.qualification_ai,
    hashtags_ai = EXCLUDED.hashtags_ai,
    updated_at = CURRENT_TIMESTAMP
"""

# AI 헬퍼 함수 추가 (main.py와 동일)
def _to_utc_ts(date_yyyy_mm_dd: str | None):
    """'YYYY-MM-DD' -> aware UTC midnight; None 유지 (방어적 파싱)"""
    if not date_yyyy_mm_dd:
        return None
    try:
        d = dt.date.fromisoformat(date_yyyy_mm_dd)
        return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    except Exception:
        return None

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
    """HTML에서 텍스트 추출"""
    if not html:
        return ""
    
    try:
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
    if not url:
        return ""
    
    url = url.strip()
    
    if base_url and not url.startswith(('http://', 'https://', '//')):
        url = urljoin(base_url, url)
    
    if url.startswith('//'):
        url = 'https:' + url
    
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    
    return url

def parse_dt(v: Any) -> Optional[datetime]:
    """다양한 형식의 날짜/시간 파싱"""
    if not v:
        return None
    
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    
    if isinstance(v, (int, float)):
        try:
            ts = v / 1000 if v > 10_000_000_000 else v
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        
        v = v.replace("Z", "+00:00")
        try:
            dt_obj = datetime.fromisoformat(v)
            return dt_obj if dt_obj.tzinfo else dt_obj.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        
        date_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%Y년 %m월 %d일 %H:%M",
            "%Y년 %m월 %d일",
        ]
        
        for fmt in date_formats:
            try:
                dt_obj = datetime.strptime(v, fmt)
                return dt_obj.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    
    return None

def extract_field(item: Dict[str, Any], field_names: List[str], 
                  default: str = "") -> Optional[str]:
    """여러 가능한 필드명에서 값 추출"""
    for field in field_names:
        if '.' in field:
            parts = field.split('.')
            value = item
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                    break
            if value:
                return value
        else:
            value = item.get(field)
            if value:
                return value
    return default

def normalize_item(item: dict, base_url: Optional[str] = None) -> dict:
    """아이템 정규화"""
    
    title_fields = [
        "title", "name", "subject", "headline", 
        "meta.title", "og:title", "titleText"
    ]
    title = clean_text(extract_field(item, title_fields), max_length=500)
    
    url_fields = [
        "url", "link", "href", "permalink", 
        "canonical", "meta.url", "og:url"
    ]
    url = normalize_url(extract_field(item, url_fields), base_url)
    
    summary_fields = [
        "summary", "description", "excerpt", "preview",
        "meta.description", "og:description", "abstract"
    ]
    summary_raw = extract_field(item, summary_fields)
    if summary_raw:
        summary_raw = clean_text(summary_raw, max_length=1000)
    
    html_fields = ["html", "content_html", "body_html", "htmlContent"]
    body_html = extract_field(item, html_fields)
    
    text_fields = ["text", "content", "body", "body_text", "plainText"]
    body_text = extract_field(item, text_fields)
    
    if body_html and not body_text:
        body_text = extract_text_from_html(body_html)
    
    if body_text and not summary_raw:
        summary_raw = clean_text(body_text[:500])
    
    date_fields = [
        "publishedAt", "published_at", "pubDate", "date", 
        "datetime", "time", "createdAt", "created_at",
        "timestamp", "postDate", "releaseDate"
    ]
    published_at = None
    for field in date_fields:
        value = item.get(field)
        if value:
            published_at = parse_dt(value)
            if published_at:
                break
    
    category_fields = ["category", "categories", "tag", "tags", "section"]
    category = extract_field(item, category_fields)
    
    author_fields = ["author", "writer", "creator", "by"]
    author = extract_field(item, author_fields)
    
    result = {
        "title": title,
        "url": url,
        "summary_raw": summary_raw,
        "body_html": body_html,
        "body_text": body_text,
        "published_at": published_at,
    }
    
    if category:
        result["category"] = clean_text(str(category))
    if author:
        result["author"] = clean_text(str(author))
    
    return result

def validate_normalized_item(item: dict) -> bool:
    """정규화된 아이템의 유효성 검증"""
    if not item.get("title") or not item.get("url"):
        return False
    
    if not item["url"].startswith(('http://', 'https://')):
        return False
    
    if len(item["title"]) < 3:
        return False
    
    if item.get("published_at"):
        if item["published_at"] > datetime.now(timezone.utc):
            return False
    
    return True

def content_hash(college_key: str, title: str, url: str, 
                published_at: Optional[datetime]) -> str:
    """컨텐츠 해시 생성"""
    url = url.rstrip('/')
    
    date_str = ""
    if published_at:
        date_str = published_at.date().isoformat()
    
    title_normalized = re.sub(r'\s+', ' ', title.lower().strip())
    
    base = f"{college_key}|{title_normalized}|{url}|{date_str}"
    return hashlib.sha256(base.encode('utf-8')).hexdigest()

# Apify API 헬퍼 함수들
def start_task_run(task_id: str, timeout=30):
    """POST /v2/actor-tasks/{taskId}/runs"""
    url = f"https://api.apify.com/v2/actor-tasks/{task_id}/runs"
    params = {"token": APIFY_TOKEN}
    try:
        resp = SESSION.post(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        print(f"  ❌ start run error: {e}")
        return None
    if resp.status_code not in (201, 200):
        print(f"  ❌ start run HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    try:
        data = resp.json()
    except ValueError:
        print("  ⚠️ start run: empty body")
        return None
    run = data.get("data") or data
    return run.get("id")

def poll_run_until_done(run_id: str, max_wait_sec=600, poll_interval=3):
    """GET /v2/actor-runs/{runId} 상태 폴링"""
    url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    params = {"token": APIFY_TOKEN}
    waited = 0
    while waited <= max_wait_sec:
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                print(f"  ⚠️ poll HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                data = resp.json().get("data") or {}
                status = data.get("status")
                if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
                    return data
        except requests.RequestException as e:
            print(f"  ⚠️ poll error: {e}")
        time.sleep(poll_interval)
        waited += poll_interval
    print("  ⚠️ poll timeout")
    return None

def fetch_dataset_items(dataset_id: str, timeout=300):
    """데이터셋 아이템 가져오기"""
    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    params = {"token": APIFY_TOKEN, "format": "json", "clean": "true"}
    try:
        resp = SESSION.get(url, params=params, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else data.get("items", [])
        print(f"  ⚠️ items HTTP {resp.status_code}: {resp.text[:300]}")
    except requests.RequestException as e:
        print(f"  ⚠️ items error: {e}")
    return []

# 메인 실행 함수 (AI 통합)
def run():
    total_upserted = 0
    total_skipped = 0
    
    print(f"🤖 AI_IN_PIPELINE: {AI_IN_PIPELINE}")
    
    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        for ck, meta in COLLEGES.items():
            name = meta["name"]
            task_id = meta["task_id"]
            site = meta.get("url")
            
            print(f"🔍 Start task: {name} ({ck})")
            
            # 태스크 실행
            run_id = start_task_run(task_id)
            if not run_id:
                print(f"  ❌ cannot start run for {ck}")
                continue

            # 완료 대기
            run_data = poll_run_until_done(run_id)
            if not run_data:
                print(f"  ❌ run polling failed for {ck}")
                continue
                
            status = run_data.get("status")
            ds_id = run_data.get("defaultDatasetId")
            
            if status != "SUCCEEDED":
                print(f"  ❌ run status={status} for {ck}")
                continue
            if not ds_id:
                print(f"  ❌ no datasetId for {ck}")
                continue

            # 데이터 가져오기
            items = fetch_dataset_items(ds_id)
            print(f"  📦 items fetched: {len(items)}")
            
            college_upserted = 0
            college_skipped = 0

            for rec in items:
                # 정규화
                norm = normalize_item(rec, base_url=site)
                
                # 유효성 검증
                if not validate_normalized_item(norm):
                    college_skipped += 1
                    continue
                
                # ============================================
                # AI 자동추출 로직 통합 (main.py와 동일)
                # ============================================
                use_ai = AI_IN_PIPELINE
                if use_ai:
                    try:
                        title_for_ai = (norm.get("title") or "").strip()
                        body_for_ai = (norm.get("body_text") or "").strip()

                        # 1) 본문/제목 기반 구조화 추출
                        ai = extract_notice_info(body_text=body_for_ai, title=title_for_ai) or {}

                        # 2) 제목 해시태그 추출
                        ht = extract_hashtags_from_title(title_for_ai) or {}

                        # 3) 필드 매핑
                        norm["category_ai"] = ai.get("category_ai")
                        norm["start_at_ai"] = _to_utc_ts(ai.get("start_date_ai"))
                        norm["end_at_ai"] = _to_utc_ts(ai.get("end_date_ai"))

                        # qualification_ai: dict or {}
                        qual_dict = ai.get("qualification_ai") or {}
                        norm["qualification_ai"] = qual_dict

                        # hashtags_ai: list or None
                        norm["hashtags_ai"] = ht.get("hashtags") or None

                    except Exception as e:
                        # 실패 시에도 저장 (기존 파이프라인을 막지 않음)
                        print(f"  ⚠️ AI extraction failed for {norm.get('title', 'unknown')[:50]}: {e}")
                        norm["category_ai"] = None
                        norm["start_at_ai"] = None
                        norm["end_at_ai"] = None
                        norm["qualification_ai"] = {}
                        norm["hashtags_ai"] = None
                else:
                    # AI 비활성화 시 전부 None/빈값
                    norm["category_ai"] = None
                    norm["start_at_ai"] = None
                    norm["end_at_ai"] = None
                    norm["qualification_ai"] = {}
                    norm["hashtags_ai"] = None
                
                # 해시 생성
                h = content_hash(ck, norm["title"], norm["url"], norm["published_at"])
                
                # DB 저장 (AI 필드 포함)
                try:
                    cur.execute(UPSERT_SQL, {
                        "college_key": ck,
                        "title": norm["title"],
                        "url": norm["url"],
                        "summary_raw": norm["summary_raw"],
                        "body_html": norm["body_html"],
                        "body_text": norm["body_text"],
                        "published_at": norm["published_at"],
                        "source_site": site,
                        "content_hash": h,
                        "category_ai": norm.get("category_ai"),
                        "start_at_ai": norm.get("start_at_ai"),
                        "end_at_ai": norm.get("end_at_ai"),
                        "qualification_ai": Json(norm.get("qualification_ai") or {}),
                        "hashtags_ai": norm.get("hashtags_ai"),
                    })
                    college_upserted += 1
                except psycopg2.Error as e:
                    print(f"  ⚠️ DB error for {norm['title'][:50]}: {e}")
                    college_skipped += 1
            
            conn.commit()
            print(f"  ✅ {name}: upserted={college_upserted}, skipped={college_skipped}")
            
            total_upserted += college_upserted
            total_skipped += college_skipped
    
    print(f"\n✨ Total: upserted={total_upserted}, skipped={total_skipped}")

if __name__ == "__main__":
    run()