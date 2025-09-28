# yonsei_app.py
"""
연세대학교 전체 단과대학 공지사항 통합 시스템 (DICE)
- 18개 단과대학 공지사항 통합
- PostgreSQL DB 연동
- 회원가입/로그인 API
- 크롤링 데이터 DB 저장
- 업데이트된 스키마 지원
"""

from flask import Flask, render_template_string, jsonify, send_from_directory, request
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

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ===== 환경 변수 설정 =====
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "apify_api_xxxxxxxxxx")
DATABASE_URL = os.getenv("DATABASE_URL", "")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-here")

# Railway에서 제공하는 DATABASE_URL이 postgres://로 시작하는 경우 postgresql://로 변경
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ===== DB 연결 함수 =====
def get_db_connection():
    """PostgreSQL 데이터베이스 연결"""
    try:
        if not DATABASE_URL:
            logger.error("DATABASE_URL is not set")
            return None
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"DB 연결 실패: {e}")
        return None

def init_db():
    """데이터베이스 초기화 - 테이블이 없으면 생성"""
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # 확장 설치 (권한이 없으면 무시)
                try:
                    cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
                    cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
                except Exception as ext_err:
                    logger.warning(f"Extension creation skipped: {ext_err}")
                
                # schema.sql 파일 실행 (존재하는 경우)
                if os.path.exists('schema.sql'):
                    with open('schema.sql', 'r', encoding='utf-8') as f:
                        schema_content = f.read()
                        # 여러 명령문을 나누어 실행
                        statements = [s.strip() for s in schema_content.split(';') if s.strip()]
                        for statement in statements:
                            try:
                                cur.execute(statement + ';')
                            except psycopg2.errors.DuplicateObject:
                                # 이미 존재하는 타입은 무시
                                pass
                            except Exception as e:
                                logger.warning(f"Statement execution warning: {e}")
                
                conn.commit()
                logger.info("DB 초기화 완료")
        except Exception as e:
            logger.error(f"DB 초기화 실패: {e}")
            conn.rollback()
        finally:
            conn.close()

def format_content(content):
    """공지사항 내용 포맷팅"""
    if not content:
        return ""
    content = re.sub(r'(?<!\n)(\d+\.)', r'\n\n\1', content)
    content = re.sub(r'(?<!\n)([가-힣]\.)', r'\n\1', content)
    content = re.sub(r'[ \t]+', ' ', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()

def save_notices_to_db(college_key, notices):
    """크롤링한 공지사항을 DB에 저장 (새 스키마 버전)"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cur:
            notices_new = 0
            notices_updated = 0
            
            for notice in notices:
                # content_hash 생성
                content_hash = hashlib.sha256(
                    f"{notice['title']}{notice.get('content', '')}".encode()
                ).hexdigest()
                
                # 날짜 파싱
                published_date = None
                if notice.get('date'):
                    try:
                        published_date = datetime.strptime(notice['date'], '%Y-%m-%d').date()
                    except:
                        pass
                
                # 카테고리 자동 감지
                category = detect_notice_category(notice)
                
                # notices 테이블에 저장 (중복 시 업데이트)
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
                if result and result[0]:  # is_new
                    notices_new += 1
                else:
                    notices_updated += 1
            
            # crawl_logs에 기록
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

def get_notices_from_db(college_key, limit=50):
    """DB에서 공지사항 조회 (새 스키마 버전)"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # colleges 테이블에서 정보 가져오기
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
            # 날짜 및 조회수 포맷팅
            for notice in notices:
                if notice['date']:
                    notice['date'] = notice['date'].strftime('%Y-%m-%d')
                notice['views'] = f"{notice['views']:,}"
                notice['id'] = str(notice['id'])
                # college 정보 추가 (호환성을 위해)
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

# ============= AUTH API =============
@app.route('/api/auth/register', methods=['POST'])
def register():
    """회원가입 API (새 스키마 버전)"""
    data = request.get_json()
    conn = get_db_connection()
    
    if not conn:
        return jsonify({'success': False, 'message': 'DB 연결 실패'}), 500
    
    try:
        # 비밀번호 해싱
        password_hash = bcrypt.hashpw(
            data['password'].encode('utf-8'), 
            bcrypt.gensalt()
        ).decode('utf-8')
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 사용자 생성
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
            
            # user_settings 기본값 생성
            cur.execute("""
                INSERT INTO user_settings (user_id)
                VALUES (%s)
            """, (user['id'],))
            
            conn.commit()
            
            # JWT 토큰 생성
            token = jwt.encode(
                {'user_id': str(user['id']), 'email': user['email']},
                JWT_SECRET_KEY,
                algorithm='HS256'
            )
            
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
        return jsonify({'success': False, 'message': '이미 등록된 이메일입니다'}), 400
    except Exception as e:
        logger.error(f"회원가입 오류: {e}")
        return jsonify({'success': False, 'message': '회원가입 실패'}), 500
    finally:
        conn.close()

@app.route('/api/auth/login', methods=['POST'])
def login():
    """로그인 API (새 스키마 버전)"""
    data = request.get_json()
    conn = get_db_connection()
    
    if not conn:
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
            
            # 비밀번호 검증
            if not bcrypt.checkpw(data['password'].encode('utf-8'), 
                                user['password_hash'].encode('utf-8')):
                return jsonify({'success': False, 'message': '비밀번호가 일치하지 않습니다'}), 401
            
            # 마지막 로그인 시간 업데이트
            cur.execute("""
                UPDATE users SET last_login_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (user['id'],))
            conn.commit()
            
            # JWT 토큰 생성
            token = jwt.encode(
                {'user_id': str(user['id']), 'email': user['email']},
                JWT_SECRET_KEY,
                algorithm='HS256'
            )
            
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
        logger.error(f"로그인 오류: {e}")
        return jsonify({'success': False, 'message': '로그인 실패'}), 500
    finally:
        conn.close()

# ============= 페이지 라우트 =============
@app.route('/')
def index():
    """메인 페이지 - index.html 제공"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/auth')
def serve_auth():
    return send_from_directory(app.static_folder, 'auth.html')

@app.route('/dashboard')
def serve_dashboard():
    return send_from_directory(app.static_folder, 'dashboard.html')

@app.route('/settings')
def serve_settings():
    return send_from_directory(app.static_folder, 'settings.html')

# SPA 라우팅을 위한 catch-all 라우트
@app.route('/<path:path>')
def catch_all(path):
    """정적 파일이 있으면 서빙, 없으면 해당 HTML로 리다이렉트"""
    file_path = os.path.join(app.static_folder, path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_from_directory(app.static_folder, path)
    else:
        # HTML 페이지 요청인 경우 해당 파일로 리다이렉트
        if path in ['auth', 'dashboard', 'settings']:
            return send_from_directory(app.static_folder, f'{path}.html')
        # 그 외의 경우 index.html로 리다이렉트
        return send_from_directory(app.static_folder, 'index.html')

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
    # DB에서 단과대학 정보 확인
    colleges = get_colleges_from_db()
    if college_key != 'all' and college_key not in colleges:
        return jsonify({'success': False, 'message': 'Invalid college'})
    
    notices = []
    
    # 먼저 DB에서 조회
    notices = get_notices_from_db(college_key)
    
    # DB에 데이터가 없고 특정 단과대학이며 Apify 토큰이 있으면 크롤링
    if not notices and college_key != 'all' and APIFY_TOKEN != 'apify_api_xxxxxxxxxx':
        college = colleges.get(college_key)
        if college and college.get('task_id'):
            apify_data = get_apify_data(college['task_id'])
            if apify_data:
                # DB에 저장
                save_notices_to_db(college_key, apify_data)
                notices = get_notices_from_db(college_key)
    
    # 응답 데이터 구성
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

@app.route('/notice/<college_key>/<notice_id>')
def notice_detail(college_key, notice_id):
    """공지사항 상세 페이지"""
    colleges = get_colleges_from_db()
    if college_key not in colleges:
        return "잘못된 접근입니다.", 404
    
    college = colleges[college_key]
    notice = None
    
    # DB에서 상세 정보 조회
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT n.*, c.name as college_name, c.icon as college_icon, c.color as college_color
                    FROM notices n
                    JOIN colleges c ON n.college_id = c.id
                    WHERE n.id = %s OR n.original_id = %s
                    LIMIT 1
                """, (notice_id, notice_id))
                
                notice_data = cur.fetchone()
                if notice_data:
                    notice = {
                        'id': str(notice_data['id']),
                        'title': notice_data['title'],
                        'content': notice_data['content'] or '',
                        'date': notice_data['published_date'].strftime('%Y-%m-%d') if notice_data['published_date'] else '',
                        'url': notice_data['original_url'] or college['url'],
                        'department': notice_data['department'] or notice_data['college_name'],
                        'views': f"{notice_data['view_count']:,}"
                    }
                    
                    # 조회수 증가 함수 호출
                    cur.execute("SELECT increment_notice_view_count(%s)", (notice_data['id'],))
                    conn.commit()
        except Exception as e:
            logger.error(f"상세 조회 오류: {e}")
        finally:
            conn.close()
    
    if not notice:
        return "공지사항을 찾을 수 없습니다.", 404
    
    notice['formatted_content'] = format_content(notice['content'])
    
    # 단과대학 정보 추가
    college_info = {
        'name': college['name'],
        'icon': college['icon'],
        'color': college['color']
    }
    
    DETAIL_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="ko">
    <head>
      <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>{{ notice.title }} - 연세대학교 공지사항</title>
      <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:'Noto Sans KR',-apple-system,sans-serif;background:#f5f7fa;min-height:100vh;padding:20px}
        .container{max-width:900px;margin:0 auto}
        .header{background:linear-gradient(135deg,#003876 0%,#005BBB 100%);color:white;padding:30px;border-radius:20px 20px 0 0;box-shadow:0 5px 20px rgba(0,0,0,0.1)}
        .back-button{display:inline-flex;gap:8px;background:rgba(255,255,255,0.2);padding:10px 20px;border-radius:10px;color:white;text-decoration:none;transition:.3s;margin-bottom:20px}
        .back-button:hover{background:rgba(255,255,255,0.3); transform:translateX(-4px)}
        .college-badge{display:inline-flex;gap:10px;background:rgba(255,255,255,0.2);padding:8px 16px;border-radius:20px;margin-bottom:15px}
        .notice-title{font-size:1.8rem;font-weight:600;margin-bottom:20px;line-height:1.4}
        .notice-meta{display:flex;gap:30px;font-size:.95rem;opacity:.9}
        .content-wrapper{background:white;padding:40px;border-radius:0 0 20px 20px;box-shadow:0 5px 20px rgba(0,0,0,0.1);margin-bottom:30px}
        .notice-content{font-size:1.05rem;line-height:1.8;color:#333;white-space:pre-wrap;word-wrap:break-word}
        .action-buttons{background:white;padding:30px;border-radius:20px;box-shadow:0 5px 20px rgba(0,0,0,0.1);display:flex;gap:15px;justify-content:center;flex-wrap:wrap}
        .btn{padding:12px 30px;border-radius:10px;text-decoration:none;font-weight:500;transition:.3s;display:inline-flex;gap:8px}
        .btn-primary{background:#003876;color:white}.btn-primary:hover{background:#002855; transform:translateY(-2px); box-shadow:0 5px 15px rgba(0,0,0,0.2)}
        .btn-secondary{background:#e0e0e0;color:#333}.btn-secondary:hover{background:#d0d0d0}
        @media (max-width:768px){.header{padding:20px}.notice-title{font-size:1.4rem}.content-wrapper{padding:25px}.notice-meta{flex-direction:column;gap:10px}}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <a href="/dashboard" class="back-button">← 목록으로 돌아가기</a>
          <div class="college-badge"><span style="font-size:1.2rem">{{ college.icon }}</span><span>{{ college.name }}</span></div>
          <h1 class="notice-title">{{ notice.title }}</h1>
          <div class="notice-meta">
            <div>📝 {{ notice.department }}</div>
            <div>📅 {{ notice.date }}</div>
            {% if notice.views %}<div>👁 조회 {{ notice.views }}</div>{% endif %}
          </div>
        </div>
        <div class="content-wrapper">
          <div class="notice-content">{{ notice.formatted_content }}</div>
        </div>
        <div class="action-buttons">
          <a href="{{ notice.url }}" target="_blank" class="btn btn-primary">🔗 원본 페이지 보기</a>
          <a href="/dashboard" class="btn btn-secondary">📋 목록으로</a>
        </div>
      </div>
    </body>
    </html>
    """
    return render_template_string(DETAIL_TEMPLATE, notice=notice, college=college_info)

@app.route('/api/health')
def health_check():
    """시스템 상태 확인"""
    db_connected = False
    colleges_count = 0
    
    try:
        conn = get_db_connection()
        if conn:
            db_connected = True
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM colleges")
                colleges_count = cur.fetchone()[0]
            conn.close()
    except:
        pass
    
    return jsonify({
        'status': 'healthy',
        'message': 'DICE 서버가 정상 작동 중입니다',
        'timestamp': datetime.now().isoformat(),
        'colleges_count': colleges_count,
        'db_connected': db_connected
    })

# 오류 핸들러
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # DB 초기화
    init_db()
    
    # Railway 환경에서는 PORT 환경 변수를 사용
    PORT = int(os.getenv('PORT', 8080))
    
    logger.info("="*60)
    logger.info("🎓 연세대학교 통합 공지사항 시스템 (DICE)")
    logger.info("="*60)
    logger.info(f"🌐 서버 포트: {PORT}")
    logger.info(f"💾 DB 연결: {bool(DATABASE_URL)}")
    logger.info("="*60)
    
    app.run(debug=False, host='0.0.0.0', port=PORT)