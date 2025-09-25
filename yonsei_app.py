# yonsei_app.py
"""
연세대학교 전체 단과대학 공지사항 통합 시스템
- 18개 단과대학 공지사항 통합
- 선택형 인터페이스
- 상세보기 페이지
- 본문 자동 포맷팅
"""

from flask import Flask, render_template_string, jsonify, send_from_directory
from datetime import datetime
import re
import requests ,os
from flask_cors import CORS 
from dotenv import load_dotenv

load_dotenv()  # .env 파일에서 환경 변수 로드

# 정적 파일(index.html, auth.html, dashboard.html)을 같은 서버에서 서빙
# 파일들이 yonsei_app.py와 같은 폴더에 있다고 가정
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ===== Apify 설정 (수정 필요 시 바꾸세요) =====
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "apify_api_xxxxxxxxxx")

# 단과대학 정보 및 Task IDs
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

# 테스트용 더미 데이터
def generate_dummy_data(college_key):
    college = COLLEGES[college_key]
    base_notices = [
        {
            'id': f'{college_key}_1',
            'title': f'{college["name"]} 2025학년도 1학기 수강신청 안내',
            'content': f'''2025학년도 1학기 수강신청 안내드립니다.

1. 수강신청 일정
가. 수강신청 기간: 2025년 2월 15일(월) ~ 2월 19일(금)
나. 수강정정 기간: 2025년 3월 2일(월) ~ 3월 6일(금)

2. 수강신청 방법
가. 연세포털시스템(portal.yonsei.ac.kr) 접속
나. 수강신청 메뉴 클릭
다. 원하는 과목 검색 및 신청

3. 유의사항
가. 수강신청 가능 학점: 최소 12학점 ~ 최대 18학점
나. 필수과목 우선 신청 권장
다. 수강신청 인원 초과시 전공자 우선

문의사항은 {college["name"]} 행정팀으로 연락 바랍니다.
전화: 02-2123-XXXX
이메일: admin@yonsei.ac.kr''',
            'date': '2025-01-15',
            'url': college['url'],
            'department': f'{college["name"]} 행정팀',
            'views': '1,234'
        },
        {
            'id': f'{college_key}_2',
            'title': f'{college["name"]} 학술 세미나 개최 안내',
            'content': f'''제15회 {college["name"]} 학술 세미나를 다음과 같이 개최합니다.

1. 행사 개요
가. 일시: 2025년 2월 10일(수) 14:00~17:00
나. 장소: {college["name"]} 대강당
다. 주제: 미래 사회와 {college["name"]}의 역할

2. 프로그램
가. 14:00~14:30 : 등록 및 개회식
나. 14:30~15:30 : 기조강연
다. 15:30~16:30 : 학생 연구발표
라. 16:30~17:00 : 질의응답 및 폐회

3. 참가 신청
가. 신청기간: 2025년 1월 20일 ~ 2월 5일
나. 신청방법: 온라인 사전등록 (선착순 200명)
다. 참가비: 무료

많은 관심과 참여 부탁드립니다.''',
            'date': '2025-01-14',
            'url': college['url'],
            'department': f'{college["name"]} 학술위원회',
            'views': '856'
        },
        {
            'id': f'{college_key}_3',
            'title': f'{college["name"]} 겨울 계절학기 성적 확인',
            'content': f'''2024년도 겨울 계절학기 성적이 공개되었습니다.

1. 성적 확인 방법
가. 연세포털시스템 로그인
나. 성적조회 메뉴 선택
다. 2024년 겨울계절학기 선택

2. 성적 이의신청
가. 신청기간: 2025년 1월 18일 ~ 1월 22일
나. 신청방법: 포털시스템 내 이의신청 메뉴
다. 처리기간: 신청 후 7일 이내

3. 주의사항
가. 이의신청은 기간 내에만 가능
나. 단순 점수 확인 요청은 불가
다. 구체적인 사유 작성 필요

문의: {college["name"]} 교무팀''',
            'date': '2025-01-13',
            'url': college['url'],
            'department': f'{college["name"]} 교무팀',
            'views': '2,341'
        }
    ]
    return base_notices

def format_content(content):
    if not content:
        return ""
    content = re.sub(r'(?<!\n)(\d+\.)', r'\n\n\1', content)
    content = re.sub(r'(?<!\n)([가-힣]\.)', r'\n\1', content)
    content = re.sub(r'[ \t]+', ' ', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()

# ---------------- UI 라우트 (정적 파일) ----------------
@app.route('/landing')
def serve_landing():
    # index.html 정적 파일
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/auth')
def serve_auth():
    return send_from_directory(app.static_folder, 'auth.html')

@app.route('/dashboard')
def serve_dashboard():
    return send_from_directory(app.static_folder, 'dashboard.html')

# -------------- 기존 메인(크롤링 UI) --------------
MAIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>연세대학교 통합 공지사항 시스템</title>
    <style>
      /* (생략 없이 원문 유지) */
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
    # 기존 메인(선택형 크롤링 UI)
    return render_template_string(MAIN_TEMPLATE, colleges=COLLEGES)

def get_apify_data(task_id):
    """Apify Task의 최신 실행 결과 가져오기 (title 필드 유연 처리)"""
    try:
        url = f"https://api.apify.com/v2/actor-tasks/{task_id}/runs"
        headers = {"Authorization": f"Bearer {APIFY_TOKEN}"}
        params = {"limit": 1, "desc": "true"}
        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code != 200:
            return None

        runs = response.json().get('data', {}).get('items', [])
        if not runs:
            # 최근 실행이 없으면 None
            return None

        latest_run = runs[0]
        dataset_id = latest_run.get('defaultDatasetId')
        if not dataset_id:
            return None

        dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        dataset_response = requests.get(dataset_url, headers=headers, timeout=10)
        if dataset_response.status_code != 200:
            return None

        # Apify는 보통 JSON 배열을 바로 반환
        items = dataset_response.json()
        valid_items = []

        for idx, item in enumerate(items[:50]):  # 넉넉히 50개까지
            if not item:
                continue

            # ---- 유연한 필드 매핑 ----
            title = (
                item.get('title')
                or item.get('name')
                or item.get('headline')
                or item.get('subject')
                or item.get('heading')
            )
            content = (
                item.get('content')
                or item.get('body')
                or item.get('text')
                or ''
            )
            date = (
                item.get('date')
                or item.get('publishedAt')
                or item.get('time')
                or item.get('createdAt')
                or ''
            )
            url_field = item.get('url') or item.get('link') or item.get('sourceUrl') or ''
            dept = item.get('department') or item.get('writer') or item.get('author') or ''
            views = item.get('views') or item.get('viewCount') or item.get('hits') or '0'

            # 제목 완전 없으면 본문/URL로 대체 생성
            if not title:
                if content:
                    # 본문 앞 30~60자 정도로 제목 생성
                    snippet = re.sub(r'\s+', ' ', content).strip()
                    title = (snippet[:50] + '…') if len(snippet) > 50 else snippet
                elif url_field:
                    # URL에서 경로 마지막 세그먼트를 제목 대용
                    title = url_field.rstrip('/').split('/')[-1][:60] or '제목 없음'
                else:
                    # 정말 아무 것도 없으면 스킵
                    continue

            # 날짜가 이상하면 비워두고, 프론트에서 정렬 시 빈값은 뒤로 밀림
            if date:
                try:
                    # ISO / 일반 한국식 섞여와도 최대한 파싱 시도
                    _ = datetime.fromisoformat(date.replace('Z', '+00:00'))
                except Exception:
                    # 간단한 yyyy-mm-dd 매칭만 체크
                    if not re.match(r'^\d{4}-\d{2}-\d{2}', str(date)):
                        # 형식 안 맞으면 비우기
                        date = ''

            valid_items.append({
                'id': f'{task_id}_{idx}',
                'title': title,
                'content': content,
                'date': str(date) if date else '',
                'url': url_field,
                'department': dept,
                'views': str(views),
            })

        return valid_items or None

    except Exception as e:
        print(f"Error fetching Apify data: {e}")
        return None


@app.route('/api/notices/<college_key>')
def get_notices(college_key):
    if college_key not in COLLEGES:
        return jsonify({'success': False, 'message': 'Invalid college'})
    college = COLLEGES[college_key]
    notices = []

    if APIFY_TOKEN != 'apify_api_xxxxxxxxxx' and not college['task_id'].startswith('task_id_'):
        apify_data = get_apify_data(college['task_id'])
        if apify_data:
            notices = apify_data
        else:
            notices = generate_dummy_data(college_key)
    else:
        notices = generate_dummy_data(college_key)

    return jsonify({
        'success': True,
        'college': { 'key': college_key, 'name': college['name'], 'icon': college['icon'], 'color': college['color'] },
        'notices': notices
    })

@app.route('/notice/<college_key>/<notice_id>')
def notice_detail(college_key, notice_id):
    if college_key not in COLLEGES:
        return "잘못된 접근입니다.", 404
    college = COLLEGES[college_key]
    notice = None

    if APIFY_TOKEN != 'apify_api_xxxxxxxxxx' and not college['task_id'].startswith('task_id_'):
        apify_data = get_apify_data(college['task_id'])
        if apify_data:
            for n in apify_data:
                if n['id'] == notice_id:
                    notice = n
                    break

    if not notice:
        notices = generate_dummy_data(college_key)
        for n in notices:
            if n['id'] == notice_id:
                notice = n
                break

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
          <a href="/" class="back-button">← 목록으로 돌아가기</a>
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
          <a href="/" class="btn btn-secondary">🏠 메인으로</a>
        </div>
      </div>
    </body>
    </html>
    """
    return render_template_string(DETAIL_TEMPLATE, notice=notice, college=college)

# 건강/서버 정보
@app.route('/api/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'message': 'DICE 크롤링 서버가 정상 작동 중입니다',
        'timestamp': datetime.now().isoformat(),
        'colleges_count': len(COLLEGES),
        'apify_configured': APIFY_TOKEN != 'apify_api_xxxxxxxxxx'
    })

@app.route('/api/server-info')
def server_info():
    configured_colleges = sum(1 for c in COLLEGES.values() if not c['task_id'].startswith('task_id_'))
    return jsonify({
        'total_colleges': len(COLLEGES),
        'configured_colleges': configured_colleges,
        'apify_token_set': APIFY_TOKEN != 'apify_api_xxxxxxxxxx',
        'server_mode': 'production' if APIFY_TOKEN != 'apify_api_xxxxxxxxxx' else 'test'
    })

# 디버그
@app.route('/api/debug/<college_key>')
def debug_college(college_key):
    if college_key not in COLLEGES:
        return jsonify({'success': False, 'message': 'Invalid college'})
    college = COLLEGES[college_key]
    debug_info = {
        'college_name': college['name'],
        'task_id': college['task_id'],
        'token_configured': APIFY_TOKEN != 'apify_api_xxxxxxxxxx',
        'task_id_configured': not college['task_id'].startswith('task_id_'),
        'data_source': None,
        'error': None,
        'api_response': None,
        'dataset_items': 0
    }
    if debug_info['token_configured'] and debug_info['task_id_configured']:
        try:
            url = f"https://api.apify.com/v2/actor-tasks/{college['task_id']}/runs"
            headers = {"Authorization": f"Bearer {APIFY_TOKEN}"}
            params = {"limit": 1, "desc": "true"}
            response = requests.get(url, headers=headers, params=params, timeout=10)
            debug_info['api_response'] = {'status_code': response.status_code, 'url': url}
            if response.status_code == 200:
                runs = response.json().get('data', {}).get('items', [])
                if runs:
                    latest_run = runs[0]
                    debug_info['latest_run'] = {
                        'id': latest_run.get('id'),
                        'status': latest_run.get('status'),
                        'finishedAt': latest_run.get('finishedAt'),
                        'datasetId': latest_run.get('defaultDatasetId')
                    }
                    dataset_id = latest_run.get('defaultDatasetId')
                    if dataset_id:
                        dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
                        dataset_response = requests.get(dataset_url, headers=headers, timeout=10)
                        if dataset_response.status_code == 200:
                            items = dataset_response.json()
                            debug_info['dataset_items'] = len(items)
                            debug_info['data_source'] = 'Apify'
                            if items:
                                debug_info['sample_item'] = {
                                    'title': items[0].get('title','N/A'),
                                    'has_content': bool(items[0].get('content')),
                                    'has_date': bool(items[0].get('date')),
                                    'has_url': bool(items[0].get('url'))
                                }
                        else:
                            debug_info['error'] = f'Dataset API error: {dataset_response.status_code}'
                    else:
                        debug_info['error'] = 'No dataset ID found'
                else:
                    debug_info['error'] = 'No runs found for this task'
            else:
                debug_info['error'] = f'Task API error: {response.status_code}'
        except Exception as e:
            debug_info['error'] = str(e)
            debug_info['data_source'] = 'Error'
    else:
        debug_info['data_source'] = 'Dummy'
        debug_info['error'] = 'Token or Task ID not configured'
    return jsonify(debug_info)

@app.route('/debug')
def debug_page():
    # (원문 디버그 페이지 템플릿 그대로 유지)
    debug_html = """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Apify 디버그 - 연세대 공지사항</title>
    <style>
      body{font-family:'Courier New',monospace;background:#1e1e1e;color:#d4d4d4;padding:20px;margin:0}
      .container{max-width:1200px;margin:0 auto}
      h1{color:#4fc3f7;border-bottom:2px solid #4fc3f7;padding-bottom:10px}
      .college-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px;margin-top:30px}
      .college-card{background:#2d2d30;border:1px solid #3e3e42;border-radius:8px;padding:15px;cursor:pointer;transition:.3s}
      .college-card:hover{border-color:#4fc3f7;background:#353537}
      .college-name{font-size:1.2rem;color:#4fc3f7;margin-bottom:10px}
      .status{padding:5px 10px;border-radius:4px;display:inline-block;font-size:.9rem;margin-top:5px}
      .status.configured{background:#1b5e20;color:#81c784}
      .status.not-configured{background:#b71c1c;color:#ef5350}
      .debug-details{background:#1e1e1e;border:1px solid #3e3e42;border-radius:8px;padding:20px;margin-top:30px;display:none}
      .debug-details.active{display:block}
      pre{background:#2d2d30;padding:15px;border-radius:4px;overflow-x:auto;color:#d4d4d4}
      .close-btn{background:#4fc3f7;color:#1e1e1e;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;float:right}
      .refresh-btn{background:#81c784;color:#1e1e1e;border:none;padding:10px 20px;border-radius:4px;cursor:pointer;margin-top:20px}
      .summary{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:20px}
      .summary-card{background:#2d2d30;padding:20px;border-radius:8px;text-align:center}
      .summary-number{font-size:2rem;color:#4fc3f7;font-weight:bold}
      .summary-label{color:#9e9e9e;margin-top:5px}
    </style></head>
    <body><div class="container"><h1>🔧 Apify 디버그 콘솔</h1>
    <div class="summary">
      <div class="summary-card"><div class="summary-number" id="total-colleges">18</div><div class="summary-label">전체 단과대학</div></div>
      <div class="summary-card"><div class="summary-number" id="configured-colleges">0</div><div class="summary-label">설정 완료</div></div>
      <div class="summary-card"><div class="summary-number" id="working-colleges">0</div><div class="summary-label">정상 작동</div></div>
    </div>
    <button class="refresh-btn" onclick="checkAllColleges()">전체 상태 확인</button>
    <div class="college-grid" id="collegeGrid"></div>
    <div class="debug-details" id="debugDetails"><button class="close-btn" onclick="closeDetails()">✕ 닫기</button>
      <h2 id="detailsTitle"></h2><pre id="detailsContent"></pre></div></div>
    <script>
      const colleges = {{ colleges | tojson }};
      function initializeGrid(){
        const grid=document.getElementById('collegeGrid'); let html='';
        for(const [key,college] of Object.entries(colleges)){
          html+=\`
          <div class="college-card" onclick="checkCollege('\${key}')" id="card-\${key}">
            <div class="college-name">\${college.icon} \${college.name}</div>
            <div>Task ID: <code>\${college.task_id.substring(0,20)}...</code></div>
            <div class="status not-configured" id="status-\${key}">확인 필요</div>
          </div>\`;
        }
        grid.innerHTML=html;
      }
      async function checkCollege(collegeKey){
        const status=document.getElementById(\`status-\${collegeKey}\`);
        status.textContent='확인 중...'; status.className='status';
        try{
          const res=await fetch(\`/api/debug/\${collegeKey}\`);
          const data=await res.json();
          if(data.data_source==='Apify' && data.dataset_items>0){
            status.textContent=\`✓ 정상 (\${data.dataset_items}개)\`; status.className='status configured';
          }else if(data.data_source==='Dummy'){
            status.textContent='✗ 미설정'; status.className='status not-configured';
          }else{
            status.textContent=\`⚠ 오류: \${data.error}\`; status.className='status not-configured';
          }
          showDetails(colleges[collegeKey].name,data);
        }catch(e){
          status.textContent='✗ 연결 실패'; status.className='status not-configured';
        }
      }
      async function checkAllColleges(){
        let configured=0, working=0;
        for(const key of Object.keys(colleges)){
          const res=await fetch(\`/api/debug/\${key}\`);
          const data=await res.json();
          if(data.task_id_configured) configured++;
          if(data.data_source==='Apify' && data.dataset_items>0) working++;
          const status=document.getElementById(\`status-\${key}\`);
          if(data.data_source==='Apify' && data.dataset_items>0){
            status.textContent=\`✓ 정상 (\${data.dataset_items}개)\`; status.className='status configured';
          }else if(data.data_source==='Dummy'){
            status.textContent='✗ 미설정'; status.className='status not-configured';
          }else{
            status.textContent='⚠ 오류'; status.className='status not-configured';
          }
        }
        document.getElementById('configured-colleges').textContent=configured;
        document.getElementById('working-colleges').textContent=working;
      }
      function showDetails(name,data){
        const d=document.getElementById('debugDetails');
        document.getElementById('detailsTitle').textContent=name+' 상세 정보';
        document.getElementById('detailsContent').textContent=JSON.stringify(data,null,2);
        d.classList.add('active');
      }
      function closeDetails(){ document.getElementById('debugDetails').classList.remove('active'); }
      initializeGrid();
    </script></body></html>"""
    return render_template_string(debug_html, colleges=COLLEGES)

if __name__ == '__main__':
    PORT = 8080
    print("="*60)
    print("🎓 연세대학교 통합 공지사항 시스템")
    print("="*60)
    print(f"\n📚 지원 단과대학: {len(COLLEGES)}개")
    for key, college in COLLEGES.items():   
        print(f"   {college['icon']} {college['name']}")
    print(f"\n🌐 서버 주소: http://localhost:{PORT}")
    print("\n📋 현재 모드: ", end="")
    print("실제 데이터 모드" if APIFY_TOKEN != 'apify_api_xxxxxxxxxx' else "테스트 모드 (더미 데이터)")
    print("\n"+"="*60)
    print("서버 시작중... (종료: Ctrl+C)")
    print("="*60+"\n")
    app.run(debug=True, host='0.0.0.0', port=PORT)
