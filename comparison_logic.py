import re
from typing import Dict, Any, List, Tuple

# --- 1. 정규화 및 매핑 데이터 (Ontology) ---
# 이 데이터는 CSV 분석을 기반으로 업그레이드되었습니다.
# (향후 이 맵만 확장하면 비교 로직이 더 똑똑해집니다.)

# 학과 매핑: '사용자 프로필 학과': ['공지사항에서 허용되는 동의어/상위그룹']
DEPARTMENT_MAP = {
    '경영학과': ['경영대학', '상경대학', '상경‧경영대학', '경영/경제 계열'],
    '경제학부': ['경제대학', '상경대학', '상경‧경영대학', '경영/경제 계열'],
    '컴퓨터과학과': ['공과대학', '첨단컴퓨팅학부', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열'],
    '인공지능학과': ['인공지능융합대학', '첨단컴퓨팅학부', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열'],
    '전기전자공학부': ['공과대학', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열'],
    '생명공학과': ['생명시스템대학', '바이오 분야', '이공 계열'],
    '화학과': ['이과대학', '바이오 분야', '이공 계열'],
    '문헌정보학과': ['문과대학'],
    '국어국문학과': ['문과대학'],
    '의예과': ['의과대학'],
    '간호학과': ['간호대학'],
}

# 학년/학기 정규화: '사용자 프로필 텍스트': (레벨, 학기)
# (1학년 1학기=1, 1학년 2학기=2, ... 4학년 2학기=8)
GRADE_TO_SEMESTER_MAP = {
    '학부 1학년': ('학부', 1), # 1학기 재학 중으로 가정
    '학부 2학년': ('학부', 3), # 2학년 1학기 재학 중으로 가정
    '학부 3학년': ('학부', 5), # 3학년 1학기 재학 중으로 가정
    '학부 4학년': ('학부', 7), # 4학년 1학기 재학 중으로 가정
    '대학원 1학기': ('대학원', 1),
    '대학원 2학기': ('대학원', 2),
    '대학원 3학기': ('대학원', 3),
    '대학원 4학기': ('대학원', 4),
}


# --- 2. 헬퍼 함수 (비교 로직 업그레이드) ---

def _normalize_user_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """사용자 프로필을 비교 가능한 표준 형태로 정규화합니다."""
    norm = profile.copy()
    
    # 학년/학기 정규화
    grade_text = profile.get('grade_level', 'N/A')
    level, semester = GRADE_TO_SEMESTER_MAP.get(grade_text, ('N/A', 0))
    norm['norm_level'] = level
    norm['norm_semester'] = semester
    
    # 학점 정규화 (숫자로 변환)
    try:
        norm['norm_gpa'] = float(profile.get('gpa', 0.0))
    except (ValueError, TypeError):
        norm['norm_gpa'] = 0.0
        
    # 소득분위 정규화 (숫자로 변환)
    try:
        # "5분위" -> 5
        income_text = profile.get('income_bracket', '99')
        match = re.search(r'(\d+)', str(income_text))
        norm['norm_income'] = int(match.group(1)) if match else 99
    except (ValueError, TypeError):
        norm['norm_income'] = 99

    return norm


def _check_gpa(user_gpa: float, req_text: str) -> Tuple[bool, str]:
    """GPA 요구사항을 비교합니다."""
    if req_text == "N/A":
        return True, ""
    
    # "3.5/4.3", "3.5" "Cumulative GPA of 3.5"
    match = re.search(r'(\d\.\d+)', req_text)
    if match:
        try:
            req_gpa = float(match.group(1))
            if user_gpa < req_gpa:
                return False, f"학점 미달 (요구: {req_gpa} / 현재: {user_gpa})"
        except ValueError:
            pass # 숫자가 아니면 통과
    return True, ""


def _check_grade_level(user_level: str, user_semester: int, req_text: str) -> Tuple[bool, str]:
    """학년/학기 요구사항을 비교합니다. (가장 복잡한 로직)"""
    if req_text == "N/A" or user_semester == 0:
        return True, ""

    req_text_norm = req_text.lower()
    
    # 1. 레벨 비교 (학부/대학원)
    if '대학원' in req_text_norm and user_level != '대학원':
        return False, "대학원생 대상"
    if ('학부' in req_text_norm or '재학생' in req_text_norm) and user_level != '학부':
         if user_level == '대학원' and '석박사' not in req_text_norm:
              return False, "학부생 대상"

    # 2. 학기 범위 비교 (예: "3~7차 학기")
    match = re.search(r'(\d)[\s~.~-]+(\d)\s*학기', req_text)
    if match:
        min_sem, max_sem = int(match.group(1)), int(match.group(2))
        if not (min_sem <= user_semester <= max_sem):
            return False, f"학기 미충족 (요구: {min_sem}~{max_sem}학기 / 현재: {user_semester}학기)"
        return True, "" # 범위 비교 통과 시 종료

    # 3. 특정 학년 비교 (예: "4학년", "2학년 이상")
    match = re.search(r'(\d)\s*학년', req_text)
    if match:
        req_grade = int(match.group(1))
        req_sem_min = (req_grade - 1) * 2 + 1 # 4학년 -> 7학기
        req_sem_max = req_sem_min + 1         # 4학년 -> 8학기

        if '이상' in req_text:
            if user_semester < req_sem_min:
                return False, f"학년 미충족 (요구: {req_grade}학년 이상 / 현재: {user_semester}학기)"
        elif '이하' in req_text:
             if user_semester > req_sem_max:
                return False, f"학년 미충족 (요구: {req_grade}학년 이하 / 현재: {user_semester}학기)"
        else: # 정확히 해당 학년
            if not (req_sem_min <= user_semester <= req_sem_max):
                 return False, f"학년 미충족 (요구: {req_grade}학년 / 현재: {user_semester}학기)"
        return True, ""

    # 4. 기타 케이스
    if '졸업예정자' in req_text and user_semester < 7: # 7, 8학기
        return False, "졸업예정자 대상"
    if 'at least four semesters' in req_text and user_semester < 4:
        return False, "최소 4학기 이수 필요"

    return True, ""


def _check_department(user_dept: str, req_text: str) -> Tuple[bool, str]:
    """학과 요구사항을 비교합니다. (매핑 테이블 사용)"""
    if req_text == "N/A" or not user_dept:
        return True, ""

    # 1. 사용자의 학과가 공지 텍스트에 직접 언급되는지 (예: "경영대학" in "상경‧경영대학")
    if user_dept in req_text:
        return True, ""
    
    # 2. 공지 텍스트가 사용자의 학과에 직접 언급되는지 (예: "상경‧경영대학" in "경영대학" -> False)
    if req_text in user_dept:
        return True, ""

    # 3. 매핑 테이블(동의어/상위그룹) 확인
    allowed_groups = DEPARTMENT_MAP.get(user_dept, [])
    for group in allowed_groups:
        if group in req_text:
            return True, ""
            
    # 4. "전 계열", "누구나" 등은 통과
    if any(s in req_text for s in ['전 계열', '모든 학과', '누구나']):
        return True, ""

    # 5. "이공 계열" "바이오 분야" 등 특정 그룹 체크
    # (DEPARTMENT_MAP의 value 값들을 체크)
    
    return False, f"학과 미충족 (요구: {req_text} / 현재: {user_dept})"


def _check_income(user_income_bracket: int, req_text: str) -> Tuple[bool, str]:
    """소득분위 요구사항을 비교합니다."""
    if req_text == "N/A":
        return True, ""

    # "X분위 이하" 패턴 (예: "8분위 이하", "소득분위 기준 8")
    match = re.search(r'(\d+)[\s]*분위', req_text)
    if match:
        try:
            req_cap = int(match.group(1))
            if user_income_bracket > req_cap:
                return False, f"소득분위 초과 (요구: {req_cap}분위 이하 / 현재: {user_income_bracket}분위)"
        except ValueError:
            pass
            
    # "가계 곤란" -> 사용자가 소득분위 정보를 가지고 있다면, 일단 통과로 간주 (증빙은 별개)
    if '가계 곤란' in req_text or 'Need-based' in req_text:
        return True, "" # (단, UI에서 '증빙 서류 필요' 알림)

    return True, ""


def _check_language(user_scores: Dict[str, Any], req_text: str) -> Tuple[bool, str]:
    """어학 요구사항을 비교합니다. (Regex 및 '또는' 논리)"""
    if req_text == "N/A" or not req_text:
        return True, ""

    # 1. 요구되는 모든 어학 점수/등급을 찾습니다.
    # (시험명, 점수/등급) 튜플의 리스트
    # 예: "TOEIC 850점 또는 TOEFL iBT 90점 이상"
    # -> [('TOEIC', '850'), ('TOEFL iBT', '90')]
    req_list = re.findall(
        r'(TOEIC|TOEFL iBT|IELTS|JLPT|HSK|TEPS|OPIc)[\s]*([0-9\.]+|N[1-5]|IH|AL)', 
        req_text, 
        re.IGNORECASE
    )

    if not req_list:
        # "영어 능통자" 등 텍스트만 있으면 일단 통과
        return True, "" 

    # 2. '또는' (OR) 논리인지 '그리고' (AND) 논리인지 확인
    is_or_logic = '또는' in req_text or ' or ' in req_text.lower()
    
    pass_results = []

    for test_name, req_score in req_list:
        test_key = test_name.upper().replace(' ', '_') # 'TOEFL iBT' -> 'TOEFL_IBT'
        user_score = user_scores.get(test_key, 0)
        
        try:
            # JLPT/HSK 등급 비교 (N1 > N2, 6급 > 5급)
            if test_key in ['JLPT', 'HSK']:
                req_val = int(re.search(r'(\d)', req_score).group(1))
                if (test_key == 'JLPT' and user_score <= req_val) or \
                   (test_key == 'HSK' and user_score >= req_val):
                    pass_results.append(True)
                else:
                    pass_results.append(False)
            
            # OPIc 등급 비교 (AL > IH > IM)
            elif test_key == 'OPIc':
                 # (이 부분은 등급표를 만들어 비교해야 함)
                 pass_results.append(True) # 단순화
                 
            # 점수 비교 (TOEIC, TOEFL, IELTS, TEPS)
            else:
                if user_score >= float(req_score):
                    pass_results.append(True)
                else:
                    pass_results.append(False)
        except:
             pass_results.append(False) # 파싱 실패 시 False


    if is_or_logic:
        if any(pass_results):
            return True, ""
    else: # AND 논리 (기본값)
        if all(pass_results):
            return True, ""

    return False, f"어학 요건 미충족 (요구: {req_text})"


def _check_simple_text(user_value: str, req_text: str) -> Tuple[bool, str]:
    """성별, 병역 등 단순 텍스트를 비교합니다."""
    if req_text == "N/A" or not user_value:
        return True, ""
    
    # 예: req_text="여학생", user_value="남성"
    if req_text in user_value: # "군필" in "군필"
        return True, ""
    if user_value in req_text: # "남성" in "군필(남성)"
        return True, ""
    
    # 특수 케이스
    if '여학생' in req_text and user_value == '남성':
        return False, f"성별 미충족 (요구: {req_text})"
    if '군필' in req_text and user_value == '미필':
        return False, f"병역 요건 미충족 (요구: {req_text})"
        
    return True, ""


# --- 3. 메인 비교 함수 ---

def check_suitability(user_profile: Dict[str, Any], notice_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    사용자 프로필과 AI가 추출한 공지사항 JSON을 비교하여 적합도를 반환합니다.
    """
    
    # 1. 단순 정보성 공지 (비교 대상 아님)
    if "qualifications" not in notice_json:
        return {
            "suitable": False, # '적합' 여부가 아님
            "reason": "정보성 공지 (행사/학사/일반)",
            "pass": None # 비교 대상이 아님
        }

    quals = notice_json.get("qualifications", {})
    fail_reasons: List[str] = []

    try:
        # 2. 사용자 프로필 표준화
        norm_profile = _normalize_user_profile(user_profile)

        # 3. 개별 항목 비교
        
        # GPA
        passed, reason = _check_gpa(norm_profile['norm_gpa'], quals.get('gpa_min', 'N/A'))
        if not passed: fail_reasons.append(reason)
        
        # 학년/학기
        passed, reason = _check_grade_level(norm_profile['norm_level'], norm_profile['norm_semester'], quals.get('grade_level', 'N/A'))
        if not passed: fail_reasons.append(reason)

        # 학과
        passed, reason = _check_department(norm_profile.get('department', ''), quals.get('department', 'N/A'))
        if not passed: fail_reasons.append(reason)

        # 소득분위
        passed, reason = _check_income(norm_profile['norm_income'], quals.get('income_status', 'N/A'))
        if not passed: fail_reasons.append(reason)

        # 어학
        passed, reason = _check_language(norm_profile.get('language_scores', {}), quals.get('language_requirements_text', 'N/A'))
        if not passed: fail_reasons.append(reason)

        # 병역
        passed, reason = _check_simple_text(norm_profile.get('military_service', ''), quals.get('military_service', 'N/A'))
        if not passed: fail_reasons.append(reason)

        # 성별
        passed, reason = _check_simple_text(norm_profile.get('gender', ''), quals.get('gender', 'N/A'))
        if not passed: fail_reasons.append(reason)

        # 4. 최종 결과 반환
        if not fail_reasons:
            return {
                "suitable": True,
                "reason": "적합",
                "pass": True
            }
        else:
            return {
                "suitable": False,
                "reason": "; ".join(fail_reasons),
                "pass": False
            }

    except Exception as e:
        print(f"Error during comparison: {e}")
        return {
            "suitable": False,
            "reason": f"비교 중 오류 발생: {e}",
            "pass": False
        }