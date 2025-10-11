import os
import psycopg2
from dotenv import load_dotenv

# .env 파일에서 DATABASE_URL을 로드합니다.
load_dotenv(encoding="utf-8")
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment. Check your .env file or Railway variables.")

try:
    # 데이터베이스에 연결합니다.
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            print("=====================================================")
            print("🗑️  Attempting to delete all data from 'notices' table...")
            
            # TRUNCATE 명령어를 실행하여 테이블을 비웁니다.
            cur.execute("TRUNCATE TABLE notices RESTART IDENTITY CASCADE;")
            
            # 변경사항을 최종 적용합니다.
            conn.commit()
            
            print("✅  Successfully deleted all data from the 'notices' table.")
            print("=====================================================")

except psycopg2.Error as e:
    print(f"❌ Database Error: {e}")
    print("   Please check if the DATABASE_URL is correct and the database is running.")
except Exception as e:
    print(f"❌ An unexpected error occurred: {e}")