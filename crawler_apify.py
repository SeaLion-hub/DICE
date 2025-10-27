# crawler_apify.py (개선된 normalize 로직)
import os, time, json, hashlib, requests, psycopg2
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, urljoin
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from typing import Optional, Dict, Any, List
from html import unescape
from bs4 import BeautifulSoup
from colleges import COLLEGES

load_dotenv(encoding="utf-8")

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not APIFY_TOKEN:
    raise RuntimeError("APIFY_TOKEN not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

UPSERT_SQL = """
INSERT INTO notices (
  college_key, title, url, summary_raw, body_html, body_text, published_at, source_site, content_hash
) VALUES (
  %(college_key)s, %(title)s, %(url)s, %(summary_raw)s, %(body_html)s, %(body_text)s, %(published_at)s, %(source_site)s, %(content_hash)s
)
ON CONFLICT (content_hash) DO UPDATE
SET
  title = EXCLUDED.title,
  url = EXCLUDED.url,
  summary_raw = EXCLUDED.summary_raw,
  body_html = EXCLUDED.body_html,
  body_text = EXCLUDED.body_text,
  published_at = COALESCE(EXCLUDED.published_at, notices.published_at),
  source_site = EXCLUDED.source_site,
  updated_at = CURRENT_TIMESTAMP;
"""

def clean_text(text: Optional[str], max_length: Optional[int] = None) -> str:
    """텍스트 정리 및 정규화"""
    if not text:
        return ""
    
    # HTML 엔티티 디코딩
    text = unescape(text)
    
    # 여러 공백을 하나로 통합
    text = re.sub(r'\s+', ' ', text)
    
    # 앞뒤 공백 제거
    text = text.strip()
    
    # 길이 제한 (필요시)
    if max_length and len(text) > max_length:
        text = text[:max_length-3] + "..."
    
    return text

def extract_text_from_html(html: Optional[str]) -> str:
    """HTML에서 텍스트 추출"""
    if not html:
        return ""
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # script, style 태그 제거
        for tag in soup(['script', 'style', 'meta', 'link']):
            tag.decompose()
        
        # 텍스트 추출
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
    
    # 상대 경로인 경우 base_url과 결합
    if base_url and not url.startswith(('http://', 'https://', '//')):
        url = urljoin(base_url, url)
    
    # // 로 시작하는 경우 https:// 추가
    if url.startswith('//'):
        url = 'https:' + url
    
    # URL 유효성 기본 검사
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    
    return url

def parse_dt(v: Any) -> Optional[datetime]:
    """다양한 형식의 날짜/시간 파싱 (개선된 버전)"""
    if not v:
        return None
    
    # datetime 객체인 경우
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    
    # 숫자 타임스탬프인 경우
    if isinstance(v, (int, float)):
        try:
            # 밀리초 타임스탬프 처리
            ts = v / 1000 if v > 10_000_000_000 else v
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    
    # 문자열인 경우
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        
        # ISO 형식 처리
        v = v.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        
        # 다양한 날짜 형식 시도
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
                dt = datetime.strptime(v, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    
    return None

def extract_field(item: Dict[str, Any], field_names: List[str], 
                  default: str = "") -> Optional[str]:
    """여러 가능한 필드명에서 값 추출"""
    for field in field_names:
        # 중첩된 필드 처리 (예: "meta.title")
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
    """아이템 정규화 (개선된 버전)"""
    
    # 제목 추출 (더 많은 필드 확인)
    title_fields = [
        "title", "name", "subject", "headline", 
        "meta.title", "og:title", "titleText"
    ]
    title = clean_text(extract_field(item, title_fields), max_length=500)
    
    # URL 추출 및 정규화
    url_fields = [
        "url", "link", "href", "permalink", 
        "canonical", "meta.url", "og:url"
    ]
    url = normalize_url(extract_field(item, url_fields), base_url)
    
    # 요약 추출
    summary_fields = [
        "summary", "description", "excerpt", "preview",
        "meta.description", "og:description", "abstract"
    ]
    summary_raw = extract_field(item, summary_fields)
    if summary_raw:
        summary_raw = clean_text(summary_raw, max_length=1000)
    
    # HTML 본문 처리
    html_fields = ["html", "content_html", "body_html", "htmlContent"]
    body_html = extract_field(item, html_fields)
    
    # 텍스트 본문 처리
    text_fields = ["text", "content", "body", "body_text", "plainText"]
    body_text = extract_field(item, text_fields)
    
    # HTML이 있지만 텍스트가 없는 경우, HTML에서 텍스트 추출
    if body_html and not body_text:
        body_text = extract_text_from_html(body_html)
    
    # 텍스트가 있지만 요약이 없는 경우, 텍스트에서 요약 생성
    if body_text and not summary_raw:
        summary_raw = clean_text(body_text[:500])
    
    # 날짜 추출 (더 많은 필드 확인)
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
    
    # 카테고리나 태그 정보 추출 (선택적)
    category_fields = ["category", "categories", "tag", "tags", "section"]
    category = extract_field(item, category_fields)
    
    # 작성자 정보 추출 (선택적)
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
    
    # 추가 메타데이터 (필요시 사용)
    if category:
        result["category"] = clean_text(str(category))
    if author:
        result["author"] = clean_text(str(author))
    
    return result

def validate_normalized_item(item: dict) -> bool:
    """정규화된 아이템의 유효성 검증"""
    # 필수 필드 확인
    if not item.get("title") or not item.get("url"):
        return False
    
    # URL 유효성 확인
    if not item["url"].startswith(('http://', 'https://')):
        return False
    
    # 제목 최소 길이 확인
    if len(item["title"]) < 3:
        return False
    
    # 날짜가 미래가 아닌지 확인
    if item.get("published_at"):
        if item["published_at"] > datetime.now(timezone.utc):
            return False
    
    return True

def content_hash(college_key: str, title: str, url: str, 
                published_at: Optional[datetime]) -> str:
    """컨텐츠 해시 생성 (개선된 버전)"""
    # URL 정규화 (trailing slash 제거 등)
    url = url.rstrip('/')
    
    # 날짜는 일 단위로만 사용 (시간 무시)
    date_str = ""
    if published_at:
        date_str = published_at.date().isoformat()
    
    # 제목 정규화 (대소문자 무시, 공백 정리)
    title_normalized = re.sub(r'\s+', ' ', title.lower().strip())
    
    base = f"{college_key}|{title_normalized}|{url}|{date_str}"
    return hashlib.sha256(base.encode('utf-8')).hexdigest()

# ---------------- Apify helpers (기존 코드 유지) ----------------

def start_task_run(task_id: str, timeout=30):
    """POST /v2/actor-tasks/{taskId}/runs"""
    url = f"https://api.apify.com/v2/actor-tasks/{task_id}/runs"
    params = {"token": APIFY_TOKEN}
    try:
        resp = SESSION.post(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        print(f"  ⌠start run error: {e}")
        return None
    if resp.status_code not in (201, 200):
        print(f"  ⌠start run HTTP {resp.status_code}: {resp.text[:300]}")
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

# ---------------- main flow (개선된 버전) ----------------

def run():
    total_upserted = 0
    total_skipped = 0
    
    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        for ck, meta in COLLEGES.items():
            name = meta["name"]
            task_id = meta["task_id"]
            site = meta.get("url")
            
            print(f"🔍 Start task: {name} ({ck})")
            
            # 태스크 실행
            run_id = start_task_run(task_id)
            if not run_id:
                print(f"  ⌠cannot start run for {ck}")
                continue

            # 완료 대기
            run_data = poll_run_until_done(run_id)
            if not run_data:
                print(f"  ⌠run polling failed for {ck}")
                continue
                
            status = run_data.get("status")
            ds_id = run_data.get("defaultDatasetId")
            
            if status != "SUCCEEDED":
                print(f"  ⌠run status={status} for {ck}")
                continue
            if not ds_id:
                print(f"  ⌠no datasetId for {ck}")
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
                
                # 해시 생성
                h = content_hash(ck, norm["title"], norm["url"], norm["published_at"])
                
                # DB 저장
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
                        "content_hash": h
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