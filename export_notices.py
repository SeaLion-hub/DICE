#!/usr/bin/env python3
"""
export_notices.py

notices 테이블의 'title', 'body_text', 'hashtags_ai' 컬럼을
CSV (notices_export.csv) 파일로 추출하는 스크립트.

필요한 라이브러리:
  pip install psycopg2-binary python-dotenv
"""

import os
import csv
import psycopg2
from dotenv import load_dotenv
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("export")

# .env 파일에서 환경 변수 로드 (UTF-8 인코딩 명시)
load_dotenv(encoding="utf-8")

# 환경 변수 로드
DATABASE_URL = os.getenv("DATABASE_URL")
OUTPUT_FILE = "notices_export.csv"

def export_data():
    """데이터베이스에서 공지사항을 조회하여 CSV 파일로 저장합니다."""
    
    if not DATABASE_URL:
        logger.error("오류: DATABASE_URL이 .env 파일에 설정되지 않았습니다.")
        return

    # SQL 쿼리: title, body_text, hashtags_ai 조회
    # COALESCE를 사용하여 NULL 값인 경우 빈 문자열이나 빈 배열로 처리
    SQL_QUERY = """
    SELECT 
        title, 
        COALESCE(body_text, ''), 
        COALESCE(hashtags_ai, ARRAY[]::text[])
    FROM notices
    ORDER BY created_at DESC;
    """

    logger.info(f"데이터베이스 연결 시도...")
    
    try:
        # 1. 데이터베이스 연결 (with 문으로 자동 close 보장)
        with psycopg2.connect(DATABASE_URL) as conn:
            logger.info("✅ 데이터베이스 연결 성공")
            
            # 2. 커서 생성 (with 문으로 자동 close 보장)
            with conn.cursor() as cur:
                
                # 3. CSV 파일 쓰기 (with 문으로 자동 close 보장)
                # encoding='utf-8'로 한국어 깨짐 방지
                # newline=''으로 CSV 파일의 불필요한 줄바꿈 방지
                with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    
                    # 4. 헤더 행(Header Row) 작성
                    writer.writerow(["title", "body_text", "hashtags"])
                    
                    logger.info("쿼리 실행...")
                    cur.execute(SQL_QUERY)
                    
                    total_rows = 0
                    
                    # 5. 데이터 행(Data Rows) 작성
                    # cur.fetchall() 대신 이터레이터로 순회하여 메모리 효율적 처리
                    for row in cur:
                        title, body_text, hashtags_list = row
                        
                        # 6. 데이터 변환
                        # hashtags_ai (text[]) 컬럼을 쉼표로 구분된 단일 문자열로 변환
                        # 예: ['#학사', '#취업'] -> "#학사,#취업"
                        hashtags_str = ",".join(hashtags_list)
                        
                        # 7. CSV에 행 쓰기
                        writer.writerow([title, body_text, hashtags_str])
                        total_rows += 1

        logger.info(f"🎉 {total_rows}개의 공지사항을 '{OUTPUT_FILE}'(으)로 성공적으로 추출했습니다.")

    except psycopg2.Error as db_err:
        logger.error(f"데이터베이스 오류 발생: {db_err}")
    except IOError as io_err:
        logger.error(f"파일 쓰기 오류 ({OUTPUT_FILE}): {io_err}")
    except Exception as e:
        logger.error(f"예상치 못한 오류 발생: {e}")

if __name__ == "__main__":
    export_data()