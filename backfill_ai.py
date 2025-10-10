"""
Summary backfill script for existing notices
Usage:
  python backfill_summary.py                     # Default: backfill summaries
  python backfill_summary.py --task=summary      # Explicit summary backfill
  python backfill_summary.py --limit=100         # Process only 100 records
  python backfill_summary.py --dry-run           # Preview without updating
  python backfill_summary.py --college=main      # Filter by college
  python backfill_summary.py --since=2024-01-01  # Filter by date
"""

import os
import sys
import time
import json
import argparse
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

from typing import Optional, Dict, Any
import logging

# Import AI processor
from ai_processor import generate_brief_summary, extract_notice_info, extract_hashtags_from_title


from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)

# Setup
load_dotenv(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill")

# Environment
DATABASE_URL = os.getenv("DATABASE_URL")
BATCH_SIZE = int(os.getenv("AI_BACKFILL_BATCH", "30"))
SLEEP_SEC = float(os.getenv("AI_SLEEP_SEC", "0.8"))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# Queries
QUERY_SUMMARY_MISSING = """
SELECT id, title, summary_raw, body_text, body_html
FROM notices
WHERE (summary_ai IS NULL OR summary_ai = '')
  {filters}
ORDER BY published_at DESC NULLS LAST, created_at DESC
LIMIT %s OFFSET %s;
"""

QUERY_AI_FIELDS_MISSING = """
SELECT id, title, body_text
FROM notices
WHERE category_ai IS NULL
   OR qualification_ai IS NULL
   OR hashtags_ai IS NULL
  {filters}
ORDER BY created_at DESC
LIMIT %s;
"""

UPDATE_SUMMARY = """
UPDATE notices
SET summary_ai = %s, updated_at = CURRENT_TIMESTAMP
WHERE id = %s;
"""

UPDATE_AI_FIELDS = """
UPDATE notices
SET category_ai = %s, 
    start_at_ai = %s, 
    end_at_ai = %s, 
    qualification_ai = %s,
    hashtags_ai = %s,
    updated_at = CURRENT_TIMESTAMP
WHERE id = %s;
"""


class BackfillStats:
    """Statistics tracker for backfill operations"""
    def __init__(self):
        self.total = 0
        self.success = 0
        self.fallback = 0
        self.failed = 0
        self.skipped = 0
        self.start_time = time.time()
        self.processing_times = []
    
    def add_success(self, is_fallback=False, processing_time=0):
        self.success += 1
        if is_fallback:
            self.fallback += 1
        if processing_time:
            self.processing_times.append(processing_time)
    
    def add_failure(self):
        self.failed += 1
    
    def add_skip(self):
        self.skipped += 1
    
    def get_summary(self) -> str:
        elapsed = time.time() - self.start_time
        avg_time = sum(self.processing_times) / len(self.processing_times) if self.processing_times else 0
        
        return f"""
✨ Backfill Complete
────────────────────
Total processed: {self.total}
Success: {self.success} (LLM: {self.success - self.fallback}, Fallback: {self.fallback})
Failed: {self.failed}
Skipped: {self.skipped}
Time elapsed: {elapsed:.1f}s
Avg processing time: {avg_time:.2f}s per item
Success rate: {(self.success/self.total*100 if self.total else 0):.1f}%
"""


def build_filters(args) -> tuple[str, list]:
    """Build SQL filter clause from arguments"""
    filters = []
    params = []
    
    if args.college:
        filters.append("college_key = %s")
        params.append(args.college)
    
    if args.since:
        filters.append("published_at >= %s")
        params.append(args.since)
    
    filter_clause = " AND " + " AND ".join(filters) if filters else ""
    return filter_clause, params


def backfill_summaries(args):
    """Backfill missing summaries"""
    stats = BackfillStats()
    filter_clause, filter_params = build_filters(args)
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            offset = args.resume_from or 0
            
            while True:
                # Fetch batch
                query = QUERY_SUMMARY_MISSING.format(filters=filter_clause)
                cur.execute(query, filter_params + [args.limit or BATCH_SIZE, offset])
                rows = cur.fetchall()
                
                if not rows:
                    logger.info("No more rows to process")
                    break
                
                logger.info(f"Processing batch of {len(rows)} items (offset: {offset})")
                stats.total += len(rows)
                
                for row in rows:
                    row_start = time.time()
                    
                    try:
                        # Skip if already has summary (race condition check)
                        if row.get("summary_ai") and len(row["summary_ai"].strip()) > 10:
                            logger.debug(f"Skipping {row['id']}: already has summary")
                            stats.add_skip()
                            continue
                        
                        # Prepare input
                        title = row.get("title", "").strip()
                        # Priority: summary_raw > body_text > body_html (stripped)
                        text = row.get("summary_raw") or row.get("body_text") or ""
                        if not text and row.get("body_html"):
                            # Simple HTML stripping
                            import re
                            text = re.sub(r'<[^>]+>', ' ', row["body_html"])
                            text = re.sub(r'\s+', ' ', text).strip()
                        
                        if args.dry_run:
                            logger.info(f"[DRY RUN] Would generate summary for: {title[:50]}")
                            stats.add_success(processing_time=time.time() - row_start)
                            continue
                        
                        # Generate summary
                        time.sleep(SLEEP_SEC)  # Rate limiting
                        summary = generate_brief_summary(title, text)
                        
                        if not summary or len(summary) < 5:
                            logger.warning(f"Empty summary generated for {row['id']}")
                            stats.add_failure()
                            continue
                        
                        # Check if fallback was used (heuristic: very similar to title)
                        is_fallback = summary[:50].lower() == title[:50].lower()
                        
                        # Update database
                        cur.execute(UPDATE_SUMMARY, (summary, row['id']))
                        conn.commit()
                        
                        processing_time = time.time() - row_start
                        stats.add_success(is_fallback=is_fallback, processing_time=processing_time)
                        
                        if is_fallback:
                            logger.info(f"✓ {row['id']}: Summary generated (fallback) - {len(summary)} chars")
                        else:
                            logger.info(f"✓ {row['id']}: Summary generated - {len(summary)} chars")
                    
                    except Exception as e:
                        logger.error(f"✗ {row['id']}: Failed - {e}")
                        stats.add_failure()
                        if not args.continue_on_error:
                            raise
                
                # Check limit
                if args.limit and stats.total >= args.limit:
                    logger.info(f"Reached limit of {args.limit} items")
                    break
                
                offset += len(rows)
    
    print(stats.get_summary())


def backfill_ai_fields(args):
    """Backfill all AI fields (category, dates, qualification, hashtags)"""
    stats = BackfillStats()
    filter_clause, filter_params = build_filters(args)
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = QUERY_AI_FIELDS_MISSING.format(filters=filter_clause)
            cur.execute(query, filter_params + [args.limit or BATCH_SIZE])
            rows = cur.fetchall()
            
            if not rows:
                logger.info("No rows to backfill")
                return
            
            logger.info(f"Processing {len(rows)} items for AI fields backfill")
            stats.total = len(rows)
            
            for row in rows:
                row_start = time.time()
                
                try:
                    if args.dry_run:
                        logger.info(f"[DRY RUN] Would process AI fields for: {row['title'][:50]}")
                        stats.add_success(processing_time=time.time() - row_start)
                        continue
                    
                    time.sleep(SLEEP_SEC)
                    
                    # Extract all AI fields
                    ai_info = extract_notice_info(
                        body_text=row.get("body_text", ""),
                        title=row.get("title", "")
                    )
                    
                    hashtags = extract_hashtags_from_title(row.get("title", ""))
                    
                    cur.execute(UPDATE_AI_FIELDS, (
                        ai_info.get("category_ai"),
                        ai_info.get("start_date_ai"),
                        ai_info.get("end_date_ai"),
                        json.dumps(ai_info.get("qualification_ai", {}), ensure_ascii=False),
                        hashtags.get("hashtags"),
                        row['id']
                    ))
                    conn.commit()
                    
                    processing_time = time.time() - row_start
                    stats.add_success(processing_time=processing_time)
                    logger.info(f"✓ {row['id']}: AI fields updated")
                
                except Exception as e:
                    logger.error(f"✗ {row['id']}: Failed - {e}")
                    stats.add_failure()
                    if not args.continue_on_error:
                        raise
    
    print(stats.get_summary())


def main():
    parser = argparse.ArgumentParser(description="Backfill AI-generated fields for notices")
    parser.add_argument("--task", choices=["summary", "ai_fields", "all"], 
                       default="summary", help="Backfill task to run")
    parser.add_argument("--limit", type=int, help="Maximum number of records to process")
    parser.add_argument("--college", help="Filter by college key")
    parser.add_argument("--since", help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating")
    parser.add_argument("--resume-from", type=int, help="Resume from offset (for summary task)")
    parser.add_argument("--continue-on-error", action="store_true", 
                       help="Continue processing even if some items fail")
    
    args = parser.parse_args()
    
    logger.info(f"Starting backfill task: {args.task}")
    
    if args.dry_run:
        logger.warning("DRY RUN MODE - No database updates will be made")
    
    try:
        if args.task == "summary":
            backfill_summaries(args)
        elif args.task == "ai_fields":
            backfill_ai_fields(args)
        elif args.task == "all":
            logger.info("Running all backfill tasks...")
            backfill_ai_fields(args)
            backfill_summaries(args)
        
    except KeyboardInterrupt:
        logger.info("\nBackfill interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()