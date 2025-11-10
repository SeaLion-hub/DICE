# backfill_ai.py (수정본)
"""
AI fields backfill script for existing notices
Usage:
  python backfill_ai.py                     # Default: backfill AI fields
  python backfill_ai.py --limit=100         # Process only 100 records
  python backfill_ai.py --dry-run           # Preview without updating
  python backfill_ai.py --college=main      # Filter by college
  python backfill_ai.py --since=2024-01-01  # Filter by date
"""

import os
import sys
import time
import json
import argparse
import psycopg2
from psycopg2.extras import RealDictCursor, Json # Json 추가
from datetime import datetime, timedelta

from typing import Optional, Dict, Any
import logging

# Import AI processor (수정됨: extract_notice_info 대신 분류/추출 함수 임포트)
from ai_processor import classify_notice_category, extract_structured_info
from calendar_utils import extract_ai_time_window
# extract_hashtags_from_title 는 더 이상 사용하지 않음

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)

# Setup
load_dotenv(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill")

# Environment
DATABASE_URL = os.getenv("DATABASE_URL")
BATCH_SIZE = int(os.getenv("AI_BACKFILL_BATCH", "30"))
SLEEP_SEC = float(os.getenv("AI_SLEEP_SEC", "0.8")) # API 호출 간 지연 시간

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# Queries (수정됨: SQL 파라미터 순서 변경 없음, 값 할당 방식 변경)
QUERY_AI_FIELDS_MISSING = """
SELECT id, title, body_text, college_key -- college_key 추가 (로그용)
FROM notices
WHERE category_ai IS NULL -- category_ai가 NULL인 것만 대상으로 함 (핵심 지표)
  {filters}
ORDER BY created_at DESC
LIMIT %s;
"""

UPDATE_AI_FIELDS = """
UPDATE notices
SET category_ai = %(category_ai)s,
    start_at_ai = %(start_at_ai)s,
    end_at_ai = %(end_at_ai)s,
    qualification_ai = %(qualification_ai)s,
    hashtags_ai = %(hashtags_ai)s,
    updated_at = CURRENT_TIMESTAMP -- updated_at 추가
WHERE id = %(id)s;
"""


class BackfillStats:
    """Statistics tracker for backfill operations"""
    def __init__(self):
        self.total = 0
        self.success = 0
        # self.fallback = 0 # Fallback 로직 제거
        self.failed = 0
        self.skipped = 0 # 스킵 개념 추가 (예: body_text 없는 경우)
        self.start_time = time.time()
        self.processing_times = []

    def add_success(self, processing_time=0):
        self.success += 1
        if processing_time:
            self.processing_times.append(processing_time)

    def add_failure(self):
        self.failed += 1

    def add_skip(self):
        self.skipped += 1


def build_filters(args) -> tuple[str, list]:
    """Build SQL filter clause from arguments"""
    filters = []
    params = []

    if args.college:
        filters.append("college_key = %s")
        params.append(args.college)

    if args.since:
        # published_at 대신 created_at 사용 (published_at 이 null일 수 있음)
        filters.append("created_at >= %s")
        params.append(args.since)

    # 기본 필터 외에 category_ai IS NULL 필터는 QUERY 자체에 포함됨
    filter_clause = " AND " + " AND ".join(filters) if filters else ""
    return filter_clause, params


def backfill_ai_fields(args):
    """Backfill all AI fields using the new two-step AI process"""
    stats = BackfillStats()
    filter_clause, filter_params = build_filters(args)

    conn = None # finally 블록에서 사용하기 위해 외부 선언
    try:
        conn = psycopg2.connect(DATABASE_URL, client_encoding='utf8')
        conn.autocommit = False # 명시적 커밋/롤백 사용

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = QUERY_AI_FIELDS_MISSING.format(filters=filter_clause)
            cur.execute(query, filter_params + [args.limit or BATCH_SIZE])
            rows = cur.fetchall()

            if not rows:
                logger.info("No rows require AI backfill (category_ai is NULL)")
                return

            logger.info(f"Processing {len(rows)} items for AI fields backfill")
            stats.total = len(rows)

            for row in rows:
                row_start = time.time()
                notice_id = row['id']
                title = row.get("title", "")
                body = row.get("body_text", "")
                college = row.get("college_key", "N/A")

                # 본문 텍스트가 없으면 AI 처리 불가, 스킵
                if not body:
                    logger.warning(f"⚠️ {notice_id} ({college}): Skipped - No body_text found for '{title[:30]}...'")
                    stats.add_skip()
                    continue

                if args.dry_run:
                    logger.info(f"[DRY RUN] Would process AI fields for: {notice_id} ('{title[:30]}...')")
                    stats.add_success(processing_time=time.time() - row_start)
                    continue

                # API 호출 간 지연
                time.sleep(SLEEP_SEC)

                try:
                    # 1단계: 카테고리 분류
                    category_ai = classify_notice_category(title=title, body=body)

                    # 2단계: 구조화된 정보 추출
                    structured_info = extract_structured_info(title=title, body=body, category=category_ai)

                    if isinstance(structured_info, dict) and "error" not in structured_info:
                        start_at_ai, end_at_ai = extract_ai_time_window(structured_info, title)
                        qualification_ai = structured_info
                    else:
                        start_at_ai, end_at_ai = None, None
                        qualification_ai = {}
                    # hashtags_ai 는 category_ai 기반 리스트 (main.py 와 동일하게)
                    hashtags_ai = [category_ai] if category_ai and category_ai != "#일반" else None # #일반은 해시태그로 넣지 않음

                    # DB 업데이트 실행
                    cur.execute(UPDATE_AI_FIELDS, {
                        "category_ai": category_ai,
                        "start_at_ai": start_at_ai,
                        "end_at_ai": end_at_ai,
                        "qualification_ai": Json(qualification_ai), # Json() 사용
                        "hashtags_ai": hashtags_ai,
                        "id": notice_id
                    })

                    processing_time = time.time() - row_start
                    stats.add_success(processing_time=processing_time)
                    logger.info(f"✓ {notice_id} ({college}): AI fields updated (Category: {category_ai})")

                except Exception as e:
                    conn.rollback() # 현재 행 처리 중 에러 발생 시 롤백
                    logger.error(f"✗ {notice_id} ({college}): Failed processing '{title[:30]}...' - {e}")
                    stats.add_failure()
                    if not args.continue_on_error:
                        raise # 에러 발생 시 중단
                    else:
                        conn.autocommit = False # 다음 루프를 위해 autocommit 재설정 (혹시 몰라서)

            # 모든 행 처리 후 최종 커밋
            conn.commit()
            logger.info("Batch finished. Committing changes.")

    except psycopg2.Error as db_err:
        logger.error(f"Database connection or query error: {db_err}")
        if conn:
            conn.rollback() # DB 에러 시 롤백
        stats.add_failure() # 실패 카운트
    except KeyboardInterrupt:
        logger.info("\nBackfill interrupted by user. Rolling back current transaction.")
        if conn:
            conn.rollback() # 중단 시 롤백
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error during backfill: {e}")
        if conn:
            conn.rollback() # 예외 발생 시 롤백
        stats.add_failure() # 실패 카운트
        if not args.continue_on_error:
             raise # 에러 시 중단 (기본값)
    finally:
        if conn:
            conn.close() # 커넥션 반환

        # 요약 정보 로깅
        elapsed = time.time() - stats.start_time
        avg_time = sum(stats.processing_times) / len(stats.processing_times) if stats.processing_times else 0
        
        logger.info(f"""
✨ Backfill Complete
────────────────────
Total checked: {stats.total}
Success (Updated): {stats.success}
Failed (AI/DB Error): {stats.failed}
Skipped (No text): {stats.skipped}
Time elapsed: {elapsed:.1f}s
Avg processing time (Success only): {avg_time:.2f}s per item
Success rate (Updated / Checked): {(stats.success/stats.total*100 if stats.total else 0):.1f}%
""")


def main():
    parser = argparse.ArgumentParser(description="Backfill AI-generated fields for notices")
    parser.add_argument("--limit", type=int, help="Maximum number of records to process")
    parser.add_argument("--college", help="Filter by college key")
    parser.add_argument("--since", help="Filter by creation date (YYYY-MM-DD), checks notices created on or after this date")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating")
    parser.add_argument("--continue-on-error", action="store_true",
                       help="Continue processing next item even if one item fails")

    args = parser.parse_args()

    logger.info("Starting backfill task: ai_fields using 2-step AI (classify -> extract)")

    if args.dry_run:
        logger.warning("DRY RUN MODE - No database updates will be made")

    backfill_ai_fields(args)


if __name__ == "__main__":
    main()