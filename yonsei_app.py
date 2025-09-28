# yonsei_app.py
"""
연세대학교 전체 단과대학 공지사항 통합 시스템
- 18개 단과대학 공지사항 통합
- PostgreSQL DB 연동
- 회원가입/로그인 API
- 크롤링 데이터 DB 저장
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

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ===== 환경 변수 설정 =====
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "apify_api_xxxxxxxxxx")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dice_db")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-here")

# ===== DB 연결 함수 =====
def get_db_connection():
    """PostgreSQL 데이터베이스 연결"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"DB 연결 실패: {e}")
        return None

def init_db():
    """데이터베이스 초기화 - 테이블이 없으면 생성"""
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # schema.sql 파일 실행 (존재하는 경우)
                if os.path.exists('schema.sql'):
                    with open('schema.sql', 'r', encoding='utf-8') as f:
                        cur.execute(f.read())
                conn.commit()
                print("DB 초기화 완료")
        except Exception as e:
            print(f"DB 초기화 실패: {e}")
        finally:
            conn.close()

# 단과대학 정보
COLLEGES = {
    'main': {
        'name': '메인 공지사항',
        'icon': '🏫',
        'color': '#003876',
        'task_id': 'VsNDqFr5fLLIi2Xh1',
        'url': 'https://www.yonsei.ac.kr'
    },
    'liberal': {
        'name': '문과대학',
        'icon': '📚',
        'color': '#8B4513',
        'task_id': 'L5AS9TZWUMorttUJJ',
        'url': 'https://liberal.yonsei.ac.kr'
    },
    'business': {
        'name': '상경대학',
        'icon': '📊',
        'color': '#FFB700',
        'task_id': 'yJ8Rp9AhTSVCw7Yt8',
        'url': 'https://soe.yonsei.ac.kr'
    },
    'management': {
        'name': '경영대학',
        'icon': '💼',
        'color': '#1E90FF',
        'task_id': 'DjsOsls6pCpaQaKq9',
        'url': 'https://ysb.yonsei.ac.kr'
    },
    'engineering': {
        'name': '공과대학',
        'icon': '⚙️',
        'color': '#DC143C',
        'task_id': 'tdcYhb8OaDnBHI8jJr',
        'url': 'https://engineering.yonsei.ac.kr'
    },
    'life': {
        'name': '생명시스템대학',
        'icon': '🧬',
        'color': '#228B22',
        'task_id': 'gOKavS1YNKhNUVsNQ',
        'url': 'https://sys.yonsei.ac.kr'
    },
    'ai': {
        'name': '인공지능융합대학',
        'icon': '🤖',
        'color': '#9370DB',
        'task_id': 'qb6M6hbdm2fnhxfeg',
        'url': 'https://ai.yonsei.ac.kr'
    },
    'theology': {
        'name': '신과대학',
        'icon': '✝️',
        'color': '#4B0082',
        'task_id': '9akDlFeStRHdeps4t',
        'url': 'https://theology.yonsei.ac.kr'
    },
    'social': {
        'name': '사회과학대학',
        'icon': '🏛️',
        'color': '#2E8B57',
        'task_id': 'hNSAPYSS35RscOWWm',
        'url': 'https://yeri.yonsei.ac.kr/socsci'
    },
    'music': {
        'name': '음악대학',
        'icon': '🎵',
        'color': '#FF1493',
        'task_id': 'B3xYzP1Jqo1jVH1Me',
        'url': 'https://music.yonsei.ac.kr'
    },
    'human': {
        'name': '생활과학대학',
        'icon': '🏠',
        'color': '#FF6347',
        'task_id': 'K5kXEuXSyZzY5uwpn',
        'url': 'https://che.yonsei.ac.kr'
    },
    'education': {
        'name': '교육과학대학',
        'icon': '🎓',
        'color': '#4169E1',
        'task_id': '9XfmKGnPdDQWZkUjW',
        'url': 'https://educa.yonsei.ac.kr'
    },
    'underwood': {
        'name': '언더우드국제대학',
        'icon': '🌏',
        'color': '#FF8C00',
        'task_id': 'Xz2t1SAdshoLSDslB',
        'url': 'https://uic.yonsei.ac.kr'
    },
    'global': {
        'name': '글로벌인재대학',
        'icon': '🌐',
        'color': '#008B8B',
        'task_id': 'BwiB4aHdY2uyP4txl',
        'url': 'https://global.yonsei.ac.kr'
    },
    'medicine': {
        'name': '의과대학',
        'icon': '⚕️',
        'color': '#B22222',
        'task_id': 'oAgxPnIMOv2IYhZej',
        'url': 'https://medicine.yonsei.ac.kr'
    },
    'dentistry': {
        'name': '치과대학',
        'icon': '🦷',
        'color': '#5F9EA0',
        'task_id': 'etPqNCyaZNI4A8sEl',
        'url': 'https://dentistry.yonsei.ac.kr'
    },
    'nursing': {
        'name': '간호대학',
        'icon': '💊',
        'color': '#DB7093',
        'task_id': 'I04xneYTZMJ8jAn4r',
        'url': 'https://nursing.yonsei.ac.kr'
    },
    'pharmacy': {
        'name': '약학대학',
        'icon': '💉',
        'color': '#663399',
        'task_id': 'gjqRcgjHJr4frQhma',
        'url': 'https://pharmacy.yonsei.ac.kr'
    }
}

def format_content(content):
    if not content:
        return ""
    content = re.sub(r'(?<!\n)(\d+\.)', r'\n\n\1', content)
    content = re.sub(r'(?<!\n)([가-힣]\.)', r'\n\1', content)
    content = re.sub(r'[ \t]+', ' ', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()

def save_notices_to_db(college_key, notices):
    """크롤링한 공지사항을 DB에 저장"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cur:
            for notice in notices:
                # content_hash 생성
                content_hash = hashlib.sha256(
                    f"{notice['title']}{notice['content']}".encode()
                ).hexdigest()
                
                # 날짜 파싱
                published_date = None
                if notice.get('date'):
                    try:
                        published_date = datetime.strptime(notice['date'], '%Y-%m-%d').date()
                    except:
                        pass
                
                # notices 테이블에 저장 (중복 시 업데이트)
                cur.execute("""
                    INSERT INTO notices (
                        college_id, title, content, department, writer,
                        original_id, original_url, published_date,
                        content_hash, view_count
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (college_id, original_id) 
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        department = EXCLUDED.department,
                        writer = EXCLUDED.writer,
                        original_url = EXCLUDED.original_url,
                        published_date = EXCLUDED.published_date,
                        content_hash = EXCLUDED.content_hash,
                        last_checked_at = CURRENT_TIMESTAMP
                """, (
                    college_key, notice['title'], notice.get('content'),
                    notice.get('department'), notice.get('writer'),
                    notice.get('id'), notice.get('url'),
                    published_date, content_hash,
                    int(notice.get('views', '0').replace(',', ''))
                ))
            
            # crawl_logs에 기록
            cur.execute("""
                INSERT INTO crawl_logs (
                    college_id, status, notices_fetched,
                    started_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s)
            """, (
                college_key, 'success', len(notices),
                datetime.now(), datetime.now()
            ))
            
            conn.commit()
            return True
    except Exception as e:
        print(f"DB 저장 실패: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def get_notices_from_db(college_key):
    """DB에서 공지사항 조회"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, content, department as writer,
                       published_date as date, original_url as url,
                       view_count as views
                FROM notices
                WHERE college_id = %s AND status = 'active'
                ORDER BY published_date DESC
                LIMIT 50
            """, (college_key,))
            
            notices = cur.fetchall()
            # 날짜 및 조회수 포맷팅
            for notice in notices:
                if notice['date']:
                    notice['date'] = notice['date'].strftime('%Y-%m-%d')
                notice['views'] = f"{notice['views']:,}"
                notice['id'] = str(notice['id'])
            
            return notices
    except Exception as e:
        print(f"DB 조회 실패: {e}")
        return []
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
        print(f"Error fetching Apify data: {e}")
        return None

# ============= AUTH API =============
@app.route('/api/auth/register', methods=['POST'])
def register():
    """회원가입 API"""
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
        print(f"회원가입 오류: {e}")
        return jsonify({'success': False, 'message': '회원가입 실패'}), 500
    finally:
        conn.close()

@app.route('/api/auth/login', methods=['POST'])
def login():
    """로그인 API"""
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
                WHERE email = %s
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
        print(f"로그인 오류: {e}")
        return jsonify({'success': False, 'message': '로그인 실패'}), 500
    finally:
        conn.close()

# ============= 기존 라우트 =============
@app.route('/landing')
def serve_landing():
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

MAIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>연세대학교 통합 공지사항 시스템</title>
    <style>
      * { margin:0; padding:0; box-sizing:border-box; }
      body { font-family: 'Noto Sans KR', -apple-system, sans-serif; background: linear-gradient(135deg, #003876 0%, #005BBB 100%); min-height: 100vh; padding: 20px; }
      .container { max-width: 1200px; margin:0 auto; }
      .header { text-align:center; color:white; margin-bottom:40px; animation: fadeIn 1s ease-out; }
      .header h1 { font-size:2.5rem; margin-bottom:20px; text-shadow:2px 2px 4px rgba(0,0,0,0.2); }
      .logo { font-size:4rem; margin-bottom:20px; }
      .college-selector { background:white; padding:30px; border-radius:20px; box-shadow:0 10px 30px rgba(0,0,0,0.2); margin-bottom:30px; }
      .selector-label { font-size:1.2rem; font-weight:600; margin-bottom:15px; color:#333; }
      .select-wrapper { position:relative; }
      select { width:100%; padding:15px 20px; font-size:1.1rem; border:2px solid #e0e0e0; border-radius:10px; background:white; cursor:pointer; appearance:none; transition:all 0.3s ease; }
      select:focus { outline:none; border-color:#003876; box-shadow:0 0 0 3px rgba(0,56,118,0.1); }
      .select-wrapper::after { content:'▼'; position:absolute; right:20px; top:50%; transform:translateY(-50%); pointer-events:none; color:#666; }
      .notice-container { display:none; animation: slideUp 0.5s ease-out; }
      .notice-container.active { display:block; }
      .college-header { background:white; padding:25px; border-radius:20px; box-shadow:0 5px 20px rgba(0,0,0,0.1); margin-bottom:20px; display:flex; align-items:center; gap:20px; }
      .college-icon { width:60px; height:60px; border-radius:15px; display:flex; align-items:center; justify-content:center; font-size:2rem; color:white; }
      .college-info h2 { font-size:1.8rem; color:#333; margin-bottom:5px; }
      .college-info p { color:#666; font-size:0.95rem; }
      .notices-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap:20px; margin-bottom:30px; }
      .notice-card { background:white; padding:25px; border-radius:15px; box-shadow:0 5px 15px rgba(0,0,0,0.1); cursor:pointer; transition:all 0.3s ease; border-left:4px solid; }
      .notice-card:hover { transform: translateY(-5px); box-shadow:0 10px 25px rgba(0,0,0,0.15); }
      .notice-title { font-size:1.1rem; font-weight:600; color:#333; margin-bottom:15px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
      .notice-meta { display:flex; justify-content:space-between; align-items:center; color:#666; font-size:0.9rem; }
      .loading { text-align:center; padding:60px; color:white; }
      .spinner { border:3px solid rgba(255,255,255,0.3); border-radius:50%; border-top:3px solid white; width:50px; height:50px; animation: spin 1s linear infinite; margin:0 auto 20px; }
      .empty-state { background:white; padding:60px; border-radius:20px; text-align:center; color:#666; }
      .empty-state .icon { font-size:4rem; margin-bottom:20px; opacity:0.3; }
      @keyframes fadeIn { from { opacity:0; transform:translateY(-20px);} to { opacity:1; transform:translateY(0);} }
      @keyframes slideUp { from { opacity:0; transform:translateY(30px);} to { opacity:1; transform:translateY(0);} }
      @keyframes spin { 0% { transform: rotate(0deg);} 100% { transform: rotate(360deg);} }
      @media (max-width:768px) {
        .header h1 { font-size:1.8rem; }
        .notices-grid { grid-template-columns: 1fr; }
        .college-header { flex-direction:column; text-align:center; }
      }
    </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">🎓</div>
      <h1>연세대학교 통합 공지사항 시스템</h1>
      <p style="opacity:0.9;">모든 단과대학 공지사항을 한 곳에서</p>
    </div>

    <div class="college-selector">
      <div class="selector-label">📌 단과대학 선택</div>
      <div class="select-wrapper">
        <select id="collegeSelect" onchange="loadCollegeNotices()">
          <option value="">단과대학을 선택하세요</option>
          {% for key, college in colleges.items() %}
          <option value="{{ key }}">{{ college.icon }} {{ college.name }}</option>
          {% endfor %}
        </select>
      </div>
    </div>

    <div id="noticeContainer" class="notice-container"></div>
    <div id="loadingContainer" style="display:none;">
      <div class="loading">
        <div class="spinner"></div>
        <p>공지사항을 불러오는 중입니다...</p>
      </div>
    </div>
  </div>

  <script>
    async function loadCollegeNotices() {
      const select = document.getElementById('collegeSelect');
      const container = document.getElementById('noticeContainer');
      const loading = document.getElementById('loadingContainer');
      const collegeKey = select.value;

      if (!collegeKey) { container.classList.remove('active'); return; }
      container.classList.remove('active'); loading.style.display = 'block';

      try {
        const response = await fetch(`/api/notices/${collegeKey}`);
        const data = await response.json();
        if (data.success) renderNotices(data);
      } catch (e) {
        console.error(e);
        container.innerHTML = '<div class="empty-state"><div class="icon">❌</div><p>공지사항을 불러오는데 실패했습니다.</p></div>';
      } finally { loading.style.display = 'none'; container.classList.add('active'); }
    }

    function renderNotices(data) {
      const container = document.getElementById('noticeContainer');
      const college = data.college;
      let html = `
        <div class="college-header">
          <div class="college-icon" style="background:${college.color};">${college.icon}</div>
          <div class="college-info">
            <h2>${college.name}</h2>
            <p>최신 공지사항 ${data.notices.length}개</p>
          </div>
        </div>
      `;
      if (data.notices && data.notices.length>0) {
        html += '<div class="notices-grid">';
        data.notices.forEach(n=>{
          html += `
            <div class="notice-card" style="border-left-color:${college.color};" onclick="viewNotice('${n.id}')">
              <div class="notice-title">${n.title}</div>
              <div class="notice-meta">
                <div>📅 ${n.date||''}</div>
                <div>👁 ${n.views||'0'}</div>
              </div>
            </div>`;
        });
        html += '</div>';
      } else {
        html += `<div class="empty-state"><div class="icon">📭</div><p>현재 공지사항이 없습니다.</p></div>`;
      }
      container.innerHTML = html;
    }

    function viewNotice(noticeId) {
      const collegeKey = document.getElementById('collegeSelect').value;
      window.location.href = `/notice/${collegeKey}/${noticeId}`;
    }
  </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(MAIN_TEMPLATE, colleges=COLLEGES)

@app.route('/api/notices/<college_key>')
def get_notices(college_key):
    if college_key not in COLLEGES:
        return jsonify({'success': False, 'message': 'Invalid college'})
    
    college = COLLEGES[college_key]
    notices = []
    
    # 먼저 DB에서 조회
    notices = get_notices_from_db(college_key)
    
    # DB에 데이터가 없으면 Apify에서 가져오기
    if not notices and APIFY_TOKEN != 'apify_api_xxxxxxxxxx':
        apify_data = get_apify_data(college['task_id'])
        if apify_data:
            # DB에 저장
            save_notices_to_db(college_key, apify_data)
            notices = apify_data
    
    return jsonify({
        'success': True,
        'college': {
            'key': college_key,
            'name': college['name'],
            'icon': college['icon'],
            'color': college['color']
        },
        'notices': notices
    })

@app.route('/notice/<college_key>/<notice_id>')
def notice_detail(college_key, notice_id):
    if college_key not in COLLEGES:
        return "잘못된 접근입니다.", 404
    
    college = COLLEGES[college_key]
    notice = None
    
    # DB에서 상세 정보 조회
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM notices
                    WHERE id = %s OR original_id = %s
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
                        'department': notice_data['department'] or college['name'],
                        'views': f"{notice_data['view_count']:,}"
                    }
                    
                    # 조회수 증가
                    cur.execute("""
                        UPDATE notices SET view_count = view_count + 1
                        WHERE id = %s
                    """, (notice_data['id'],))
                    conn.commit()
        except Exception as e:
            print(f"상세 조회 오류: {e}")
        finally:
            conn.close()
    
    if not notice:
        return "공지사항을 찾을 수 없습니다.", 404
    
    notice['formatted_content'] = format_content(notice['content'])
    
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
          <a href="/notices" class="back-button">← 목록으로 돌아가기</a>
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
          <a href="/notices" class="btn btn-secondary">📋 목록으로</a>
        </div>
      </div>
    </body>
    </html>
    """
    return render_template_string(DETAIL_TEMPLATE, notice=notice, college=college)

@app.route('/api/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'message': 'DICE 서버가 정상 작동 중입니다',
        'timestamp': datetime.now().isoformat(),
        'colleges_count': len(COLLEGES),
        'db_connected': get_db_connection() is not None
    })

if __name__ == '__main__':
    # DB 초기화
    init_db()
    
    PORT = 8080
    print("="*60)
    print("🎓 연세대학교 통합 공지사항 시스템")
    print("="*60)
    print(f"\n📚 지원 단과대학: {len(COLLEGES)}개")
    print(f"\n🌐 서버 주소: http://localhost:{PORT}")
    print("\n📋 현재 모드: ", end="")
    print("실제 데이터 모드" if APIFY_TOKEN != 'apify_api_xxxxxxxxxx' else "테스트 모드")
    print("\n"+"="*60)
    print("서버 시작중... (종료: Ctrl+C)")
    print("="*60+"\n")
    app.run(debug=True, host='0.0.0.0', port=PORT)