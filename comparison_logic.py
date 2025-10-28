# comparison_logic.py

import re
from typing import Dict, Any, List, Tuple

# --- 1. 정규화 및 매핑 데이터 (Ontology) ---
# --- 1. 정규화 및 매핑 데이터 (Ontology) ---

# 학과 매핑: '사용자 프로필 학과': ['공지사항에서 허용되는 동의어/상위그룹']
DEPARTMENT_MAP = {
    # 기존 학과 매핑 유지
    '경영학과': ['경영대학', '상경대학', '상경‧경영대학', '경영/경제 계열'],
    '경제학부': ['경제대학', '상경대학', '상경‧경영대학', '경영/경제 계열'],
    '응용통계학과': ['상경대학', '상경‧경영대학', '통계데이터사이언스학과', '통계학과'], # CSV 데이터 반영
    '컴퓨터과학과': ['공과대학', '첨단컴퓨팅학부', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열', '인공지능융합대학'], # CSV 데이터 반영
    '인공지능학과': ['인공지능융합대학', '첨단컴퓨팅학부', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열'],
    '전기전자공학부': ['공과대학', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열'],
    '생명공학과': ['생명시스템대학', '바이오 분야', '이공 계열', '자연계'], # CSV 데이터 반영
    '화학과': ['이과대학', '바이오 분야', '이공 계열', '자연계'], # CSV 데이터 반영
    '문헌정보학과': ['문과대학', '인문사회계열'], # CSV 데이터 반영
    '국어국문학과': ['문과대학', '인문사회계열'], # CSV 데이터 반영
    '의예과': ['의과대학', '의학계열'],
    '의학과': ['의과대학', '의학계열'], # CSV 데이터 반영
    '치과대학': ['치과대학', '치의예과', '치의학과', '의학계열'], # CSV 데이터 반영
    '간호학과': ['간호대학', '의학계열'],
    '약학과': ['약학대학'], # CSV 데이터 반영
    '신학과': ['신과대학'], # CSV 데이터 반영
    '사회학과': ['사회과학대학', '인문사회계열'], # CSV 데이터 반영
    '행정학과': ['사회과학대학', '인문사회계열'], # CSV 데이터 반영
    '정치외교학과': ['사회과학대학', '인문사회계열'], # CSV 데이터 반영
    '언론홍보영상학부': ['사회과학대학', '인문사회계열'], # CSV 데이터 반영
    '사회복지학과': ['사회과학대학', '인문사회계열'], # CSV 데이터 반영
    '문화인류학과': ['사회과학대학', '인문사회계열'], # CSV 데이터 반영
    '음악대학': ['음악대학', '교회음악과', '성악과', '피아노과', '관현악과', '작곡과', '예체능계열'], # CSV 데이터 반영
    '생활과학대학': ['생활과학대학', '의류환경학과', '식품영양학과', '실내건축학과', '아동가족학과', '생활디자인학과', '인문사회계열', '자연계열'], # CSV 데이터 반영 (통합디자인은 생활디자인으로 간주)
    '교육과학대학': ['교육과학대학', '교육학부', '체육교육학과', '스포츠응용산업학과', '인문사회계열', '예체능계열'], # CSV 데이터 반영
    '언더우드국제대학': ['언더우드국제대학', 'UIC'], # CSV 데이터 반영
    '글로벌인재대학': ['글로벌인재대학', 'GLC'], # CSV 데이터 반영
    # 기타 학과 및 계열 매핑 추가 (CSV에서 발견된 표현 기반)
    '시스템반도체공학과': ['공과대학', 'IT 계열', '이공 계열'],
    '디스플레이융합공학과': ['공과대학', 'IT 계열', '이공 계열'],
    '이과대학': ['이과대학', '수학과', '물리학과', '화학과', '지구시스템과학과', '천문우주학과', '대기과학과', '자연계열', '이공 계열'],
    '공과대학': ['공과대학', '화공생명공학부', '전기전자공학부', '건축공학과', '도시공학과', '토목환경공학과', '기계공학부', '신소재공학부', '산업공학과', '컴퓨터과학과', '시스템반도체공학과', '디스플레이융합공학과', 'IT 계열', '이공 계열', '자연계열'],
    '생명시스템대학': ['생명시스템대학', '시스템생물학과', '생화학과', '생명공학과', '자연계열', '이공 계열'],
    '인공지능융합대학': ['인공지능융합대학', '컴퓨터과학과', '인공지능학과', '데이터사이언스융합전공', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열'],
    '자연계': ['자연계', '자연계 대학원', '이과대학', '공과대학', '생명시스템대학', '인공지능융합대학', '약학대학', '의과대학', '치과대학', '간호대학'], # CSV 데이터 반영
    '인문사회계열': ['인문사회계열', '문과대학', '상경대학', '경영대학', '신과대학', '사회과학대학', '생활과학대학', '교육과학대학'], # CSV 데이터 반영
    '예체능계열': ['예체능계열', '음악대학', '체육교육학과', '스포츠응용산업학과'], # CSV 데이터 반영
}

# 학년/학기 정규화: '사용자 프로필 텍스트': (레벨, 학기)
# (1학년 1학기=1, 1학년 2학기=2, ... 4학년 2학기=8, 대학원 1학기=9...)
GRADE_TO_SEMESTER_MAP = {
    '학부 1학년': ('학부', 1), # 1학기 재학 중
    '학부 2학년': ('학부', 3), # 2학년 1학기 재학 중 (CSV: '2~4학년' 표현 고려)
    '학부 3학년': ('학부', 5), # 3학년 1학기 재학 중
    '학부 4학년': ('학부', 7), # 4학년 1학기 재학 중 (CSV: '졸업 예정인 학부생' 고려)
    '학부 재학생': ('학부', 1), # 최소 학기로 가정 (CSV: '학부 재학생' 표현)
    '학부 졸업예정자': ('학부', 7), # 최소 7학기로 가정
    '학부 졸업생': ('학부', 9), # 졸업 상태 (학기 9로 임의 지정)
    '대학원생': ('대학원', 9), # 대학원 1학기로 가정 (CSV: '대학원생')
    '대학원 재학생': ('대학원', 9), # 대학원 1학기로 가정 (CSV: '대학원 재학생')
    '대학원 석사과정': ('대학원', 9), # 석사 1학기로 가정
    '대학원 박사과정': ('대학원', 13), # 박사 1학기 (석사 4학기 이후)로 가정 (CSV: '박사과정 재학생')
    '대학원 석박사통합과정': ('대학원', 9), # 통합 1학기로 가정 (CSV: '석·박사통합과정')
    '대학원 1학기': ('대학원', 9),
    '대학원 2학기': ('대학원', 10),
    '대학원 3학기': ('대학원', 11),
    '대학원 4학기': ('대학원', 12), # 석사 수료 시점
    '대학원 5학기 이상': ('대학원', 13), # 박사 과정 시작 또는 통합과정 (CSV: '통합과정 5학기 이상')
    '대학원 졸업생': ('대학원', 17), # 졸업 상태 (학기 17로 임의 지정)
    # 추가적인 학기 표현 매핑 가능
}

# 성별 매핑: '사용자 프로필 값': ['공지사항에서 허용되는 동의어/표현']
GENDER_MAP = {
    '여성': ['여성', '여학생', '여자', 'female', 'woman'],
    '남성': ['남성', '남학생', '남자', 'male', 'man'],
    # '무관' 키 추가: 성별 제한 없는 경우
    '무관': ['성별무관', '성별 무관', '남녀무관', '남녀 무관', '성별 제한 없음'],
}

# 병역 매핑: '사용자 프로필 값': ['공지사항에서 허용되는 동의어/표현']
MILITARY_SERVICE_MAP = {
    '군필': ['군필', '병역필', '전역', 'served', 'completed military service', '군필자'],
    '미필': ['미필', '병역미필', 'unserved', 'not completed military service', '보충역'], # 보충역은 미필 상태로 간주
    '면제': ['면제', '병역면제', 'exempt', 'exemption', '군 면제'],
    '해당없음': ['여성', 'female', '병역 해당 없음'], # 병역 의무 없는 경우
    '전문연구요원': ['전문연구요원', '전문연구요원 복무', 'specialized research personnel'], # CSV 데이터 반영
    # '무관' 키 추가: 병역 제한 없는 경우
    '무관': ['병역무관', '병역 무관', '병역 관계 없음', '병역 제한 없음'],
}


# --- 2. 헬퍼 함수 (비교 로직 업그레이드) ---

def _normalize_user_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """사용자 프로필(UserProfileRequest 스키마 기반)을 비교 가능한 표준 형태로 정규화합니다."""
    norm = profile.copy()

    # 학년/학기 정규화 (profile 딕셔너리에 'grade' 키가 있다고 가정)
    grade = profile.get('grade') # UserProfileRequest 모델에 따라 'grade' 사용
    level, semester = ('N/A', 0)
    if isinstance(grade, int):
        if 1 <= grade <= 4:
            level = '학부'
            # 학년만 있으므로, 해당 학년의 첫 학기(예: 3학년 -> 5학기)로 가정
            semester = (grade - 1) * 2 + 1
        elif grade > 4: # 예시: 대학원생을 5, 6 등으로 표현한 경우
             level = '대학원'
             # 대학원 학기 계산 로직은 단순화됨, 필요시 구체화
             semester = grade - 4 # 예: 5학년->1학기, 6학년->2학기

    # GRADE_TO_SEMESTER_MAP을 사용할 키 생성 (더 견고한 방식 필요 시 수정)
    grade_text_key = f"{level} {grade}학년" if level == '학부' and grade else None
    if level == '대학원' and semester > 0:
        grade_text_key = f"대학원 {semester}학기"

    if grade_text_key:
        level_from_map, semester_from_map = GRADE_TO_SEMESTER_MAP.get(grade_text_key, (level, semester))
        norm['norm_level'] = level_from_map
        norm['norm_semester'] = semester_from_map
    else:
        norm['norm_level'] = level
        norm['norm_semester'] = semester


    # 학점 정규화 (숫자로 변환, UserProfileRequest 모델에 따라 'gpa' 사용)
    try:
        # UserProfileRequest에서 gpa는 Optional[float]
        gpa_value = profile.get('gpa')
        norm['norm_gpa'] = float(gpa_value) if gpa_value is not None else 0.0
    except (ValueError, TypeError):
        norm['norm_gpa'] = 0.0

    # 소득분위 정규화 (숫자로 변환, profile에 'income_bracket' 키가 있다고 가정)
    # UserProfileRequest 스키마에는 없으므로, 프로필 데이터에 이 키가 있을 경우를 대비
    try:
        income_text = profile.get('income_bracket', '99') # 기본값 99 (정보 없음 의미)
        match = re.search(r'(\d+)', str(income_text))
        norm['norm_income'] = int(match.group(1)) if match else 99
    except (ValueError, TypeError):
        norm['norm_income'] = 99

    # UserProfileRequest 모델에 있는 다른 필드들도 가져오기
    norm['major'] = profile.get('major') # 'major' 필드
    norm['toeic'] = profile.get('toeic') # 'toeic' 필드

    # 어학 점수 딕셔너리 생성 (UserProfileRequest에는 TOEIC만 있음)
    norm['language_scores'] = {}
    if norm['toeic'] is not None:
        # API 및 비교 함수에서 사용할 키 형식 (대문자, 공백/특수문자 제거)
        norm['language_scores']['TOEIC'] = norm['toeic']
    # 만약 profile 딕셔너리에 다른 어학 점수 필드가 있다면 여기에 추가
    # 예: norm['language_scores']['TOEFL_IBT'] = profile.get('toefl_ibt')

    # 기타 필드 (UserProfileRequest 스키마에 없으므로 가정)
    norm['military_service'] = profile.get('military_service', '') # 예시
    norm['gender'] = profile.get('gender', '') # 예시
    norm['degree'] = profile.get('degree', '') # 예시 (학위 정보, 예: '학사 재학', '석사 졸업')

    return norm


def _check_gpa(user_gpa: float, req_text: str) -> Tuple[bool, str]:
    """GPA 요구사항을 비교합니다."""
    if req_text == "N/A" or not req_text:
        return True, "" # 조건 없으면 통과

    # "3.5/4.3", "3.5", "Cumulative GPA of 3.5 or higher" 등에서 숫자 추출
    # 소수점 한 자리 또는 두 자리 숫자 추출 (예: 3.0, 3.75)
    match = re.search(r'(\d\.\d{1,2})', req_text)
    if match:
        try:
            req_gpa = float(match.group(1))
            # 정확히 일치 요구가 아닌 이상(>=)으로 비교
            if user_gpa < req_gpa:
                # 평점 기준(4.3 또는 4.5) 명시 시 메시지에 포함 고려
                scale_match = re.search(r'/(\d\.\d)', req_text)
                scale_info = f" ({scale_match.group(1)} 만점 기준)" if scale_match else ""
                return False, f"학점 미달 (요구: {req_gpa}{scale_info} / 현재: {user_gpa:.2f})"
            else:
                return True, "" # 조건 충족
        except ValueError:
            print(f"Warning: Could not parse required GPA from '{req_text}'")
            return True, "" # 숫자 파싱 실패 시 일단 통과 처리 (확인 필요 메시지 가능)
    else:
        # 요구 학점 정보가 명확하지 않으면 일단 통과 (확인 필요 메시지 가능)
        print(f"Warning: No clear GPA requirement found in '{req_text}'")
        return True, "(GPA 요건 확인 필요)"

    return True, ""


def _check_grade_level(user_level: str, user_semester: int, req_text: str) -> Tuple[bool, str]:
    """학년/학기 요구사항을 비교합니다. (가장 복잡한 로직)"""
    if req_text == "N/A" or not req_text or user_semester == 0:
        return True, "" # 조건 없거나 사용자 정보 없으면 통과

    req_text_norm = req_text.lower().strip()

    # 1. 레벨 비교 (학부/대학원)
    is_grad_req = '대학원' in req_text_norm or '석사' in req_text_norm or '박사' in req_text_norm
    # '재학생', '학부생' 등 키워드 확인, '대학원' 키워드 없을 때만 학부생 요구로 간주
    is_undergrad_req = ('학부' in req_text_norm or '재학생' in req_text_norm or re.search(r'\d\s*학년', req_text_norm)) and not is_grad_req

    if is_grad_req and user_level != '대학원':
        return False, "대학원생 대상"
    if is_undergrad_req and user_level == '대학원':
        # 석박사 과정생도 포함하는 경우가 아니라면 불충족
        if '석박사' not in req_text_norm:
             return False, "학부생 대상"

    # 2. 학기 범위 비교 (예: "3~7차 학기", "2~7학기")
    match = re.search(r'(\d)[\s~.~-]+(\d)\s*학기', req_text_norm)
    if match:
        try:
            min_sem, max_sem = int(match.group(1)), int(match.group(2))
            if not (min_sem <= user_semester <= max_sem):
                return False, f"학기 미충족 (요구: {min_sem}~{max_sem}학기 / 현재: {user_semester}학기)"
            return True, "" # 범위 비교 통과 시 종료
        except ValueError:
            print(f"Warning: Could not parse semester range from '{req_text_norm}'")

    # 3. 특정 학년 범위 또는 단일 학년 비교 (예: "1~4학년", "4학년", "2학년 이상")
    range_match = re.search(r'(\d)[\s~.~-]+(\d)\s*학년', req_text_norm) # 예: "1~4학년"
    grade_match = re.search(r'(\d)\s*학년', req_text_norm) # 예: "4학년", "2학년 이상"

    if range_match: # "X~Y학년" 형태 먼저 처리
         try:
             min_grade, max_grade = int(range_match.group(1)), int(range_match.group(2))
             # 학부생 대상 조건인지 확인
             if user_level != '학부': return False, "학부생 대상"
             min_sem_req = (min_grade - 1) * 2 + 1
             max_sem_req = max_grade * 2
             if not (min_sem_req <= user_semester <= max_sem_req):
                 return False, f"학년 범위 미충족 (요구: {min_grade}~{max_grade}학년 / 현재: {user_semester}학기)"
             return True, ""
         except ValueError:
              print(f"Warning: Could not parse grade range from '{req_text_norm}'")
    elif grade_match: # "X학년" 또는 "X학년 이상/이하"
        try:
            req_grade = int(grade_match.group(1))
            # 학부생 대상 조건인지 확인
            if user_level != '학부': return False, "학부생 대상"
            req_sem_min = (req_grade - 1) * 2 + 1 # 예: 4학년 -> 7학기 시작
            req_sem_max = req_grade * 2         # 예: 4학년 -> 8학기 끝

            if '이상' in req_text_norm or 'Completion of at least' in req_text_norm: # 영어 케이스 추가
                if user_semester < req_sem_min:
                    return False, f"학년 미충족 (요구: {req_grade}학년 이상 / 현재: {user_semester}학기)"
            elif '이하' in req_text_norm:
                 if user_semester > req_sem_max:
                    return False, f"학년 미충족 (요구: {req_grade}학년 이하 / 현재: {user_semester}학기)"
            else: # 정확히 해당 학년
                if not (req_sem_min <= user_semester <= req_sem_max):
                     return False, f"학년 미충족 (요구: {req_grade}학년 / 현재: {user_semester}학기)"
            return True, ""
        except ValueError:
             print(f"Warning: Could not parse grade requirement from '{req_text_norm}'")

    # 4. 특정 학기 수 이상/이하 비교 (예: "Completion of at least four semesters")
    # 정규식 수정: '최소', '적어도' 등 한국어 키워드 및 다양한 표현 고려
    semester_count_match = re.search(r'(최소|적어도|at least)\s*(\d+)\s*(학기|semesters)', req_text_norm, re.IGNORECASE)
    if semester_count_match:
        try:
            min_semesters_req = int(semester_count_match.group(2))
            # 사용자 학기는 '현재 진행 중인 학기'이므로, '이수한 학기 수'는 user_semester - 1 로 계산
            completed_semesters = max(0, user_semester - 1) # 1학기생은 0학기 이수
            if completed_semesters < min_semesters_req:
                return False, f"이수 학기 수 미충족 (요구: 최소 {min_semesters_req}학기 이수 / 현재: {completed_semesters}학기 이수)"
            return True, ""
        except ValueError:
            print(f"Warning: Could not parse minimum semester count from '{req_text_norm}'")

    # 5. 기타 케이스
    if '졸업예정자' in req_text_norm and user_semester < 7: # 보통 7, 8학기
        # 단, 조기졸업 대상자는 해당될 수 있으므로, 프로필에 조기졸업 여부 확인 로직 추가 가능
        return False, "졸업예정자 대상 아님"
    # '재학생'만 언급된 경우 (다른 학년/학기 조건 없이) -> user_semester > 0 이면 통과
    if '재학생' in req_text_norm and user_semester > 0 and not grade_match and not range_match and not match and not semester_count_match:
        return True, ""

    # 위 조건들에 해당하지 않으면 불확실 -> 확인 필요 메시지 반환
    print(f"Notice: Grade requirement '{req_text_norm}' could not be definitively checked against user semester {user_semester}.")
    return True, f"(학년/학기 요건 '{req_text_norm}' 확인 필요)"


def _check_department(user_dept: str, req_text: str) -> Tuple[bool, str]:
    """학과 요구사항을 비교합니다. (매핑 테이블 사용)"""
    if req_text == "N/A" or not req_text or not user_dept:
        return True, "" # 조건 없거나 사용자 정보 없으면 통과

    req_text_norm = req_text.strip() # 공백 제거

    # 1. "전 계열", "모든 학과", "누구나" 등 전체 허용 키워드
    # 정규식 사용하여 더 유연하게 매칭 (예: "전학과", "전계열")
    if re.search(r'전\s*(계열|학과)|모든\s*학과|누구나|학과\s*무관', req_text_norm):
        return True, ""

    # 2. 사용자의 학과가 공지 텍스트에 직접 포함되는지 확인 (띄어쓰기 무시, 소문자 변환 비교)
    user_dept_processed = user_dept.replace(" ", "").lower()
    req_text_processed = req_text_norm.replace(" ", "").lower()
    if user_dept_processed in req_text_processed:
        return True, ""

    # 3. 매핑 테이블(동의어/상위그룹) 확인
    allowed_groups = DEPARTMENT_MAP.get(user_dept, [])
    for group in allowed_groups:
        group_processed = group.replace(" ", "").lower()
        if group_processed in req_text_processed:
            return True, ""

    # 4. 특정 분야 키워드 확인 (예: '이공계', '바이오', 'IT') - DEPARTMENT_MAP 값 활용
    # 사용자의 학과가 속한 카테고리(매핑 테이블의 value 리스트) 찾기
    user_categories = set()
    for dept, groups in DEPARTMENT_MAP.items():
        if dept == user_dept:
            user_categories.update(groups) # 해당 학과의 모든 카테고리 추가

    # 공지사항 텍스트에 사용자의 카테고리 중 하나라도 포함되는지 확인
    for cat in user_categories:
        cat_processed = cat.replace(" ", "").lower()
        if cat_processed in req_text_processed:
            return True, ""

    # 모든 검사를 통과하지 못하면 불일치
    return False, f"학과 미충족 (요구: {req_text_norm} / 현재: {user_dept})"


def _check_income(user_income_bracket: int, req_text: str) -> Tuple[bool, str]:
    """소득분위 요구사항을 비교합니다."""
    if req_text == "N/A" or not req_text:
        return True, "" # 조건 없으면 통과

    req_text_norm = req_text.strip()

    # "X분위 이하" 패턴 (예: "8분위 이하", "소득분위 기준 8")
    match = re.search(r'(\d+)[\s]*분위', req_text_norm)
    if match:
        try:
            req_cap = int(match.group(1))
            # user_income_bracket 값이 99이면 정보 없음으로 간주하여 확인 필요
            if user_income_bracket == 99:
                 print(f"Notice: Income bracket requirement '{req_text_norm}' needs verification as user data is missing.")
                 return True, f"(소득 {req_cap}분위 이하 확인 필요)"
            if user_income_bracket > req_cap:
                return False, f"소득분위 초과 (요구: {req_cap}분위 이하 / 현재: {user_income_bracket}분위)"
            else:
                return True, "" # 조건 만족
        except ValueError:
            print(f"Warning: Could not parse income bracket cap from '{req_text_norm}'")

    # "기초생활수급자" 또는 "가계 곤란" 키워드
    if '기초생활수급자' in req_text_norm or '가계 곤란' in req_text_norm or 'Need-based' in req_text_norm.lower():
        # 사용자 프로필에 해당 정보가 있는지 확인하는 로직 필요
        # 예: if norm_profile.get('is_basic_recipient'): return True, ""
        # 현재 프로필에 해당 정보가 없으므로, 확인 필요 메시지와 함께 통과
        print(f"Notice: Income requirement '{req_text_norm}' needs verification based on user status.")
        return True, "(수급자/가계곤란 해당 여부 확인 필요)"

    # 특정 분위 조건이나 키워드가 없으면 일단 통과 (다른 소득 관련 조건은 추가 파싱 필요)
    return True, ""


def _check_language(user_scores: Dict[str, Any], req_text: str) -> Tuple[bool, str]:
    """어학 요구사항을 비교합니다. (Regex 및 '또는'/'and' 논리)"""
    if req_text == "N/A" or not req_text:
        return True, "" # 조건 없으면 통과

    # 1. 요구되는 모든 어학 점수/등급 찾기 (정규식 개선)
    # 시험명 약어(TEPS 등), 점수(소수점 포함), 등급(N1, IH, AL, Level 5 등) 포괄
    req_list = re.findall(
        r'(TOEIC|TOEFL\s*(?:iBT|ITP)?|IELTS|JLPT|HSK|TEPS|OPIc|G-TELP)[\s:]*(?:level)?\s*([0-9]+\.?[0-9]*|N[1-5]|[A-Z]{1,3}\d?|[A-Z]{2,})',
        req_text,
        re.IGNORECASE
    )

    if not req_list:
        # "영어 능통자", "Fluent in English" 등 텍스트만 있는 경우
        if any(kw in req_text.lower() for kw in ['능통', 'fluent', 'proficient']):
            print(f"Notice: Language requirement '{req_text}' needs verification for proficiency.")
            return True, "(어학 능통 여부 확인 필요)"
        # "우대" 조건인 경우 일단 통과
        if '우대' in req_text:
            return True, ""
        # 특정 시험 언급 없으면 일단 통과 (다른 언어 요구 조건은 추가 파싱 필요)
        return True, ""

    # 2. '또는' (OR) 논리인지 '그리고' (AND / 기본값) 논리인지 확인
    is_or_logic = '또는' in req_text or ' or ' in req_text.lower()
    # 'and' 또는 쉼표(,)가 여러 조건을 나열하는 데 사용될 수 있으므로 AND로 간주 (단순화)
    is_and_logic = ' and ' in req_text.lower() or ',' in req_text

    pass_results = []
    missing_scores = [] # 사용자가 점수 정보를 입력하지 않은 시험
    requirements_details = [] # 요구 조건 상세 저장

    # OPIc / 토익스피킹 등급 순서 (높은 순 -> 낮은 순, 점수 매핑)
    level_scores = {
        'OPIC': {'AL': 6, 'IH': 5, 'IM3': 4, 'IM2': 3, 'IM1': 2, 'IL': 1, 'NH': 0},
        'TOEIC_SPEAKING': {'AL': 8, 'AH': 7, 'IM3': 6, 'IM2': 5, 'IM1': 4, 'IL': 3, 'NH': 0} # 예시 점수
        # 기타 등급 시험 추가 가능
    }

    for test_name, req_score_str in req_list:
        # 시험명 정규화 (API 키와 일치하도록)
        test_key_raw = test_name.upper().replace(' ', '').replace('IBT','_IBT').replace('SPEAKING', '_SPEAKING')
        test_key = test_key_raw # 최종 키

        # 요구 조건 상세 저장
        requirements_details.append(f"{test_name} {req_score_str}")

        # 사용자 점수 가져오기
        user_score_val = user_scores.get(test_key)

        if user_score_val is None:
            missing_scores.append(test_key)
            pass_results.append(False) # 점수 없으면 일단 False
            continue

        req_score_str_norm = req_score_str.upper() # 대소문자 통일

        current_pass = False
        try:
            # 등급 기반 시험 처리 (JLPT, HSK, OPIc, TOEIC_SPEAKING 등)
            if test_key in ['JLPT', 'HSK']:
                req_level_match = re.search(r'(\d)', req_score_str_norm)
                user_level_match = re.search(r'(\d)', str(user_score_val))
                if req_level_match and user_level_match:
                    req_val = int(req_level_match.group(1))
                    user_val = int(user_level_match.group(1))
                    if (test_key == 'JLPT' and user_val <= req_val): # 숫자가 작을수록 높음
                        current_pass = True
                    elif (test_key == 'HSK' and user_val >= req_val): # 숫자가 클수록 높음
                        current_pass = True
            elif test_key in level_scores: # OPIc, TOEIC Speaking 등
                 level_map = level_scores[test_key]
                 req_level_score = level_map.get(req_score_str_norm, -1)
                 user_level_str = str(user_score_val).upper()
                 user_level_score = level_map.get(user_level_str, -1)
                 if req_level_score != -1 and user_level_score >= req_level_score:
                     current_pass = True

            # 점수 기반 시험 처리 (TOEIC, TOEFL, IELTS, TEPS 등)
            else:
                req_score_num = float(req_score_str_norm)
                # 사용자 점수가 숫자 형태가 아니면 변환 시도
                if not isinstance(user_score_val, (int, float)):
                     user_score_val = float(str(user_score_val))

                if user_score_val >= req_score_num:
                    current_pass = True

            pass_results.append(current_pass)

        except (ValueError, TypeError) as e:
             print(f"Warning: Error comparing language score for {test_key}. Req: '{req_score_str}', User: '{user_score_val}'. Error: {e}")
             pass_results.append(False) # 파싱 또는 비교 실패 시 False

    # 최종 판정
    final_pass = False
    num_requirements = len(req_list)

    if is_or_logic: # '또는' 조건 명시 시
        if any(pass_results):
            final_pass = True
    elif is_and_logic: # 'and' 또는 ',' 사용 시 (명시적 AND)
        if all(pass_results) and len(pass_results) == num_requirements:
            final_pass = True
    else: # 조건이 하나거나, 논리 연산자 불분명 시 (기본 AND 간주)
         if all(pass_results) and len(pass_results) == num_requirements:
             final_pass = True


    if final_pass:
        return True, ""
    else:
        reason_msg = f"어학 요건 미충족 (요구: {', '.join(requirements_details)})"
        if missing_scores:
            missing_str = ', '.join(sorted(list(set(missing_scores)))) # 중복 제거 및 정렬
            reason_msg += f" (점수 입력 필요: {missing_str})"
        return False, reason_msg


def _check_simple_text(user_value: str, req_text: str, map_dict: Dict[str, List[str]] = None) -> Tuple[bool, str]:
    """성별, 병역 등 단순 텍스트 요구사항을 매핑 테이블을 사용하여 비교합니다."""
    if req_text == "N/A" or not req_text:
        return True, "" # 조건 없으면 통과

    req_text_norm = req_text.lower().strip()

    # "무관" 키워드가 공지사항에 있으면 사용자 값과 관계없이 항상 통과
    # 매핑 테이블의 '무관' 키에 해당하는 표현들 확인
    if map_dict and any(keyword.lower() in req_text_norm for keyword in map_dict.get('무관', [])):
        return True, ""
    # 일반적인 '무관' 표현 확인
    if any(keyword in req_text_norm for keyword in ['무관', '상관없음', '제한 없음', '관계 없음']):
         return True, ""

    # 사용자 정보가 없는 경우
    if not user_value:
        # 확인 필요 메시지와 함께 일단 통과 (일치율 계산에는 영향 O)
        print(f"Notice: Simple text requirement '{req_text}' needs verification as user data is missing.")
        return True, f"({req_text} 관련 정보 확인 필요)"

    user_value_norm = user_value.lower().strip() # 사용자 값 정규화

    # 1. 매핑 테이블 사용 (map_dict가 제공된 경우)
    if map_dict:
        matched = False
        # 사용자의 상태(user_value)에 해당하는 허용 표현(allowed_expressions) 목록 가져오기
        # 사용자 값이 map_dict의 키에 없을 수 있으므로 get 사용
        allowed_expressions = [expr.lower() for expr in map_dict.get(user_value, [])]

        # 공지사항 요구사항(req_text_norm)에 사용자의 상태를 나타내는 허용 표현 중 하나라도 포함되는지 확인
        if any(expr in req_text_norm for expr in allowed_expressions):
            matched = True

        # 위에서 매칭되지 않았다면, 반대로 공지사항의 특정 요구사항이 사용자 상태와 호환되는지 확인
        # (예: 공지 '군필 또는 면제', 사용자 '군필')
        if not matched:
            for status_key, req_keyword_list in map_dict.items():
                # 공지사항 텍스트에 특정 상태(예: '군필', '면제')를 나타내는 키워드가 있는지 확인
                req_keywords_in_text = [req_keyword.lower() for req_keyword in req_keyword_list if req_keyword.lower() in req_text_norm]
                if req_keywords_in_text:
                    # 해당 키워드 상태(status_key)가 사용자의 상태(user_value)와 일치하는지 확인
                    if status_key == user_value:
                         matched = True
                         break # 하나라도 맞으면 매칭 성공

        if matched:
            return True, ""
        else:
            # 매핑 테이블 기준으로 명확히 불일치
            return False, f"조건 불일치 (요구: {req_text} / 현재: {user_value})"

    # 2. 매핑 테이블 없이 직접 비교 (Fallback)
    # (이 부분은 매핑 테이블이 항상 제공된다면 제거 가능)
    if req_text_norm in user_value_norm or user_value_norm in req_text_norm:
        return True, ""

    # 특정 키워드 기반 불일치 확인 (매핑 없을 때 fallback)
    if ('여학생' in req_text_norm or 'female' in req_text_norm) and user_value_norm in ['남성', 'male']:
        return False, f"성별 미충족 (요구: {req_text})"
    if ('남학생' in req_text_norm or 'male' in req_text_norm) and user_value_norm in ['여성', 'female']:
        return False, f"성별 미충족 (요구: {req_text})"
    if ('군필' in req_text_norm or '면제' in req_text_norm) and user_value_norm in ['미필', 'unserved']:
         # 전문연구요원은 별도 처리되었으므로 여기서는 제외해도 됨
         return False, f"병역 요건 미충족 (요구: {req_text})"

    # 매핑 없고 직접 비교도 실패 시 불일치
    # 또는 불확실하므로 확인 필요 메시지 반환
    print(f"Notice: Simple text requirement '{req_text}' could not be definitively checked against user value '{user_value}'.")
    return True, f"({req_text} 관련 조건 확인 필요)"


# --- 3. 메인 비교 함수 (수정본) ---

def check_suitability(user_profile: Dict[str, Any], notice_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    사용자 프로필과 AI가 추출한 공지사항 JSON을 비교하여 적합도와 일치율(%)을 반환합니다.

    Args:
        user_profile (Dict[str, Any]): 사용자 프로필 딕셔너리 (UserProfileRequest 스키마 기반).
        notice_json (Dict[str, Any]): AI가 추출한 공지사항 정보 딕셔너리.

    Returns:
        Dict[str, Any]: 비교 결과 딕셔너리.
            - suitable (bool): 모든 명시적 불충족 조건이 없는 경우 True.
            - reason (str): 불충족 시 사유 또는 충족/확인필요 메시지.
            - match_percentage (float): 전체 관련 조건 중 충족한 조건의 비율 (0.0 ~ 100.0).
            - pass (bool): suitable과 동일 (기존 호환성).
    """

    # 1. 단순 정보성 공지 처리 (비교 대상 아님)
    # qualifications 필드가 없거나, 있더라도 비어있는 경우
    if "qualifications" not in notice_json or not notice_json.get("qualifications"):
        return {
            "suitable": True, # 정보성 공지는 누구나 볼 수 있으므로 True
            "reason": "정보성 공지 (자격 요건 없음)",
            "match_percentage": 100.0, # 비교할 조건이 없으므로 100%
            "pass": True
        }

    quals = notice_json.get("qualifications", {})
    # quals가 문자열 등으로 잘못 들어올 경우 처리
    if not isinstance(quals, dict):
         return {
            "suitable": True, # 자격 요건 파싱 실패 시 일단 True 처리
            "reason": f"자격 요건 정보 형식 오류 (예상: dict, 실제: {type(quals)})",
            "match_percentage": 100.0, # 비교 불가
            "pass": True
        }


    fail_reasons: List[str] = []
    met_criteria_count = 0
    total_relevant_criteria = 0
    verification_needed_reasons: List[str] = [] # 확인 필요 항목 저장

    try:
        # 2. 사용자 프로필 표준화
        norm_profile = _normalize_user_profile(user_profile)

        # 3. 개별 항목 비교 및 카운트
        # (조건 키, 검사 함수, 사용자 값(들)...) 튜플 리스트
        checks_to_perform = [
            # quals 딕셔너리에 해당 키가 있을 때 사용할 비교 함수 및 사용자 프로필 값
            ('gpa_min', _check_gpa, norm_profile['norm_gpa']),
            ('grade_level', _check_grade_level, norm_profile['norm_level'], norm_profile['norm_semester']),
            ('department', _check_department, norm_profile.get('major', '')), # 프로필의 'major' 사용
            ('income_status', _check_income, norm_profile['norm_income']),
            ('language_requirements_text', _check_language, norm_profile.get('language_scores', {})),
            ('military_service', _check_simple_text, norm_profile.get('military_service', ''), MILITARY_SERVICE_MAP), # 병역 맵 전달
            ('gender', _check_simple_text, norm_profile.get('gender', ''), GENDER_MAP), # 성별 맵 전달
            ('degree', _check_simple_text, norm_profile.get('degree', '')), # 학위 조건 (맵 없이)
            # ('other', _check_simple_text, norm_profile.get('other_profile_field', '')), # 기타 조건 비교 함수 필요 시 추가
        ]

        # quals 딕셔너리에 있는 모든 키에 대해 반복
        for key, req_text in quals.items():
            # 값이 존재하고 "N/A"가 아니며 비어있지 않은 경우에만 관련 조건으로 간주
            if req_text and req_text != 'N/A':
                total_relevant_criteria += 1
                found_check = False
                # 해당 키에 대한 검사 함수 찾기
                for check_key, check_func, *user_values in checks_to_perform:
                    if key == check_key:
                        found_check = True
                        try:
                            # *user_values 언패킹 시 map_dict 포함될 수 있음
                            passed, reason = check_func(*user_values, req_text)
                            if passed:
                                met_criteria_count += 1
                                # 확인 필요 메시지가 반환된 경우 저장
                                if reason and reason.startswith("("):
                                    verification_needed_reasons.append(reason.strip("()"))
                            else:
                                fail_reasons.append(reason)
                        except Exception as check_err:
                             print(f"Error checking condition '{key}': {check_err}")
                             fail_reasons.append(f"{key} 조건 비교 중 오류")
                        break # 해당 키 검사 완료

                # 'other' 필드 또는 checks_to_perform에 정의되지 않은 키 처리
                if not found_check and key == 'other':
                     # 'other' 조건은 복잡하여 자동 비교 어려움 -> 확인 필요
                     verification_needed_reasons.append(f"기타 조건 확인 필요: {req_text}")
                     # 일단 충족한 것으로 간주하여 match_percentage에 반영 (선택적)
                     # met_criteria_count += 1
                elif not found_check:
                     print(f"Warning: No check function defined for qualification key '{key}'. Needs verification.")
                     verification_needed_reasons.append(f"{key} 조건({req_text}) 확인 필요")
                     # 일단 충족한 것으로 간주할지 여부 결정 필요
                     # met_criteria_count += 1


        # 4. 일치율 계산
        if total_relevant_criteria > 0:
            match_percentage = (met_criteria_count / total_relevant_criteria) * 100
        else:
            # 관련 조건이 아예 없으면 (모두 "N/A" 등) 100% 일치로 간주
            match_percentage = 100.0

        # 5. 최종 결과 조합
        final_reason = ""
        # 중복 제거 및 정렬을 위해 set 사용
        unique_fails = sorted(list(set(fail_reasons)))
        unique_verifications = sorted(list(set(verification_needed_reasons)))

        if not unique_fails: # 명시적 실패 조건 없음
            final_reason = "모든 명시적 조건 충족"
            if unique_verifications:
                final_reason += f" (단, 확인 필요: {'; '.join(unique_verifications)})"
        else: # 실패 조건 있음
            final_reason = "; ".join(unique_fails)
            if unique_verifications:
                final_reason += f" (추가 확인 필요: {'; '.join(unique_verifications)})"

        is_suitable = not unique_fails # 실패 사유가 없으면 적합 (확인 필요 항목은 suitable에 영향 안 줌)

        return {
            "suitable": is_suitable,
            "reason": final_reason,
            "match_percentage": round(match_percentage, 1), # 소수점 첫째 자리까지
            "pass": is_suitable # 기존 pass 필드 유지
        }

    except Exception as e:
        # 전체 비교 로직에서 예외 발생 시
        import traceback
        print(f"Error during comparison process: {e}\n{traceback.format_exc()}") # 스택 트레이스 출력
        return {
            "suitable": False,
            "reason": f"전체 비교 처리 중 오류 발생: {e}",
            "match_percentage": 0.0,
            "pass": False
        }

# --- 테스트 예시 (필요 시 주석 해제하여 사용) ---
# if __name__ == "__main__":
#     # 예시 사용자 프로필 (UserProfileRequest 스키마 가정)
#     test_user_profile = {
#         "grade": 3,              # 학년 (정수)
#         "major": "컴퓨터과학과",
#         "gpa": 3.8,
#         "toeic": 850,
#         "keywords": ["#취업", "#공모전"],
#         # --- 스키마 외 필드 (가정) ---
#         "language_scores": {"TOEIC": 850, "OPIC": "IH"}, # 다양한 어학 점수
#         "military_service": "군필",
#         "gender": "남성",
#         "degree": "학사 재학",
#         "income_bracket": 5 # 소득 분위
#     }
#
#     # 예시 공고 JSON (AI 추출 결과 가정)
#     test_notice_json_1 = {
#         "qualifications": {
#             "gpa_min": "3.5",
#             "grade_level": "학부 3학년 이상",
#             "department": "이공 계열",
#             "language_requirements_text": "TOEIC 800점 이상 또는 OPIc IH 이상",
#             "military_service": "군필 또는 면제자",
#             "other": "Python 사용 경험자 우대"
#         }
#     }
#
#     test_notice_json_2 = {
#         "qualifications": {
#             "grade_level": "대학원생",
#             "department": "인문사회계열",
#             "income_status": "4분위 이하",
#         }
#     }
#
#     test_notice_json_3 = { # 정보성 공지 (자격 요건 없음)
#         "title": "행사 안내",
#         "body_text": "..."
#     }
#
#     print("--- 비교 결과 1 ---")
#     result1 = check_suitability(test_user_profile, test_notice_json_1)
#     print(json.dumps(result1, indent=2, ensure_ascii=False))
#
#     print("\n--- 비교 결과 2 ---")
#     result2 = check_suitability(test_user_profile, test_notice_json_2)
#     print(json.dumps(result2, indent=2, ensure_ascii=False))
#
#     print("\n--- 비교 결과 3 (정보성) ---")
#     result3 = check_suitability(test_user_profile, test_notice_json_3)
#     print(json.dumps(result3, indent=2, ensure_ascii=False))