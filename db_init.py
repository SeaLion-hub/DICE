# db_init.py
import os
import re
import sqlparse
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

# ---------- helpers ----------
def get_db_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 환경변수가 없습니다.")
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url

def load_schema_sql(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"스키마 파일을 찾을 수 없습니다: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def normalize_sql(s: str) -> str:
    # 공백/개행 정규화 + 소문자
    s2 = re.sub(r"\s+", " ", s.strip())
    return s2.lower()

def split_statements(sql: str):
    # sqlparse로 안전 분리
    statements = [s.strip() for s in sqlparse.split(sql) if s.strip()]
    # -- 주석으로 시작하는 문장은 제거
    statements = [s for s in statements if not s.strip().startswith("--")]
    return statements

# 패턴
RE_EXT   = re.compile(r"^\s*create\s+extension\b", re.I)
RE_TYPE  = re.compile(r"^\s*create\s+type\b", re.I)
RE_TABLE = re.compile(r"^\s*create\s+table\b", re.I)
RE_INDEX = re.compile(r"^\s*create\s+index\b", re.I)
RE_FUNC  = re.compile(r"^\s*create\s+or\s+replace\s+function\b|\breturns\s+trigger\b", re.I)
RE_TRIG  = re.compile(r"^\s*(create\s+trigger|drop\s+trigger)\b", re.I)
RE_INS   = re.compile(r"^\s*insert\b", re.I)
RE_VIEW  = re.compile(r"^\s*create\s+(or\s+replace\s+)?view\b", re.I)

def is_users_create(stmt: str) -> bool:
    # users 테이블 생성문을 탄탄하게 잡기 (schema.qualify/개행/공백 모두 허용)
    n = normalize_sql(stmt)
    return (
        n.startswith("create table") and
        re.search(r"\bcreate\s+table\s+(if\s+not\s+exists\s+)?(\"?public\"?\.)?\"?users\"?\b", n)
        is not None
    )

def bucketize(statements):
    buckets = {
        "ext": [], "type": [], "table_users": [], "table": [],
        "index": [], "func": [], "trigger": [], "insert": [], "view": [], "other": []
    }
    for stmt in statements:
        t = stmt.strip()
        if RE_EXT.match(t):
            buckets["ext"].append(stmt)
        elif RE_TYPE.match(t):
            buckets["type"].append(stmt)
        elif RE_TABLE.match(t):
            if is_users_create(t):
                buckets["table_users"].append(stmt)
            else:
                buckets["table"].append(stmt)
        elif RE_INDEX.match(t):
            buckets["index"].append(stmt)
        elif RE_FUNC.match(t):
            buckets["func"].append(stmt)
        elif RE_TRIG.match(t):
            buckets["trigger"].append(stmt)
        elif RE_INS.match(t):
            buckets["insert"].append(stmt)
        elif RE_VIEW.match(t):
            buckets["view"].append(stmt)
        else:
            buckets["other"].append(stmt)
    return buckets

# PostgreSQL SQLSTATE codes we want to ignore in idempotent runs
PG_DUPLICATE_OBJECT = "42710"  # duplicate_object (type, function 등)
PG_DUPLICATE_TABLE  = "42P07"  # duplicate_table
PG_DUPLICATE_SCHEMA = "42P06"
PG_DUPLICATE_ALIAS  = "42712"
PG_DUPLICATE_COLUMN = "42701"
PG_DUPLICATE_PKEY   = "23505"  # unique violation (INSERT ON CONFLICT 없는 초기데이터 등)
PG_UNDEFINED_TABLE  = "42P01"  # undefined_table

def should_ignore_error(phase: str, err: DBAPIError) -> bool:
    """재실행 시 무시 가능한 에러는 건너뛰고 계속."""
    pgcode = getattr(getattr(err, "orig", None), "pgcode", None)
    if not pgcode:
        return False
    # 타입/인덱스/뷰/트리거/함수/테이블 재실행 시의 중복은 무시
    if pgcode in {PG_DUPLICATE_OBJECT, PG_DUPLICATE_TABLE, PG_DUPLICATE_SCHEMA, PG_DUPLICATE_ALIAS, PG_DUPLICATE_COLUMN}:
        return True
    # 초기데이터 중복(유니크 위반)도 무시
    if phase == "Initial Data" and pgcode == PG_DUPLICATE_PKEY:
        return True
    return False

def run_statements(engine, statements, title):
    if not statements:
        return
    print(f"▶ {title}: {len(statements)} statements")
    with engine.begin() as conn:
        for i, stmt in enumerate(statements, start=1):
            try:
                conn.exec_driver_sql(stmt)
            except DBAPIError as e:
                if should_ignore_error(title, e):
                    # 이미 존재 → 스킵하고 계속
                    continue
                # 실패한 문장 로그 후 중단
                print(f"\n❌ 실패 ({title} #{i}):\n{stmt}\n")
                raise

def apply_schema(engine, sql: str):
    stmts = split_statements(sql)
    buckets = bucketize(stmts)

    # users 테이블이 정말 버킷에 들어갔는지 가드
    if not buckets["table_users"]:
        # 최후의 보루: 테이블들 중에서 users 포함된 문장을 찾아 빼오기
        for s in list(buckets["table"]):
            if is_users_create(s):
                buckets["table_users"].append(s)
                buckets["table"].remove(s)
                break

    # 실행 순서
    run_statements(engine, buckets["ext"],         "Extensions")
    run_statements(engine, buckets["type"],        "Types")
    run_statements(engine, buckets["table_users"], "Tables(users first)")
    run_statements(engine, buckets["table"],       "Tables(others)")
    run_statements(engine, buckets["index"],       "Indexes")
    run_statements(engine, buckets["func"],        "Functions")
    run_statements(engine, buckets["trigger"],     "Triggers")
    run_statements(engine, buckets["insert"],      "Initial Data")
    run_statements(engine, buckets["view"],        "Views")
    run_statements(engine, buckets["other"],       "Other")

def main():
    db_url = get_db_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    schema_sql = load_schema_sql("schema.sql")
    apply_schema(engine, schema_sql)
    print("✅ schema.sql 적용 완료")

if __name__ == "__main__":
    main()
