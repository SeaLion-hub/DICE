import os, time, json, psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from ai_processor import extract_notice_info

load_dotenv(encoding="utf-8")
URL   = os.getenv("DATABASE_URL")
BATCH = int(os.getenv("AI_BACKFILL_BATCH", "30"))
SLEEP = float(os.getenv("AI_SLEEP_SEC", "0.8"))

Q_SELECT = """
SELECT id, title, body_text
FROM notices
WHERE category_ai IS NULL
   OR qualification_ai IS NULL
   OR hashtags_ai IS NULL
ORDER BY created_at DESC
LIMIT %s;
"""

Q_UPDATE = """
UPDATE notices
SET category_ai=%s, start_at_ai=%s, end_at_ai=%s, qualification_ai=%s, updated_at=now()
WHERE id=%s;
"""

def main():
    with psycopg2.connect(URL) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(Q_SELECT, (BATCH,))
        rows = cur.fetchall()
        if not rows:
            print("✅ No rows to backfill."); return
        ok = fail = 0
        for r in rows:
            try:
                time.sleep(SLEEP)
                ai = extract_notice_info(r["body_text"] or "", r["title"] or "")
                cur.execute(Q_UPDATE, (
                    ai.get("category_ai"),
                    ai.get("start_date_ai"),
                    ai.get("end_date_ai"),
                    json.dumps(ai.get("qualification_ai") or {}, ensure_ascii=False),
                    r["id"]
                ))
                ok += 1
            except Exception as e:
                print("⚠️ backfill failed:", r["id"], e)
                fail += 1
        conn.commit()
        print(f"✨ Backfill done. ok={ok}, fail={fail}")

if __name__ == "__main__":
    main()
