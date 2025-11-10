#!/usr/bin/env python3
"""
link_health_check.py
Check URL health status for notices and update database
"""

import os
import sys
import time
import argparse
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict, Any

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL")
LINK_CHECK_TIMEOUT = float(os.getenv("LINK_CHECK_TIMEOUT", "6.0"))
LINK_CHECK_BATCH = int(os.getenv("LINK_CHECK_BATCH", "300"))

# Request headers to mimic browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def pick_targets(conn, stale_hours: int = 24, limit: int = 300) -> List[Dict[str, Any]]:
    """
    Select notices that need URL health check
    
    Args:
        conn: Database connection
        stale_hours: Hours before re-check
        limit: Maximum records to process
    
    Returns:
        List of notice records with id, url, title
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
    
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, url, title
            FROM notices
            WHERE url IS NOT NULL
              AND url != ''
              AND (url_checked_at IS NULL OR url_checked_at < %s)
            ORDER BY url_checked_at ASC NULLS FIRST
            LIMIT %s
        """, (cutoff_time, limit))
        
        return cur.fetchall()


def check_one(session: requests.Session, url: str, timeout: float = 6.0) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Check single URL health status using session for connection pooling
    
    Args:
        session: Requests session for connection reuse
        url: URL to check
        timeout: Request timeout in seconds
    
    Returns:
        Tuple of (url_ok, status_code, final_url)
    """
    try:
        # Try HEAD request first (faster)
        response = session.head(
            url, 
            headers=HEADERS, 
            timeout=timeout, 
            allow_redirects=True
        )
        
        # Fallback to GET for certain status codes or empty response
        if response.status_code in (403, 405) or not response.content:
            response = session.get(
                url, 
                headers=HEADERS, 
                timeout=timeout, 
                allow_redirects=True
            )
        
        status_code = response.status_code
        final_url = response.url
        url_ok = 200 <= status_code < 400
        
        return (url_ok, status_code, final_url)
        
    except requests.RequestException as e:
        # Connection error, timeout, etc.
        return (False, None, None)
    except Exception as e:
        # Unexpected error
        return (False, None, None)


def update_one(conn, notice_id: str, url_ok: bool, 
               status_code: Optional[int], final_url: Optional[str]) -> bool:
    """
    Update single notice with URL check results
    
    Args:
        conn: Database connection
        notice_id: Notice ID to update
        url_ok: Whether URL is healthy
        status_code: HTTP status code
        final_url: Final URL after redirects
    
    Returns:
        True if update successful, False otherwise
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE notices
                SET url_ok = %s,
                    url_status_code = %s,
                    url_final = %s,
                    url_checked_at = NOW()
                WHERE id = %s
            """, (url_ok, status_code, final_url, notice_id))
            
            conn.commit()
            return True
            
    except psycopg2.Error as e:
        conn.rollback()
        print(f"DB error updating {notice_id}: {e}")
        return False


def main():
    """
    Main execution function with optimized resource management
    """
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Check URL health for notices")
    parser.add_argument(
        "--stale-hours", 
        type=int, 
        default=24,
        help="Hours before re-checking URL (default: 24)"
    )
    parser.add_argument(
        "--limit", 
        type=int, 
        default=LINK_CHECK_BATCH,
        help=f"Maximum URLs to check (default: {LINK_CHECK_BATCH})"
    )
    args = parser.parse_args()
    
    if not DATABASE_URL:
        print("❌ DATABASE_URL not set in environment")
        sys.exit(1)
    
    # Use context manager for proper connection lifecycle
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            # Get URLs to check
            targets = pick_targets(conn, args.stale_hours, args.limit)
            print(f"🔎 Targets: {len(targets)}")
            
            if not targets:
                print("No URLs to check")
                return
            
            # Statistics
            ok_count = 0
            bad_count = 0
            
            # Create session for connection reuse (performance optimization)
            with requests.Session() as session:
                # Process each URL
                for notice in targets:
                    notice_id = notice["id"]
                    url = notice["url"]
                    title = notice["title"][:50] + "..." if len(notice["title"]) > 50 else notice["title"]
                    
                    # Check URL with session
                    url_ok, status_code, final_url = check_one(session, url, LINK_CHECK_TIMEOUT)
                    
                    # Update database
                    if update_one(conn, notice_id, url_ok, status_code, final_url):
                        if url_ok:
                            ok_count += 1
                            print(f"✅ {status_code or 'OK'} {url}")
                        else:
                            bad_count += 1
                            print(f"❌ {status_code or 'ERR'} {url}")
                    else:
                        bad_count += 1
                        print(f"⚠️  Failed to update: {url}")
                    
                    # Small delay to avoid hammering servers
                    time.sleep(0.1)
            
            print(f"\nDone. OK={ok_count}, BAD={bad_count}")
            
    except psycopg2.Error as e:
        print(f"❌ Database connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    
    main()