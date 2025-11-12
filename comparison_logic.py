# comparison_logic.py 
# - 스키마 정합성 (auth_schemas v2), JSONB language_scores 다중 시험 지원
# - 필수/우대/선택 태깅 + confidence 가중치 반영
# - 어학 AND/OR 복합 로직 감지 (VERIFY 처리)
# - 어학 점수 표준화/정규화(숫자형·등급형 혼합)
# - GPA 스케일 환산(4.3/4.5 스케일 혼재 대응)
# - [수정] 전공 매칭 로직 수정 (유사도 검사 제거, 기본 FAIL)
# - [수정] 애매한 요건(예: "성실한", "9학점") VERIFY 처리 (기본 PASS 제거)
# - [수정] 점수 계산 로직 (pass_count, total_checks) 완전 제거
# - [수정] 라벨 결정을 (FAIL / VERIFY 존재 여부)로만 판단 (key_date 포함)
# - [수정] 모든 반환 메시지를 한글(KOREAN)로 변경
# - [수정] 'N/A', '해당 없음' 값 PASS 처리
# - 설명 가능성(reason_codes/reasons_human/missing_info) 강화
# - 로깅/에러 내성

from __future__ import annotations
import re
import logging
from typing import Dict, Any, List, Tuple, Literal, Optional, Set, Union
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# =========================
# 0) 튜닝/정책 상수
# =========================

# 항목 가중치는 이제 '필수' 여부 판단에만 사용됨
CRITERIA_WEIGHTS = {
    "required": 0.50,
    "preferred": 0.30,
    "optional": 0.20,
}

# GPA 스케일 기본값
DEFAULT_GPA_SCALE = 4.5

# =========================
# 1) 어학 정규화 온톨로지
# =========================

LANGUAGE_KEY_MAP = {
    'toeic': 'TOEIC', '토익': 'TOEIC',
    'toefl': 'TOEFL_IBT', '토플': 'TOEFL_IBT', 'toefl ibt': 'TOEFL_IBT', 'ibt': 'TOEFL_IBT',
    'ielts': 'IELTS', '아이엘츠': 'IELTS',
    'jlpt': 'JLPT',
    'opic': 'OPIC', '오픽': 'OPIC', 'opi c': 'OPIC',
    'toeic speaking': 'TOEIC_SPEAKING', '토익스피킹': 'TOEIC_SPEAKING', 'toeic spk': 'TOEIC_SPEAKING',
    'teps': 'TEPS', '텝스': 'TEPS',
    'hsk': 'HSK',
}

# 등급→서열 점수(높을수록 좋음)
LANGUAGE_LEVEL_MAP = {
    'JLPT': {'N1': 5, 'N2': 4, 'N3': 3, 'N4': 2, 'N5': 1},
    'HSK': {'6급': 6, '5급': 5, '4급': 4, '3급': 3, '2급': 2, '1급': 1},
    'OPIC': {'AL': 7, 'IH': 6, 'IM3': 5, 'IM2': 4, 'IM1': 3, 'IL': 2, 'NH': 1, 'NM': 0, 'NL': 0},
    'TOEIC_SPEAKING': {  # 다양한 표기 흡수
        'ADVANCED HIGH': 8, 'ADVANCED MID': 7, 'ADVANCED LOW': 7, 'AL': 7,
        'INTERMEDIATE HIGH': 6, 'IH': 6,
        'INTERMEDIATE MID 3': 5, 'IM3': 5,
        'INTERMEDIATE MID 2': 4, 'IM2': 4,
        'INTERMEDIATE MID 1': 3, 'IM1': 3,
        'INTERMEDIATE LOW': 2, 'IL': 2,
        'NOVICE HIGH': 1, 'NH': 1,
        'NOVICE MID': 0, 'NM': 0, 'NOVICE LOW': 0, 'NL': 0
    }
}

# 숫자형 시험의 최대 점수(정규화용)
LANGUAGE_MAX_SCORE = {
    'TOEIC': 990.0,
    'TOEFL_IBT': 120.0,
    'IELTS': 9.0,
    'TEPS': 600.0,  # (최근 기준 600 만점)
    # 등급형은 후술 정규화
}

# 등급형 최대 서열값
LANG_LEVEL_MAX = {
    'JLPT': max(LANGUAGE_LEVEL_MAP['JLPT'].values()),
    'HSK': max(LANGUAGE_LEVEL_MAP['HSK'].values()),
    'OPIC': max(LANGUAGE_LEVEL_MAP['OPIC'].values()),
    'TOEIC_SPEAKING': max(LANGUAGE_LEVEL_MAP['TOEIC_SPEAKING'].values()),
}

# =========================
# 2) [수정] 전공 매핑 (연세대 기준 확장)
# =========================

DEPARTMENT_MAP = {
    # 문과대학
    '국어국문학과': ['문과대학', '인문계열'],
    '중어중문학과': ['문과대학', '인문계열'],
    '영어영문학과': ['문과대학', '인문계열'],
    '독어독문학과': ['문과대학', '인문계열'],
    '불어불문학과': ['문과대학', '인문계열'],
    '노어노문학과': ['문과대학', '인문계열'],
    '사학과': ['문과대학', '인문계열', '사회계열'],
    '철학과': ['문과대학', '인문계열'],
    '문헌정보학과': ['문과대학', '인문계열', '사회계열'],
    '심리학과': ['문과대학', '인문계열', '사회계열'],

    # 상경대학/경영대학
    '경제학부': ['상경대학', '상경‧경영대학', '사회계열'],
    '응용통계학과': ['상경대학', '상경‧경영대학', '사회계열', '데이터사이언스'],
    '경영학과': ['경영대학', '상경‧경영대학', '사회계열'],

    # 이과대학
    '수학과': ['이과대학', '이공계열', '자연계열'],
    '물리학과': ['이과대학', '이공계열', '자연계열'],
    '화학과': ['이과대학', '이공계열', '자연계열'],
    '지구시스템과학과': ['이과대학', '이공계열', '자연계열'],
    '천문우주학과': ['이과대학', '이공계열', '자연계열'],
    '대기과학과': ['이과대학', '이공계열', '자연계열'],

    # 공과대학
    '화공생명공학부': ['공과대학', '이공계열'],
    '전기전자공학부': ['공과대학', '이공계열', 'IT계열', 'ICT 분야'], 
    '건축공학과': ['공과대학', '이공계열'],
    '도시공학과': ['공과대학', '이공계열', '사회계열'],
    '사회환경시스템공학부': ['공과대학', '이공계열'],
    '기계공학부': ['공과대학', '이공계열'],
    '신소재공학부': ['공과대학', '이공계열'],
    '산업공학과': ['공과대학', '이공계열', 'IT계열', 'ICT 분야'], 
    '컴퓨터과학과': ['공과대학', '인공지능융합대학', '이공계열', 'IT계열', 'ICT 분야'], 
    '시스템반도체공학과': ['공과대학', '이공계열', 'IT계열', 'ICT 분야'], 

    # 생명시스템대학 (사용자 예시)
    '시스템생물학과': ['생명시스템대학', '이공계열', '자연계열'],
    '생화학과': ['생명시스템대학', '이공계열', '자연계열'],
    '생명공학과': ['생명시스템대학', '이공계열'],

    # 인공지능융합대학
    '인공지능학과': ['인공지능융합대학', '이공계열', 'IT계열', 'ICT 분야'], 
    '데이터사이언스학과': ['응용통계학과', '인공지능융합대학', '이공계열', 'IT계열', 'ICT 분야'], # 응통과 연관

    # 신과대학
    '신학과': ['신과대학', '인문계열'],

    # 사회과학대학
    '정치외교학과': ['사회과학대학', '사회계열'],
    '행정학과': ['사회과학대학', '사회계열'],
    '사회복지학과': ['사회과학대학', '사회계열'],
    '사회학과': ['사회과학대학', '사회계열'],
    '문화인류학과': ['사회과학대학', '사회계열'],
    '언론홍보영상학부': ['사회과학대학', '사회계열'],

    # 음악대학
    '교회음악과': ['음악대학', '예체능계열'],
    '성악과': ['음악대학', '예체능계열'],
    '기악과': ['음악대학', '예체능계열'],
    '작곡과': ['음악대학', '예체능계열'],

    # 생활과학대학
    '의류환경학과': ['생활과학대학', '자연계열', '사회계열'],
    '식품영양학과': ['생활과학대학', '자연계열'],
    '실내건축학과': ['생활과학대학', '예체능계열', '이공계열'],
    '아동가족학과': ['생활과학대학', '사회계열'],
    '통합디자인학과': ['생활과학대학', '예체능계열'],

    # 교육과학대학
    '교육학부': ['교육과학대학', '사회계열'],
    '체육교육학과': ['교육과학대학', '예체능계열'],
    '스포츠응용산업학과': ['교육과학대학', '예체능계열', '사회계열'],

    # 의과대학
    '의예과': ['의과대학', '의학계열'],
    '의학과': ['의과대학', '의학계열'],

    # 치과대학
    '치의예과': ['치과대학', '의학계열'],
    '치의학과': ['치과대학', '의학계열'],

    # 간호대학
    '간호학과': ['간호대학', '의학계열'],

    # 약학대학
    '약학과': ['약학대학', '의학계열'],

    # 언더우드국제대학 (UIC)
    '언더우드학부': ['언더우드국제대학', '인문계열', '사회계열'],
    '융합인문사회계열': ['언더우드국제대학', '인문계열', '사회계열'],
    '융합과학공학계열': ['언더우드국제대학', '이공계열', 'IT계열', 'ICT 분야'], 

    # 글로벌인재대학
    '글로벌인재학부': ['글로벌인재대학', '인문계열', '사회계열'],
}


# [신규] 비교 로직 2번 수정을 위해 DEPARTMENT_MAP의 모든 값을 Set으로 미리 만듦
KNOWN_DEPT_KEYWORDS = set(k.lower() for k in DEPARTMENT_MAP.keys())
KNOWN_COLLEGE_KEYWORDS = set(c.lower() for v in DEPARTMENT_MAP.values() for c in v)
ALL_DEPT_KEYWORDS = KNOWN_DEPT_KEYWORDS.union(KNOWN_COLLEGE_KEYWORDS)


# =========================
# 3) 반환 타입/코드
# =========================

CheckStatus = Literal['PASS', 'FAIL', 'VERIFY']

@dataclass
class CheckResult:
    status: CheckStatus
    reason_code: str
    message: str
    is_required: bool = True
    confidence: float = 1.0  # 추출 신뢰도(0~1)

# [수정] 표준 코드 → 한글 메시지 템플릿
REASON_TEMPLATES = {
    "GPA_FAIL": "학점 미달",
    "LANG_FAIL_SCORE": "어학 요건 미충족",
    "DEPT_FAIL_MISMATCH": "전공 요건 불일치",
    "GRADE_FAIL_LEVEL": "학위(학부/대학원) 요건 불일치",
    "GRADE_FAIL_SEMESTER": "학기 범위 불충족",
    "GRADE_FAIL_YEAR": "학년 요건 불충족",
    "INCOME_FAIL_CAP": "소득분위 요건 초과",
    "GENDER_FAIL": "성별 요건 불일치",
    "MILITARY_FAIL": "병역 요건 불일치",
    "OTHER_VERIFY": "기타 조건 확인 필요",
    # VERIFY (MISSING)
    "GPA_MISSING": "GPA 정보 없음 (확인 필요)",
    "LANG_SCORE_MISSING": "어학 점수 정보 없음 (확인 필요)",
    "MAJOR_MISSING": "전공 정보 없음 (확인 필요)",
    "GRADE_MISSING": "학년/학기 정보 없음 (확인 필요)",
    "INCOME_MISSING": "소득분위 정보 없음 (확인 필요)",
    "MILITARY_MISSING": "병역 정보 없음 (확인 필요)",
    "GENDER_MISSING": "성별 정보 없음 (확인 필요)",
    # VERIFY (OTHER)
    "INCOME_VERIFY_AMBIGUOUS": "소득 요건 확인 필요 (예: '경제 사정')",
    "GRADE_VERIFY_AMBIGUOUS": "학년 요건 확인 필요 (예: '성실한 학생')",
    "DEPT_VERIFY_AMBIGUOUS": "전공 요건 확인 필요 (예: '관련 분야 학생')",
    "INCOME_VERIFY_RECIPIENT": "수급자/가계곤란 여부 확인 필요",
    "LANG_VERIFY_FLUENCY": "어학 능통 여부 확인 필요",
    "LANG_VERIFY_COMPLEX": "복합 어학 요건(AND/OR 혼용) 확인 필요",
    "GRADE_VERIFY_COMPLEX": "복합 학년 요건 확인 필요 (예: 학년 + 학점)", # [신규]
}


# =========================
# 4) 정규식(사전 컴파일)
# =========================
RE_GPA_NUM = re.compile(r'(\d(?:\.\d{1,2})?)')
# [수정됨] (?!<\d) 추가: "2025-2학기"가 "5~2학기"로 오탐지되는 것을 방지
RE_GRADE_RANGE = re.compile(r'(?<!\d)(\d)[\s~.~-]+(\d)\s*학기')
RE_GRADE_ABOVE = re.compile(r'(\d)\s*학년\s*이상')
RE_ANY_DEPT_ANYONE = re.compile(r'전\s*(계열|학과)|모든\s*학과|누구나|학과\s*무관')
RE_OR = re.compile(r'\b(또는|or|OR)\b')
RE_AND = re.compile(r'\b(그리고|및|and|AND)\b')
RE_PAREN = re.compile(r'[\(\)]')

# [신규] 비교 로직 2번 수정을 위한 키워드
RE_GRADE_KEYWORDS = re.compile(r'(학년|학기|학부|대학원|재학생|휴학생)')
RE_INCOME_KEYWORDS = re.compile(r'(분위|수급자|가계곤란|경제사정)')
# [신규] 복합 요건 감지용 (target_audience)
RE_GPA_KEYWORDS = re.compile(r'(학점|gpa)', re.IGNORECASE)
RE_DEPT_KEYWORDS = re.compile(r'(학과|전공|계열|대학)') # [FIX] "대학" 추가


# 언어 요구 추출
RE_LANG_REQ = re.compile(
    r'(TOEIC|TOEFL|IELTS|JLPT|HSK|OPI[cC]|TEPS|TOEIC\s*SPEAKING|토익|토플|아이엘츠|오픽|텝스|토익\s*스피킹)'
    r'[\s:/-]*'
    r'([0-9]+\.?[0-9]*|N[1-5]|AL|IH|IM[1-3]|IL|NH|NM|NL|[1-6]급|[A-Za-z ]{2,})',
    re.IGNORECASE
)

# =========================
# 5) 공통 유틸
# =========================

def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        s = dt_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s) 
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        m = re.search(r'(\d{4})[.\s/-]+(\d{1,2})[.\s/-]+(\d{1,2})', dt_str)
        if m:
            try:
                y, mth, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                dt = datetime(y, mth, d, 23, 59, 59, tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

# =========================
# 6) 프로필 정규화
# =========================

def _normalize_user_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    norm = dict(profile or {})
    norm['gender'] = profile.get('gender')
    norm['age'] = profile.get('age')
    norm['major'] = profile.get('major') or ""
    norm['grade'] = profile.get('grade')
    norm['keywords'] = set(profile.get('keywords', []))
    norm['military_service'] = profile.get('military_service')
    norm['income_bracket'] = profile.get('income_bracket')
    norm['gpa'] = profile.get('gpa')
    norm['gpa_scale'] = float(profile.get('gpa_scale') or DEFAULT_GPA_SCALE)

    # 언어 정규화: 숫자형은 max로 나눠 0~1, 등급형은 서열/최대서열
    norm_scores: Dict[str, float] = {}
    raw_scores = profile.get('language_scores') or {}
    for raw_key, value in raw_scores.items():
        key = LANGUAGE_KEY_MAP.get(raw_key.lower().strip(), None)
        if not key:
            continue
        # 등급형
        if key in LANGUAGE_LEVEL_MAP and isinstance(value, str):
            ordinal = LANGUAGE_LEVEL_MAP[key].get(value.upper().strip())
            if ordinal is None:
                continue
            norm_scores[key] = float(ordinal) / float(LANG_LEVEL_MAX[key])
        else:
            try:
                val = float(re.sub(r'[^0-9.]', '', str(value)))
            except Exception:
                continue
            maxv = LANGUAGE_MAX_SCORE.get(key)
            if not maxv or maxv <= 0:
                continue
            norm_scores[key] = _clamp(val / maxv, 0.0, 1.0)
    norm['norm_lang_scores'] = norm_scores

    # 학부/대학원 + 학기 추정
    level, semester = 'N/A', 0
    g = norm.get('grade')
    if isinstance(g, int):
        if 1 <= g <= 4:
            level = '학부'
            semester = (g - 1) * 2 + 1
        elif g >= 5:
            level = '대학원'
            semester = (g - 5) * 2 + 1
    norm['norm_level'] = level
    norm['norm_semester'] = semester
    return norm

# =========================
# 7) 요건 파싱·태깅·신뢰도
# =========================

@dataclass
class Requirement:
    key: str
    text: str
    tag: Literal['required', 'preferred', 'optional']
    confidence: float

def _infer_tag_and_conf(text_or_obj: Union[str, Dict[str, Any]], default_tag='required') -> Tuple[str, float, str]:
    """
    qualifications의 값이 문자열 또는 {text, tag, confidence}일 수 있다는 가정.
    문자열만 있으면 '우대' 포함 시 preferred, 그 외 required.
    """
    if isinstance(text_or_obj, dict):
        txt = (text_or_obj.get('text') or '').strip()
        tag = text_or_obj.get('tag') or default_tag
        conf = float(text_or_obj.get('confidence') or 1.0)
        tag = tag if tag in ('required', 'preferred', 'optional') else default_tag
        return tag, _clamp(conf, 0.0, 1.0), txt
    txt = (text_or_obj or '').strip()
    tag = 'preferred' if ('우대' in txt or 'preferred' in txt.lower()) else default_tag
    return tag, 1.0, txt

# =========================
# 8) 개별 비교기 (한글 메시지 반환)
# =========================

def _check_gpa(user_gpa: Optional[float], user_scale: float, req: Requirement) -> CheckResult:
    # [신규] "N/A" 또는 "해당 없음"은 요건이 없는 것이므로 PASS 처리
    txt_raw = req.text.lower().strip()
    if txt_raw in ("n/a", "해당없음", "무관"):
        return CheckResult('PASS', 'GPA_PASS_NONE', "학점 요건 무관 (충족)", req.tag=='required', req.confidence)

    if user_gpa is None:
        return CheckResult('VERIFY', 'GPA_MISSING', REASON_TEMPLATES['GPA_MISSING'], req.tag=='required', req.confidence)
    
    req_scale = 4.5
    if '4.3' in req.text:
        req_scale = 4.3
    m = RE_GPA_NUM.search(req.text)
    if not m:
        # [FIX] GPA 숫자를 못찾으면 VERIFY
        return CheckResult('VERIFY', 'GPA_VERIFY_AMBIGUOUS', f"학점 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)
    try:
        req_gpa_raw = float(m.group(1))
    except Exception:
        return CheckResult('VERIFY', 'GPA_VERIFY_AMBIGUOUS', f"학점 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)
    
    user_gpa_on_req_scale = (user_gpa / max(user_scale, 0.1)) * req_scale
    if user_gpa_on_req_scale + 1e-9 < req_gpa_raw:
        return CheckResult('FAIL', 'GPA_FAIL',
                           f"학점 미달 (요구≥{req_gpa_raw:.2f}/{req_scale:.1f} | 보유≈{user_gpa_on_req_scale:.2f}/{req_scale:.1f})",
                           req.tag=='required', req.confidence)
    return CheckResult('PASS', 'GPA_PASS', "학점 요건 충족", req.tag=='required', req.confidence)

def _check_grade_level(user_level: str, user_semester: int, req: Requirement) -> CheckResult:
    # [신규] "N/A" 또는 "해당 없음"은 요건이 없는 것이므로 PASS 처리
    t_raw = req.text.lower().strip()
    if t_raw in ("n/a", "해당없음", "무관"):
        return CheckResult('PASS', 'GRADE_PASS_NONE', "학년/학기 요건 무관 (충족)", req.tag=='required', req.confidence)

    if user_semester == 0:
        return CheckResult('VERIFY', 'GRADE_MISSING', REASON_TEMPLATES['GRADE_MISSING'], req.tag=='required', req.confidence)
    
    t = req.text.replace(" ", "").lower()
    
    # [수정됨] '졸업' 관련 요건은 복합 요건으로 VERIFY 처리
    if '졸업' in t: # "졸업", "졸업예정자", "졸업가능자" 등
        return CheckResult('VERIFY', 'GRADE_VERIFY_COMPLEX', f"복합 요건 확인 필요 (졸업): {req.text}", req.tag=='required', req.confidence)

    # [FIX] 애매한 요건(예: "성실한") VERIFY 처리
    if not RE_GRADE_KEYWORDS.search(t):
        # [수정 요청] 학년/학기 키워드가 없는 애매한 요건은 '복합 요건'으로 처리
        return CheckResult('VERIFY', 'GRADE_VERIFY_COMPLEX', f"복합 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)

    # [FIX] 복합 요건(예: "9학점 이수자") VERIFY 처리
    if RE_GPA_KEYWORDS.search(t) or RE_DEPT_KEYWORDS.search(t):
        return CheckResult('VERIFY', 'GRADE_VERIFY_COMPLEX', f"복합 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)


    if '대학원' in t and user_level != '대학원':
        return CheckResult('FAIL', 'GRADE_FAIL_LEVEL', "대학원생 대상", req.tag=='required', req.confidence)
    if ('학부' in t or '학년' in t) and user_level != '학부':
        return CheckResult('FAIL', 'GRADE_FAIL_LEVEL', "학부생 대상", req.tag=='required', req.confidence)
    
    pass_all = True
    fail_reasons = []

    if '3학기이상' in t or '3학기 이상' in t:
         if user_semester < 3:
             pass_all = False
             fail_reasons.append(f"학기 미충족 (요구: 3학기 이상 | 현재: {user_semester}학기)")
    if '6학기이수전' in t or '6학기 이수 전' in t:
         if user_semester > 6:
             pass_all = False
             fail_reasons.append(f"학기 미충족 (요구: 6학기 이수 전 | 현재: {user_semester}학기)")
    if ('2학년' in t or '3학년' in t):
        is_2nd = (3 <= user_semester <= 4)
        is_3rd = (5 <= user_semester <= 6)
        if not (is_2nd or is_3rd):
             pass_all = False
             fail_reasons.append(f"학년 미충족 (요구: 2-3학년 | 현재: {user_semester}학기)")
    
    rt = req.text 
    m = RE_GRADE_RANGE.search(rt)
    if m:
        try:
            min_sem, max_sem = int(m.group(1)), int(m.group(2))
            if not (min_sem <= user_semester <= max_sem):
                 pass_all = False
                 fail_reasons.append(f"학기 미충족 (요구: {min_sem}~{max_sem}학기 | 현재: {user_semester}학기)")
        except Exception:
             pass 
                 
    m2 = RE_GRADE_ABOVE.search(rt)
    if m2:
        try:
            min_grade = int(m2.group(1))
            min_sem_req = (min_grade - 1) * 2 + 1
            if user_semester < min_sem_req:
                 pass_all = False
                 fail_reasons.append(f"학년 미충족 (요구: {min_grade}학년 이상 | 현재: {user_semester}학기)")
        except Exception:
            pass

    if pass_all:
        return CheckResult('PASS', 'GRADE_PASS', "학년/학기 요건 충족", req.tag=='required', req.confidence)
    else:
        return CheckResult('FAIL', 'GRADE_FAIL_SEMESTER', "; ".join(fail_reasons), req.tag=='required', req.confidence)


def _check_department(user_major: str, req: Requirement) -> CheckResult:
    # [신규] "N/A" 또는 "해당 없음"은 요건이 없는 것이므로 PASS 처리
    txt_raw = req.text.lower().strip()
    if txt_raw in ("n/a", "해당없음", "무관"):
        return CheckResult('PASS', 'DEPT_PASS_NONE', "전공 요건 무관 (충족)", req.tag=='required', req.confidence)

    if not user_major:
        return CheckResult('VERIFY', 'MAJOR_MISSING', REASON_TEMPLATES['MAJOR_MISSING'], req.tag=='required', req.confidence)
    
    txt = req.text.lower() # 예: "의과대학"
    
    if RE_ANY_DEPT_ANYONE.search(txt):
        return CheckResult('PASS', 'DEPT_PASS_ANY', "전공 무관 (충족)", req.tag=='required', req.confidence)
    
    # [FIX] 사용자의 전공명 + 매핑된 그룹
    groups = DEPARTMENT_MAP.get(user_major, []) + [user_major]
    
    for g in groups:
        if g.lower() in txt: 
            return CheckResult('PASS', 'DEPT_PASS', f"전공 일치 (충족: {g})", req.tag=='required', req.confidence)
    
    # [FIX] 애매한 요건(예: "성실한") VERIFY 처리
    # (학과, 대학, 전공, 계열) 키워드도 없고, 아는 키워드(공과대학 등)도 없으면
    if not RE_DEPT_KEYWORDS.search(txt) and not any(k in txt for k in ALL_DEPT_KEYWORDS):
        return CheckResult('VERIFY', 'DEPT_VERIFY_AMBIGUOUS', f"전공 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)

    # [FIX] 일치하는 것이 없으면 무조건 FAIL
    return CheckResult('FAIL', 'DEPT_FAIL_MISMATCH', f"전공 미충족 (요구: {req.text} | 보유: {user_major})", req.tag=='required', req.confidence)


def _check_income(user_income: Optional[int], req: Requirement) -> CheckResult:
    # [수정] "N/A" 또는 "해당 없음"은 요건이 없는 것이므로 PASS 처리
    txt_raw = req.text.replace(" ", "").lower()
    if txt_raw in ("n/a", "해당없음", "무관"):
        return CheckResult('PASS', 'INCOME_PASS_NONE', "소득 요건 무관 (충족)", req.tag=='required', req.confidence)

    if user_income is None:
        return CheckResult('VERIFY', 'INCOME_MISSING', REASON_TEMPLATES['INCOME_MISSING'], req.tag=='required', req.confidence)

    txt = req.text.replace(" ", "") # 원본 txt 사용

    # [FIX] 애매한 요건(예: "경제사정") VERIFY 처리
    if not RE_INCOME_KEYWORDS.search(txt):
        return CheckResult('VERIFY', 'INCOME_VERIFY_AMBIGUOUS', f"소득 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)

    m = re.search(r'(\d+)[\s]*분위', txt)
    if m:
        try:
            cap = int(m.group(1))
            if user_income > cap:
                return CheckResult('FAIL', 'INCOME_FAIL_CAP',
                                   f"소득분위 초과 (요구≤{cap}분위 | 현재 {user_income}분위)",
                                   req.tag=='required', req.confidence)
            return CheckResult('PASS', 'INCOME_PASS', "소득분위 요건 충족", req.tag=='required', req.confidence)
        except Exception:
            pass
    if '기초생활수급' in txt or '가계곤란' in txt:
        return CheckResult('VERIFY', 'INCOME_VERIFY_RECIPIENT', REASON_TEMPLATES['INCOME_VERIFY_RECIPIENT'], req.tag=='required', req.confidence)
    
    # [FIX] "경제사정" 등 키워드는 찾았으나, 명확한 기준(X분위)이 없으면 VERIFY
    return CheckResult('VERIFY', 'INCOME_VERIFY_AMBIGUOUS', f"소득 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)

def _check_simple_text(user_value: Optional[str], req: Requirement, field_name: str) -> CheckResult:
    t = req.text.lower().strip() # requirement text
    
    # [수정] "N/A" 및 "무관" 키워드를 맨 앞에서 처리
    if t in ("n/a", "해당없음", "무관", "없음", "제한없음"):
        return CheckResult('PASS', f'{field_name.upper()}_PASS_NONE', f"{field_name} 요건 무관 (충족)", req.tag=='required', req.confidence)

    if not user_value:
        code = f'{field_name.upper()}_MISSING'
        return CheckResult('VERIFY', code, REASON_TEMPLATES.get(code, f"{field_name} 정보 없음"), req.tag=='required', req.confidence)
    
    u = user_value.lower()
    if field_name == 'military_service' and (('군필' in t) or ('면제' in t)) and u == 'pending':
        return CheckResult('FAIL', 'MILITARY_FAIL', "병역 요건 미충족(군필/면제 요구)", req.tag=='required', req.confidence)
    if field_name == 'gender' and (('여성' in t) or ('여학생' in t)) and u == 'male':
        return CheckResult('FAIL', 'GENDER_FAIL', "성별 요건 불일치(여성 대상)", req.tag=='required', req.confidence)
    
    # [FIX] 애매한 텍스트는 VERIFY
    if field_name == 'military_service' and not ('군필' in t or '면제' in t or '군휴학생' in t): # [수정] '군휴학생'도 통과
         return CheckResult('VERIFY', 'MILITARY_VERIFY_AMBIGUOUS', f"병역 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)
    if field_name == 'gender' and not ('여성' in t or '남성' in t):
         return CheckResult('VERIFY', 'GENDER_VERIFY_AMBIGUOUS', f"성별 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)

    return CheckResult('PASS', f'{field_name.upper()}_PASS', f"{field_name} 요건 충족", req.tag=='required', req.confidence)

def _normalize_lang_key(s: str) -> Optional[str]:
    k = LANGUAGE_KEY_MAP.get(s.lower().strip())
    return k

def _norm_required_value(test_key: str, val: str) -> Optional[float]:
    """요구 텍스트의 점수/등급을 0~1로 정규화."""
    if test_key in LANGUAGE_LEVEL_MAP:
        v = LANGUAGE_LEVEL_MAP[test_key].get(val.upper().strip())
        if v is None:
            return None
        return v / LANG_LEVEL_MAX[test_key]
    try:
        num = float(re.sub(r'[^0-9.]', '', val))
    except Exception:
        return None
    maxv = LANGUAGE_MAX_SCORE.get(test_key)
    if not maxv:
        return None
    return _clamp(num / maxv, 0.0, 1.0)

def _check_language(norm_user_scores: Dict[str, float], req: Requirement) -> CheckResult:
    txt = req.text
    
    # [신규] "N/A" 또는 "해당 없음"은 요건이 없는 것이므로 PASS 처리
    txt_raw = req.text.lower().strip()
    if txt_raw in ("n/a", "해당없음", "무관"):
        return CheckResult('PASS', 'LANG_PASS_NONE', "어학 요건 무관 (충족)", req.tag=='required', req.confidence)

    requirements = RE_LANG_REQ.findall(txt)
    if not requirements:
        if "우대" in txt:
            return CheckResult('PASS', 'LANG_PASS_PREFER', "어학 (우대/충족 간주)", req.tag=='required', req.confidence)
        if "능통" in txt or "fluent" in txt.lower():
            return CheckResult('VERIFY', 'LANG_VERIFY_FLUENCY', REASON_TEMPLATES['LANG_VERIFY_FLUENCY'], req.tag=='required', req.confidence)
        # [FIX] 애매한 텍스트(예: "영어 가능자") VERIFY
        if "어학" in txt or "영어" in txt or "외국어" in txt:
            return CheckResult('VERIFY', 'LANG_VERIFY_AMBIGUOUS', f"어학 요건 확인 필요: {req.text}", req.tag=='required', req.confidence)
        
        return CheckResult('PASS', 'LANG_PASS_NONE', "어학 요건 없음 (충족)", req.tag=='required', req.confidence)

    atoms: List[bool] = []
    missing: Set[str] = set()
    fail_msgs: List[str] = []

    for raw_name, raw_req in requirements:
        key = _normalize_lang_key(raw_name)
        if not key:
            continue
        req_norm = _norm_required_value(key, raw_req)
        if req_norm is None:
            continue
        user_val = norm_user_scores.get(key)
        if user_val is None:
            atoms.append(False)
            missing.add(key)
            continue
        if user_val + 1e-9 >= req_norm:
            atoms.append(True)
        else:
            atoms.append(False)
            fail_msgs.append(f"{key} 미달(요구≈{raw_req} | 보유 정규화≈{user_val:.2f})")

    # [FIX] AND/OR 복합 로직 감지
    has_and = bool(RE_AND.search(txt))
    has_or = bool(RE_OR.search(txt))

    if has_and and has_or:
        return CheckResult('VERIFY', 'LANG_VERIFY_COMPLEX', REASON_TEMPLATES['LANG_VERIFY_COMPLEX'], req.tag=='required', req.confidence)

    # [FIX] 기본값을 OR (any)로 변경 (더 일반적인 케이스)
    is_and = has_and
    final_pass = all(atoms) if is_and else any(atoms) if atoms else False # [FIX] (atoms가 비어있으면 False)

    if final_pass:
        return CheckResult('PASS', 'LANG_PASS', "어학 요건 충족", req.tag=='required', req.confidence)
    else:
        if missing:
            return CheckResult('VERIFY', 'LANG_SCORE_MISSING',
                               f"어학 점수 정보 없음 (요구 항목: {', '.join(sorted(missing))})",
                               req.tag=='required', req.confidence)
        return CheckResult('FAIL', 'LANG_FAIL_SCORE',
                           f"어학 요건 미충족 ({'; '.join(fail_msgs)})",
                           req.tag=='required', req.confidence)

# =========================
# 9) 메인 비교
# =========================

def check_suitability(user_profile: Dict[str, Any], notice_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    사용자 프로필 vs 공지(AI 추출)를 비교하여 결과 반환.
    [수정됨] 퍼센트(match_percentage) 및 점수 계산 로직 완전 제거.
    """
    
    try:
        norm = _normalize_user_profile(user_profile)

        # 1) 비교 가능한 모든 함수 맵 정의
        check_map = {
            'gpa_min': lambda r: _check_gpa(norm.get('gpa'), norm.get('gpa_scale'), r),
            'grade_level': lambda r: _check_grade_level(norm.get('norm_level'), norm.get('norm_semester'), r),
            'target_audience': lambda r: _check_grade_level(norm.get('norm_level'), norm.get('norm_semester'), r), 
            'department': lambda r: _check_department(norm.get('major'), r),
            'income_status': lambda r: _check_income(norm.get('income_bracket'), r),
            'language_requirements_text': lambda r: _check_language(norm.get('norm_lang_scores', {}), r),
            'military_service': lambda r: _check_simple_text(norm.get('military_service'), r, 'military_service'),
            'gender': lambda r: _check_simple_text(norm.get('gender'), r, 'gender'),
            # 'other'는 check_map에 의도적으로 포함시키지 않음. (항상 VERIFY)
        }
        CHECKABLE_KEYS = set(check_map.keys())


        # 2) 비교할 요건(Requirement) 목록 구성
        potential_reqs: Dict[str, Any] = {}
        quals_dict = notice_json.get("qualifications")
        if isinstance(quals_dict, dict):
            potential_reqs.update(quals_dict)
            
        # [수정] qualifications 밖의 키도 포함 (하위 호환성)
        for key in CHECKABLE_KEYS:
            if key not in potential_reqs and key in notice_json and notice_json[key]:
                potential_reqs[key] = notice_json[key]
        
        # 'other'는 qualifications에만 있을 수 있음
        if 'other' not in potential_reqs and quals_dict and quals_dict.get('other'):
             potential_reqs['other'] = quals_dict.get('other')


        # 3) "정보성 공지" 판단
        empty_values = [None, "N/A", "", "해당 없음", "정보 없음"]
        if not potential_reqs or not any(v not in empty_values for v in potential_reqs.values()):
            return {
                "eligibility": "ELIGIBLE",
                "suitable": True,
                "criteria_results": {
                    "pass": ["정보성 공지 (특별한 자격 요건 없음)"],
                    "fail": [],
                    "verify": []
                },
                "reason_codes": ["INFO_NOTICE"],
                "reasons_human": ["정보성 공지 (특별한 자격 요건 없음)"],
                "missing_info": [],
            }

        # 4) Requirement 객체 생성
        reqs: Dict[str, Requirement] = {}
        for k, v in potential_reqs.items():
            if v is None: continue
            tag, conf, txt = _infer_tag_and_conf(v)
            if not txt: continue # 빈 문자열은 무시
            
            txt_lower = txt.strip().lower()
            if txt_lower in ("n/a", "해당없음", "무관", "정보 없음"): 
                continue 
            
            reqs[k] = Requirement(k, txt, tag, conf)


        # 5) 항목별 평가
        reasons: List[CheckResult] = []
        for key, req in reqs.items():
            check_fn = check_map.get(key) 
            
            if not check_fn:
                # (예: other)는 VERIFY
                reasons.append(CheckResult('VERIFY', 'OTHER_VERIFY', f"기타 정보 확인 필요: {req.text}",
                                           req.tag=='optional', 0.0))
                continue

            res = check_fn(req)
            reasons.append(res)
        
        # 6) [수정] 라벨 결정을 위한 확인 (점수 계산 완전 제거)
        
        # 6-1. '필수' 요건 중 '실패(FAIL)'가 있는지 확인
        required_fail = any(r.status == 'FAIL' and r.is_required for r in reasons)
        
        # 6-2. '정보 누락(VERIFY)'이 있는지 확인 (OTHER_VERIFY 포함)
        has_missing_info = any(r.status == 'VERIFY' for r in reasons)


        # 7) 라벨 결정
        if required_fail:
            eligibility = 'INELIGIBLE'
            suitable = False
        elif has_missing_info:
            eligibility = 'BORDERLINE'
            suitable = True # (부적합은 아니므로)
        else:
            # (필수 FAIL도 없고, VERIFY도 없으면)
            eligibility = 'ELIGIBLE'
            suitable = True

        # 8) 설명/결손 정보 및 3가지 조건 목록 생성
        reason_codes = sorted(set(r.reason_code for r in reasons if r.reason_code))
        
        pass_conditions = []
        fail_conditions = []
        verify_conditions = []
        missing_info_codes = set()
        human_msgs_set = set() # (reasons_human 생성용)

        for r in reasons:
            msg = r.message
            if not msg:
                msg = REASON_TEMPLATES.get(r.reason_code, r.reason_code)
            
            if r.status == 'PASS':
                pass_conditions.append(msg) 

            elif r.status == 'FAIL':
                fail_conditions.append(msg)
                human_msgs_set.add(msg)
                
            elif r.status == 'VERIFY':
                verify_conditions.append(msg)
                human_msgs_set.add(msg)
                if r.reason_code.endswith('_MISSING'):
                    missing_info_codes.add(r.reason_code.split('_MISSING')[0].lower())
        
        reasons_human_final = list(human_msgs_set)
        if not reasons_human_final and eligibility == 'ELIGIBLE':
            reasons_human_final.append("모든 자격 요건에 부합합니다.")
        elif not reasons_human_final and eligibility == 'BORDERLINE':
            reasons_human_final.append("일부 요건은 충족하였으나, 확인/누락된 정보가 있습니다.")


        return {
            "eligibility": eligibility,
            "suitable": suitable,
            
            "criteria_results": {
                "pass": sorted(set(pass_conditions)),
                "fail": sorted(set(fail_conditions)),
                "verify": sorted(set(verify_conditions))
            },
            
            "reason_codes": reason_codes,
            "reasons_human": sorted(reasons_human_final),
            "missing_info": sorted(missing_info_codes),
        }

    except Exception as e:
        logger.error(f"[comparison_logic] 오류: {e}", exc_info=True)
        return {
            "eligibility": "BORDERLINE",
            "suitable": True,
            
            "criteria_results": {
                "pass": [],
                "fail": [],
                "verify": ["적합도 비교 중 오류가 발생했습니다. 직접 확인해주세요."]
            },

            "reason_codes": ["COMPARISON_ERROR"],
            "reasons_human": ["적합도 비교 중 오류가 발생했습니다. 직접 확인해주세요."],
            "missing_info": [],
        }