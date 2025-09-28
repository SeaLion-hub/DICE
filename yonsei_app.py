# yonsei_app.py
"""
연세대학교 전체 단과대학 공지사항 통합 시스템 (DICE)
Railway 배포 개선 버전
"""

from flask import Flask, render_template_string, jsonify, send_from_directory, request, redirect, url_for
from datetime import datetime, timedelta
import re
import requests
import os
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
import bcrypt
import jwt
from flask_cors import CORS 
from dotenv import load_dotenv
import json
import logging
import traceback

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ===== 환경 변수 설정 =====
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "apify_api_xxxxxxxxxx")
DATABASE_URL = os.getenv("DATABASE_URL", "")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-here-change-in-production")

# Railway의 postgres:// -> postgresql:// 변환
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    logger.info("DATABASE_URL protocol updated to postgresql://")

# ===== DB 연결 함수 개선 =====
def get_db_connection():
    """PostgreSQL 데이터베이스 연결 (에러 처리 강화)"""
    try:
        if not DATABASE_URL:
            logger.error("DATABASE_URL is not set")
            return None
        
        # 연결 파라미터 추가
        conn = psycopg2.connect(
            DATABASE_URL,
            connect_timeout=10,
            options='-c statement_timeout=30000'
        )
        conn.autocommit = False
        return conn
    except psycopg2.OperationalError as e:
        logger.error(f"DB 연결 실패 (OperationalError): {e}")
        return None
    except Exception as e:
        logger.error(f"DB 연결 실패 (Exception): {e}")
        logger.error(traceback.format_exc())
        return None

def test_db_connection():
    """DB 연결 테스트"""
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                result = cur.fetchone()
                logger.info(f"DB 연결 테스트 성공: {result}")
                return True
        except Exception as e:
            logger.error(f"DB 연결 테스트 실패: {e}")
            return False
        finally:
            conn.close()
    return False

def init_db():
    """데이터베이스 초기화 - 단계별 실행 (개선된 버전)"""
    logger.info("Starting DB initialization...")
    
    conn = get_db_connection()
    if not conn:
        logger.error("DB initialization failed - no connection")
        return False
    
    try:
        # 1. Extensions 설치 시도 (개별 실행)
        extensions = [
            'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";',
            'CREATE EXTENSION IF NOT EXISTS "pgcrypto";'
        ]
        
        for ext_sql in extensions:
            try:
                with conn.cursor() as cur:
                    cur.execute(ext_sql)
                    conn.commit()
                logger.info(f"Extension executed: {ext_sql}")
            except Exception as ext_err:
                logger.warning(f"Extension skipped: {ext_err}")
                conn.rollback()
        
        # 2. ENUM 타입 생성 (개별 실행)
        enum_types = [
            """
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
                    CREATE TYPE user_role AS ENUM ('student', 'admin', 'moderator');
                END IF;
            END $$;
            """,
            """
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_category') THEN
                    CREATE TYPE notice_category AS ENUM (
                        'general','scholarship','internship','competition',
                        'recruitment','academic','seminar','event'
                    );
                END IF;
            END $$;
            """,
            """
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_status') THEN
                    CREATE TYPE notice_status AS ENUM ('active', 'archived', 'deleted');
                END IF;
            END $$;
            """
        ]
        
        for enum_sql in enum_types:
            try:
                with conn.cursor() as cur:
                    cur.execute(enum_sql)
                    conn.commit()
                logger.info("ENUM type created successfully")
            except Exception as e:
                logger.error(f"ENUM creation error: {e}")
                conn.rollback()
        
        # 3. 테이블 생성 - 순서대로 (개별 실행)
        tables = [
            # users 테이블
            """
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                name VARCHAR(100),
                student_id VARCHAR(20),
                major VARCHAR(100),
                gpa DECIMAL(3,2) CHECK (gpa >= 0 AND gpa <= 4.5),
                toeic_score INTEGER CHECK (toeic_score >= 0 AND toeic_score <= 990),
                role user_role DEFAULT 'student',
                is_active BOOLEAN DEFAULT true,
                email_verified BOOLEAN DEFAULT false,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP WITH TIME ZONE
            );
            """,
            
            # colleges 테이블
            """
            CREATE TABLE IF NOT EXISTS colleges (
                id VARCHAR(50) PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                name_en VARCHAR(100),
                icon VARCHAR(10),
                color VARCHAR(7),
                url VARCHAR(255),
                apify_task_id VARCHAR(100),
                crawl_enabled BOOLEAN DEFAULT true,
                display_order INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            
            # user_settings 테이블
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                push_notifications BOOLEAN DEFAULT true,
                email_notifications BOOLEAN DEFAULT true,
                deadline_alerts BOOLEAN DEFAULT true,
                ai_recommendations BOOLEAN DEFAULT true,
                notification_time TIME DEFAULT '09:00:00',
                deadline_alert_days INTEGER DEFAULT 3 CHECK (deadline_alert_days BETWEEN 1 AND 30),
                interested_categories notice_category[] DEFAULT ARRAY['general']::notice_category[],
                excluded_keywords TEXT[],
                filter_keywords TEXT[],
                notices_per_page INTEGER DEFAULT 20 CHECK (notices_per_page BETWEEN 10 AND 100),
                default_sort_order VARCHAR(20) DEFAULT 'date_desc',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            
            # notices 테이블
            """
            CREATE TABLE IF NOT EXISTS notices (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                college_id VARCHAR(50) NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,
                title VARCHAR(500) NOT NULL,
                content TEXT,
                department VARCHAR(200),
                writer VARCHAR(100),
                original_id VARCHAR(100),
                original_url VARCHAR(500),
                category notice_category DEFAULT 'general',
                status notice_status DEFAULT 'active',
                published_date DATE,
                deadline_date DATE,
                event_date DATE,
                view_count INTEGER DEFAULT 0,
                click_count INTEGER DEFAULT 0,
                crawled_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_checked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                content_hash VARCHAR(64),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_notice_per_college UNIQUE (college_id, original_id)
            );
            """,
            
            # 나머지 테이블들
            """
            CREATE TABLE IF NOT EXISTS user_college_subscriptions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                college_id VARCHAR(50) NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,
                notifications_enabled BOOLEAN DEFAULT true,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, college_id)
            );
            """,
            
            """
            CREATE TABLE IF NOT EXISTS user_notice_interactions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                notice_id UUID NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
                viewed BOOLEAN DEFAULT false,
                clicked BOOLEAN DEFAULT false,
                bookmarked BOOLEAN DEFAULT false,
                hidden BOOLEAN DEFAULT false,
                viewed_at TIMESTAMP WITH TIME ZONE,
                clicked_at TIMESTAMP WITH TIME ZONE,
                bookmarked_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, notice_id)
            );
            """,
            
            """
            CREATE TABLE IF NOT EXISTS crawl_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                college_id VARCHAR(50) REFERENCES colleges(id) ON DELETE SET NULL,
                task_id VARCHAR(100),
                run_id VARCHAR(100),
                status VARCHAR(50),
                error_message TEXT,
                notices_fetched INTEGER DEFAULT 0,
                notices_new INTEGER DEFAULT 0,
                notices_updated INTEGER DEFAULT 0,
                started_at TIMESTAMP WITH TIME ZONE,
                completed_at TIMESTAMP WITH TIME ZONE,
                duration_seconds INTEGER,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        ]
        
        # 테이블 생성 (개별 실행)
        for i, table_sql in enumerate(tables):
            try:
                with conn.cursor() as cur:
                    cur.execute(table_sql)
                    conn.commit()
                logger.info(f"Table {i+1}/{len(tables)} created successfully")
            except Exception as e:
                logger.error(f"Table {i+1} creation error: {e}")
                conn.rollback()
        
        # 4. 인덱스 생성 (개별 실행)
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);",
            "CREATE INDEX IF NOT EXISTS idx_notices_college ON notices(college_id);",
            "CREATE INDEX IF NOT EXISTS idx_notices_published ON notices(published_date DESC);",
        ]
        
        for index_sql in indexes:
            try:
                with conn.cursor() as cur:
                    cur.execute(index_sql)
                    conn.commit()
            except Exception as e:
                logger.warning(f"Index creation warning: {e}")
                conn.rollback()
        
        # 5. 초기 데이터 삽입 (colleges)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM colleges")
            college_count = cur.fetchone()[0]
        
        if college_count == 0:
            logger.info("Inserting initial college data...")
            colleges_data = [
                ('main','메인 공지사항','🏫','#003876','https://www.yonsei.ac.kr','VsNDqFr5fLLIi2Xh1',0),
                ('liberal','문과대학','📚','#8B4513','https://liberal.yonsei.ac.kr','L5AS9TZWUMorttUJJ',1),
                ('business','상경대학','📊','#FFB700','https://soe.yonsei.ac.kr','yJ8Rp9AhTSVCw7Yt8',2),
                ('management','경영대학','💼','#1E90FF','https://ysb.yonsei.ac.kr','DjsOsls6pCpaQaKq9',3),
                ('engineering','공과대학','⚙️','#DC143C','https://engineering.yonsei.ac.kr','tdcYhb8OaDnBHI8jJr',4),
                ('life','생명시스템대학','🧬','#228B22','https://sys.yonsei.ac.kr','gOKavS1YNKhNUVsNQ',5),
                ('ai','인공지능융합대학','🤖','#9370DB','https://ai.yonsei.ac.kr','qb6M6hbdm2fnhxfeg',6),
                ('theology','신과대학','✝️','#4B0082','https://theology.yonsei.ac.kr','9akDlFeStRHdeps4t',7),
                ('social','사회과학대학','🏛️','#2E8B57','https://yeri.yonsei.ac.kr/socsci','hNSAPYSS35RscOWWm',8),
                ('music','음악대학','🎵','#FF1493','https://music.yonsei.ac.kr','B3xYzP1Jqo1jVH1Me',9),
                ('human','생활과학대학','🏠','#FF6347','https://che.yonsei.ac.kr','K5kXEuXSyZzY5uwpn',10),
                ('education','교육과학대학','🎓','#4169E1','https://educa.yonsei.ac.kr','9XfmKGnPdDQWZkUjW',11),
                ('underwood','언더우드국제대학','🌏','#FF8C00','https://uic.yonsei.ac.kr','Xz2t1SAdshoLSDslB',12),
                ('global','글로벌인재대학','🌐','#008B8B','https://global.yonsei.ac.kr','BwiB4aHdY2uyP4txl',13),
                ('medicine','의과대학','⚕️','#B22222','https://medicine.yonsei.ac.kr','oAgxPnIMOv2IYhZej',14),
                ('dentistry','치과대학','🦷','#5F9EA0','https://dentistry.yonsei.ac.kr','etPqNCyaZNI4A8sEl',15),
                ('nursing','간호대학','💊','#DB7093','https://nursing.yonsei.ac.kr','I04xneYTZMJ8jAn4r',16),
                ('pharmacy','약학대학','💉','#663399','https://pharmacy.yonsei.ac.kr','gjqRcgjHJr4frQhma',17)
            ]
            
            for college in colleges_data:
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO colleges (id, name, icon, color, url, apify_task_id, display_order)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, college)
                        conn.commit()
                except Exception as e:
                    logger.warning(f"College insert warning: {e}")
                    conn.rollback()
            
            logger.info("Initial college data inserted")
        
        logger.info("DB initialization completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"DB initialization failed: {e}")
        logger.error(traceback.format_exc())
        return False
    finally:
        conn.close()

# ============= 기존 함수들 (변경 없음) =============
def format_content(content):
    """공지사항 내용 포맷팅"""
    if not content:
        return ""
    content = re.sub(r'(?<!\n)(\d+\.)', r'\n\n\1', content)
    content = re.sub(r'(?<!\n)([가-힣]\.)', r'\n\1', content)
    content = re.sub(r'[ \t]+', ' ', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()

def detect_notice_category(notice):
    """공지사항 카테고리 자동 감지"""
    title = (notice.get('title', '') or '').lower()
    content = (notice.get('content', '') or '').lower()
    text = f"{title} {content}"
    
    if any(keyword in text for keyword in ['장학', 'scholarship']):
        return 'scholarship'
    elif any(keyword in text for keyword in ['인턴', 'intern']):
        return 'internship'
    elif any(keyword in text for keyword in ['공모', 'competition', '대회']):
        return 'competition'
    elif any(keyword in text for keyword in ['채용', 'recruit', '모집']):
        return 'recruitment'
    elif any(keyword in text for keyword in ['수강', '강의', '학사', 'academic']):
        return 'academic'
    elif any(keyword in text for keyword in ['세미나', 'seminar', '강연']):
        return 'seminar'
    elif any(keyword in text for keyword in ['행사', 'event', '축제']):
        return 'event'
    else:
        return 'general'

def save_notices_to_db(college_key, notices):
    """크롤링한 공지사항을 DB에 저장"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cur:
            notices_new = 0
            notices_updated = 0
            
            for notice in notices:
                content_hash = hashlib.sha256(
                    f"{notice['title']}{notice.get('content', '')}".encode()
                ).hexdigest()
                
                published_date = None
                if notice.get('date'):
                    try:
                        published_date = datetime.strptime(notice['date'], '%Y-%m-%d').date()
                    except:
                        pass
                
                category = detect_notice_category(notice)
                
                cur.execute("""
                    INSERT INTO notices (
                        college_id, title, content, department, writer,
                        original_id, original_url, published_date,
                        content_hash, view_count, category
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (college_id, original_id) 
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        department = EXCLUDED.department,
                        writer = EXCLUDED.writer,
                        original_url = EXCLUDED.original_url,
                        published_date = EXCLUDED.published_date,
                        content_hash = EXCLUDED.content_hash,
                        category = EXCLUDED.category,
                        last_checked_at = CURRENT_TIMESTAMP
                    RETURNING (xmax = 0) AS is_new
                """, (
                    college_key, notice['title'], notice.get('content'),
                    notice.get('department'), notice.get('writer'),
                    notice.get('id'), notice.get('url'),
                    published_date, content_hash,
                    int(notice.get('views', '0').replace(',', '') if isinstance(notice.get('views'), str) else notice.get('views', 0)),
                    category
                ))
                
                result = cur.fetchone()
                if result and result[0]:
                    notices_new += 1
                else:
                    notices_updated += 1
            
            cur.execute("""
                INSERT INTO crawl_logs (
                    college_id, status, notices_fetched,
                    notices_new, notices_updated,
                    started_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                college_key, 'success', len(notices),
                notices_new, notices_updated,
                datetime.now(), datetime.now()
            ))
            
            conn.commit()
            logger.info(f"저장 완료 - {college_key}: 신규 {notices_new}, 업데이트 {notices_updated}")
            return True
    except Exception as e:
        logger.error(f"DB 저장 실패: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def get_notices_from_db(college_key, limit=50):
    """DB에서 공지사항 조회"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if college_key == 'all':
                cur.execute("""
                    SELECT n.id, n.title, n.content, n.department as writer,
                           n.published_date as date, n.original_url as url,
                           n.view_count as views, n.category,
                           c.name as college_name, c.icon as college_icon, 
                           c.color as college_color, n.college_id
                    FROM notices n
                    JOIN colleges c ON n.college_id = c.id
                    WHERE n.status = 'active'
                    ORDER BY n.published_date DESC
                    LIMIT %s
                """, (limit,))
            else:
                cur.execute("""
                    SELECT n.id, n.title, n.content, n.department as writer,
                           n.published_date as date, n.original_url as url,
                           n.view_count as views, n.category,
                           c.name as college_name, c.icon as college_icon, 
                           c.color as college_color, n.college_id
                    FROM notices n
                    JOIN colleges c ON n.college_id = c.id
                    WHERE n.college_id = %s AND n.status = 'active'
                    ORDER BY n.published_date DESC
                    LIMIT %s
                """, (college_key, limit))
            
            notices = cur.fetchall()
            for notice in notices:
                if notice['date']:
                    notice['date'] = notice['date'].strftime('%Y-%m-%d')
                notice['views'] = f"{notice['views']:,}"
                notice['id'] = str(notice['id'])
                notice['college'] = {
                    'key': notice['college_id'],
                    'name': notice['college_name'],
                    'icon': notice['college_icon'],
                    'color': notice['college_color']
                }
            
            return notices
    except Exception as e:
        logger.error(f"DB 조회 실패: {e}")
        return []
    finally:
        conn.close()

def get_colleges_from_db():
    """DB에서 단과대학 정보 조회"""
    conn = get_db_connection()
    if not conn:
        return {}
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, icon, color, url, apify_task_id
                FROM colleges
                WHERE crawl_enabled = true
                ORDER BY display_order, name
            """)
            
            colleges = {}
            for row in cur.fetchall():
                colleges[row['id']] = {
                    'name': row['name'],
                    'icon': row['icon'],
                    'color': row['color'],
                    'url': row['url'],
                    'task_id': row['apify_task_id']
                }
            return colleges
    except Exception as e:
        logger.error(f"단과대학 조회 실패: {e}")
        return {}
    finally:
        conn.close()

def get_apify_data(task_id):
    """Apify Task의 최신 실행 결과 가져오기"""
    try:
        url = f"https://api.apify.com/v2/actor-tasks/{task_id}/runs"
        headers = {"Authorization": f"Bearer {APIFY_TOKEN}"}
        params = {"limit": 1, "desc": "true"}
        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code != 200:
            return None

        runs = response.json().get('data', {}).get('items', [])
        if not runs:
            return None

        latest_run = runs[0]
        dataset_id = latest_run.get('defaultDatasetId')
        if not dataset_id:
            return None

        dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        dataset_response = requests.get(dataset_url, headers=headers, timeout=10)
        if dataset_response.status_code != 200:
            return None

        items = dataset_response.json()
        valid_items = []

        for idx, item in enumerate(items[:50]):
            if not item:
                continue

            title = (
                item.get('title') or item.get('name') or 
                item.get('headline') or item.get('subject')
            )
            content = item.get('content') or item.get('body') or item.get('text') or ''
            date = item.get('date') or item.get('publishedAt') or ''
            url_field = item.get('url') or item.get('link') or ''
            dept = item.get('department') or item.get('writer') or ''
            views = item.get('views') or '0'

            if not title:
                continue

            valid_items.append({
                'id': f'{task_id}_{idx}',
                'title': title,
                'content': content,
                'date': str(date) if date else '',
                'url': url_field,
                'department': dept,
                'writer': dept,
                'views': str(views),
            })

        return valid_items or None

    except Exception as e:
        logger.error(f"Error fetching Apify data: {e}")
        return None

# ============= AUTH API 개선 =============
@app.route('/api/auth/register', methods=['POST'])
def register():
    """회원가입 API"""
    try:
        data = request.get_json()
        
        if not data or not data.get('email') or not data.get('password'):
            return jsonify({'success': False, 'message': '이메일과 비밀번호는 필수입니다'}), 400
        
        conn = get_db_connection()
        if not conn:
            logger.error("Register: DB connection failed")
            return jsonify({'success': False, 'message': 'DB 연결 실패'}), 500
        
        try:
            password_hash = bcrypt.hashpw(
                data['password'].encode('utf-8'), 
                bcrypt.gensalt()
            ).decode('utf-8')
            
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO users (
                        email, password_hash, name, student_id,
                        major, gpa, toeic_score
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, email, name, student_id, major, gpa, toeic_score
                """, (
                    data['email'], password_hash, data.get('name'),
                    data.get('student_id'), data.get('major'),
                    data.get('gpa'), data.get('toeic_score')
                ))
                
                user = cur.fetchone()
                
                cur.execute("""
                    INSERT INTO user_settings (user_id)
                    VALUES (%s)
                """, (user['id'],))
                
                conn.commit()
                
                token = jwt.encode(
                    {'user_id': str(user['id']), 'email': user['email']},
                    JWT_SECRET_KEY,
                    algorithm='HS256'
                )
                
                logger.info(f"User registered successfully: {user['email']}")
                
                return jsonify({
                    'success': True,
                    'user': {
                        'id': str(user['id']),
                        'email': user['email'],
                        'name': user['name'],
                        'major': user['major'],
                        'gpa': float(user['gpa']) if user['gpa'] else None,
                        'toeic_score': user['toeic_score']
                    },
                    'token': token
                })
                
        except psycopg2.IntegrityError:
            conn.rollback()
            return jsonify({'success': False, 'message': '이미 등록된 이메일입니다'}), 400
        except Exception as e:
            conn.rollback()
            logger.error(f"회원가입 오류: {e}")
            return jsonify({'success': False, 'message': '회원가입 처리 중 오류가 발생했습니다'}), 500
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Register error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': '서버 오류가 발생했습니다'}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    """로그인 API"""
    try:
        data = request.get_json()
        
        if not data or not data.get('email') or not data.get('password'):
            return jsonify({'success': False, 'message': '이메일과 비밀번호를 입력해주세요'}), 400
        
        conn = get_db_connection()
        if not conn:
            logger.error("Login: DB connection failed")
            return jsonify({'success': False, 'message': 'DB 연결 실패'}), 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, email, password_hash, name, student_id,
                           major, gpa, toeic_score
                    FROM users
                    WHERE email = %s AND is_active = true
                """, (data['email'],))
                
                user = cur.fetchone()
                
                if not user:
                    return jsonify({'success': False, 'message': '사용자를 찾을 수 없습니다'}), 404
                
                if not bcrypt.checkpw(data['password'].encode('utf-8'), 
                                    user['password_hash'].encode('utf-8')):
                    return jsonify({'success': False, 'message': '비밀번호가 일치하지 않습니다'}), 401
                
                cur.execute("""
                    UPDATE users SET last_login_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (user['id'],))
                conn.commit()
                
                token = jwt.encode(
                    {'user_id': str(user['id']), 'email': user['email']},
                    JWT_SECRET_KEY,
                    algorithm='HS256'
                )
                
                logger.info(f"User logged in successfully: {user['email']}")
                
                return jsonify({
                    'success': True,
                    'user': {
                        'id': str(user['id']),
                        'email': user['email'],
                        'name': user['name'],
                        'major': user['major'],
                        'gpa': float(user['gpa']) if user['gpa'] else None,
                        'toeic_score': user['toeic_score']
                    },
                    'token': token
                })
                
        except Exception as e:
            logger.error(f"로그인 처리 오류: {e}")
            return jsonify({'success': False, 'message': '로그인 처리 중 오류가 발생했습니다'}), 500
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Login error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': '서버 오류가 발생했습니다'}), 500

# ============= 페이지 라우트 개선 =============
@app.route('/')
def index():
    """메인 페이지"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/auth.html')
def serve_auth():
    return send_from_directory(app.static_folder, 'auth.html')

@app.route('/dashboard.html')
def serve_dashboard():
    return send_from_directory(app.static_folder, 'dashboard.html')

@app.route('/settings.html')
def serve_settings():
    return send_from_directory(app.static_folder, 'settings.html')

# 추가 라우트 - Railway에서 필요
@app.route('/auth')
def auth_redirect():
    return redirect('/auth.html')

@app.route('/dashboard')
def dashboard_redirect():
    return redirect('/dashboard.html')

@app.route('/settings')
def settings_redirect():
    return redirect('/settings.html')

# ============= API 라우트 =============
@app.route('/api/colleges')
def get_colleges():
    """단과대학 목록 조회"""
    colleges = get_colleges_from_db()
    return jsonify({
        'success': True,
        'colleges': colleges
    })

@app.route('/api/notices/<college_key>')
def get_notices(college_key):
    """특정 단과대학 공지사항 조회"""
    colleges = get_colleges_from_db()
    if college_key != 'all' and college_key not in colleges:
        return jsonify({'success': False, 'message': 'Invalid college'})
    
    notices = get_notices_from_db(college_key)
    
    if not notices and college_key != 'all' and APIFY_TOKEN != 'apify_api_xxxxxxxxxx':
        college = colleges.get(college_key)
        if college and college.get('task_id'):
            apify_data = get_apify_data(college['task_id'])
            if apify_data:
                save_notices_to_db(college_key, apify_data)
                notices = get_notices_from_db(college_key)
    
    college_info = None
    if college_key == 'all':
        college_info = {
            'key': 'all',
            'name': '전체 공지사항',
            'icon': '📋',
            'color': '#2563eb'
        }
    else:
        college = colleges.get(college_key)
        if college:
            college_info = {
                'key': college_key,
                'name': college['name'],
                'icon': college['icon'],
                'color': college['color']
            }
    
    return jsonify({
        'success': True,
        'college': college_info,
        'notices': notices
    })

@app.route('/api/health')
def health_check():
    """시스템 상태 확인"""
    db_connected = False
    colleges_count = 0
    users_count = 0
    notices_count = 0
    
    try:
        conn = get_db_connection()
        if conn:
            db_connected = True
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM colleges")
                colleges_count = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM users")
                users_count = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM notices")
                notices_count = cur.fetchone()[0]
            conn.close()
    except Exception as e:
        logger.error(f"Health check error: {e}")
    
    return jsonify({
        'status': 'healthy' if db_connected else 'degraded',
        'message': 'DICE 서버가 정상 작동 중입니다' if db_connected else 'DB 연결 문제가 있습니다',
        'timestamp': datetime.now().isoformat(),
        'db_connected': db_connected,
        'stats': {
            'colleges': colleges_count,
            'users': users_count,
            'notices': notices_count
        }
    })

@app.route('/api/db/test')
def test_db():
    """DB 연결 테스트 엔드포인트"""
    result = test_db_connection()
    return jsonify({
        'success': result,
        'database_url_configured': bool(DATABASE_URL),
        'message': 'DB 연결 성공' if result else 'DB 연결 실패'
    })

@app.route('/api/db/init')
def init_db_endpoint():
    """DB 초기화 엔드포인트 (개발용)"""
    result = init_db()
    return jsonify({
        'success': result,
        'message': 'DB 초기화 성공' if result else 'DB 초기화 실패'
    })

# 오류 핸들러
@app.errorhandler(404)
def not_found(error):
    # HTML 파일 요청인 경우 index.html로 리다이렉트
    if request.path.endswith('.html'):
        return send_from_directory(app.static_folder, 'index.html')
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Railway 배포 시작
    logger.info("="*60)
    logger.info("🎓 연세대학교 통합 공지사항 시스템 (DICE)")
    logger.info("="*60)
    logger.info(f"💾 DATABASE_URL 설정: {bool(DATABASE_URL)}")
    
    # DB 연결 테스트
    if test_db_connection():
        logger.info("✅ DB 연결 테스트 성공")
        # DB 초기화
        if init_db():
            logger.info("✅ DB 초기화 완료")
        else:
            logger.warning("⚠️ DB 초기화 실패 - 수동으로 /api/db/init 접근 필요")
    else:
        logger.error("❌ DB 연결 실패 - DATABASE_URL 확인 필요")
    
    # Railway 환경에서는 PORT 환경 변수를 사용
    PORT = int(os.getenv('PORT', 8080))
    logger.info(f"🌐 서버 포트: {PORT}")
    logger.info("="*60)
    
    app.run(debug=False, host='0.0.0.0', port=PORT)