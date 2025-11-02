# comparison_logic.py 
# - 스키마 정합성 (auth_schemas v2), JSONB language_scores 다중 시험 지원
# - 필수/우대/선택 태깅 + confidence 가중치 반영
# - 논리 연산(AND/OR/괄호) 1단계 지원
# - 어학 점수 표준화/정규화(숫자형·등급형 혼합)
# - GPA 스케일 환산(4.3/4.5 스케일 혼재 대응)
# - 전공 매칭 유사도(간단 trigram 유사도 + 매핑 테이블)
# - 키워드 Jaccard 보너스(0.7~1.1)
# - 가중합 점수(필수/우대/선택 0.5/0.3/0.2) + 컷오프(0.8/0.5)
# - 시간 가중치(마감 임박/신선도/학기 시점) + 클램프
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

# 항목 가중치 (필수/우대/선택)
CRITERIA_WEIGHTS = {
    "required": 0.50,
    "preferred": 0.30,
    "optional": 0.20,
}

# 라벨 컷오프 (가중 점수 기준)
CUTOFFS = {
    "eligible": 0.80,    # 0.80 이상 → ELIGIBLE
    "borderline": 0.50,  # 0.50~0.80 → BORDERLINE
}

# Temporal 가중치 (deadline/freshness/term)
TEMPORAL_WEIGHTS = {
    "deadline": {
        "passed_penalty": 0.75,
        "gt_30d": 1.00,
        "15_30d": 1.03,
        "7_15d": 1.06,
        "3_7d": 1.12,
        "0_3d": 1.20
    },
    "freshness": {
        "gt_60d": 0.92,
        "30_60d": 0.96,
        "7_30d": 1.00,
        "0_7d": 1.04
    },
    "term": {
        "perfect": 1.08,
        "ok": 1.02,
        "far": 0.97,
        "unknown": 1.00
    },
    "cap": (0.80, 1.30)
}

# 키워드 보너스 (Jaccard) → 0.7 ~ 1.1 사이로 선형 맵핑
JACCARD_BOUNDS = (0.0, 1.0)
KEYWORD_WEIGHT_BOUNDS = (0.70, 1.10)

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
# 2) 전공 매핑/유사도
# =========================

DEPARTMENT_MAP = {
    '경영학과': ['경영대학', '상경대학', '상경‧경영대학', '경영/경제 계열'],
    '경제학부': ['경제대학', '상경대학', '상경‧경영대학', '경영/경제 계열'],
    '응용통계학과': ['상경대학', '상경‧경영대학', '통계데이터사이언스학과', '통계학과'],
    '컴퓨터과학과': ['공과대학', '첨단컴퓨팅학부', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열', '인공지능융합대학'],
    '인공지능학과': ['인공지능융합대학', '첨단컴퓨팅학부', 'AI·ICT 관련 학과', 'IT 계열', '이공 계열'],
    # ... 필요 시 확장
}

def _trigram_similarity(a: str, b: str) -> float:
    """아주 간단한 trigram 유사도 (0~1). 성능 목적상 경량화."""
    a = (a or "").lower()
    b = (b or "").lower()
    if not a or not b:
        return 0.0
    def trigrams(s: str) -> Set[str]:
        return {s[i:i+3] for i in range(len(s)-2)} if len(s) >= 3 else {s}
    A, B = trigrams(a), trigrams(b)
    inter = len(A & B)
    union = len(A | B) or 1
    return inter / union

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

# 표준 코드 → 기본 메시지 템플릿 (UI에서 아이콘/번역 매핑 가능)
REASON_TEMPLATES = {
    "GPA_FAIL": "학점 미달",
    "LANG_FAIL_SCORE": "어학 요건 미충족",
    "LANG_SCORE_MISSING": "어학 점수 정보 없음",
    "DEPT_FAIL_MISMATCH": "전공 요건 불일치",
    "GRADE_FAIL_LEVEL": "학위(학부/대학원) 요건 불일치",
    "GRADE_FAIL_SEMESTER": "학기 범위 불충족",
    "GRADE_FAIL_YEAR": "학년 요건 불충족",
    "INCOME_FAIL_CAP": "소득분위 요건 초과",
    "GENDER_FAIL": "성별 요건 불일치",
    "MILITARY_FAIL": "병역 요건 불일치",
    "OTHER_VERIFY": "기타 조건 확인 필요",
}

# =========================
# 4) 정규식(사전 컴파일)
# =========================
RE_GPA_NUM = re.compile(r'(\d(?:\.\d{1,2})?)')
RE_GRADE_RANGE = re.compile(r'(\d)[\s~.~-]+(\d)\s*학기')
RE_GRADE_ABOVE = re.compile(r'(\d)\s*학년\s*이상')
RE_ANY_DEPT_ANYONE = re.compile(r'전\s*(계열|학과)|모든\s*학과|누구나|학과\s*무관')
RE_OR = re.compile(r'\b(또는|or|OR)\b')
RE_AND = re.compile(r'\b(그리고|및|and|AND)\b')
RE_PAREN = re.compile(r'[\(\)]')

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
        return None

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _current_term(now: datetime) -> Tuple[int, int]:
    m = now.month
    sem = 1 if 3 <= m <= 8 else 2
    return (now.year, sem)

def _parse_target_term(notice: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    tt = notice.get("target_term_ai")
    if isinstance(tt, str) and "-" in tt:
        try:
            y_str, s_str = tt.split("-", 1)
            return (int(y_str), int(s_str))
        except Exception:
            pass
    start_iso = _parse_iso(notice.get("start_at_ai"))
    if start_iso:
        return _current_term(start_iso)
    txt = ""
    quals = notice.get("qualifications") or {}
    gl = quals.get("grade_level") or ""
    if isinstance(gl, str):
        txt = gl
    t = txt.replace(" ", "").lower()
    now = datetime.now(timezone.utc)
    y_now, s_now = _current_term(now)
    if "1학기" in t or "봄" in t:
        return (y_now if s_now == 1 else y_now + 1, 1)
    if "2학기" in t or "가을" in t or "fall" in t:
        return (y_now if s_now == 2 else y_now, 2)
    if "내년" in t or "nextyear" in t:
        return (y_now + 1, s_now)
    return None

def _temporal_weight(notice: Dict[str, Any]) -> float:
    now = datetime.now(timezone.utc)
    # 1) deadline
    w_deadline = 1.0
    deadline_dt = _parse_iso(notice.get("deadline_ai")) or _parse_iso(notice.get("end_at_ai"))
    if deadline_dt:
        days = (deadline_dt - now).total_seconds() / 86400.0
        d = TEMPORAL_WEIGHTS["deadline"]
        if days < 0:
            w_deadline = d["passed_penalty"]
        elif days <= 3:
            w_deadline = d["0_3d"]
        elif days <= 7:
            w_deadline = d["3_7d"]
        elif days <= 15:
            w_deadline = d["7_15d"]
        elif days <= 30:
            w_deadline = d["15_30d"]
        else:
            w_deadline = d["gt_30d"]
    # 2) freshness
    w_fresh = 1.0
    created_dt = _parse_iso(notice.get("created_at"))
    if created_dt:
        age_days = (now - created_dt).total_seconds() / 86400.0
        f = TEMPORAL_WEIGHTS["freshness"]
        if age_days <= 7:
            w_fresh = f["0_7d"]
        elif age_days <= 30:
            w_fresh = f["7_30d"]
        elif age_days <= 60:
            w_fresh = f["30_60d"]
        else:
            w_fresh = f["gt_60d"]
    # 3) term
    w_term = 1.0
    tterm = _parse_target_term(notice)
    if tterm:
        y_now, s_now = _current_term(now)
        y_tar, s_tar = tterm
        steps = (y_tar - y_now) * 2 + (s_tar - s_now)
        t = TEMPORAL_WEIGHTS["term"]
        if steps in (0, 1):
            w_term = t["perfect"]
        elif 2 <= steps <= 3:
            w_term = t["ok"]
        elif steps >= 4 or steps < 0:
            w_term = t["far"]
        else:
            w_term = t["unknown"]
    else:
        w_term = TEMPORAL_WEIGHTS["term"]["unknown"]
    lo, hi = TEMPORAL_WEIGHTS["cap"]
    return _clamp(w_deadline * w_fresh * w_term, lo, hi)

def _jaccard_bonus(user_keywords: Set[str], notice_hashtags: Set[str]) -> float:
    if not user_keywords or not notice_hashtags:
        return 1.0
    inter = len(user_keywords & notice_hashtags)
    union = len(user_keywords | notice_hashtags) or 1
    jaccard = inter / union  # 0~1
    lo_j, hi_j = JACCARD_BOUNDS
    lo_w, hi_w = KEYWORD_WEIGHT_BOUNDS
    # 선형 맵핑
    k = (hi_w - lo_w) / (hi_j - lo_j) if (hi_j - lo_j) != 0 else 0
    return _clamp(lo_w + k * (jaccard - lo_j), lo_w, hi_w)

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
# 8) 개별 비교기
# =========================

def _check_gpa(user_gpa: Optional[float], user_scale: float, req: Requirement) -> CheckResult:
    if user_gpa is None:
        return CheckResult('VERIFY', 'GPA_MISSING', f"GPA 정보 없음 (요구: {req.text})", req.tag=='required', req.confidence)
    # 요구 스케일 탐지 (4.3/4.5)
    req_scale = 4.5
    if '4.3' in req.text:
        req_scale = 4.3
    # 요구 최소값 추출
    m = RE_GPA_NUM.search(req.text)
    if not m:
        return CheckResult('PASS', 'GPA_PARSE_FAIL', "", req.tag=='required', req.confidence)
    try:
        req_gpa_raw = float(m.group(1))
    except Exception:
        return CheckResult('PASS', 'GPA_PARSE_FAIL', "", req.tag=='required', req.confidence)
    # 사용자 GPA를 요구 스케일로 환산
    user_gpa_on_req_scale = (user_gpa / max(user_scale, 0.1)) * req_scale
    if user_gpa_on_req_scale + 1e-9 < req_gpa_raw:
        return CheckResult('FAIL', 'GPA_FAIL',
                           f"학점 미달 (요구≥{req_gpa_raw:.2f}/{req_scale:.1f} | 보유≈{user_gpa_on_req_scale:.2f}/{req_scale:.1f})",
                           req.tag=='required', req.confidence)
    return CheckResult('PASS', 'GPA_PASS', "", req.tag=='required', req.confidence)

def _check_grade_level(user_level: str, user_semester: int, req: Requirement) -> CheckResult:
    if user_semester == 0:
        return CheckResult('VERIFY', 'GRADE_MISSING', f"학년/학기 정보 없음 (요구: {req.text})", req.tag=='required', req.confidence)
    t = req.text.replace(" ", "").lower()
    if '대학원' in t and user_level != '대학원':
        return CheckResult('FAIL', 'GRADE_FAIL_LEVEL', "대학원생 대상", req.tag=='required', req.confidence)
    if ('학부' in t or '학년' in t) and user_level != '학부':
        return CheckResult('FAIL', 'GRADE_FAIL_LEVEL', "학부생 대상", req.tag=='required', req.confidence)
    rt = t
    m = RE_GRADE_RANGE.search(rt)
    if m:
        min_sem, max_sem = int(m.group(1)), int(m.group(2))
        if not (min_sem <= user_semester <= max_sem):
            return CheckResult('FAIL', 'GRADE_FAIL_SEMESTER',
                               f"학기 미충족 (요구: {min_sem}~{max_sem}학기 | 현재: {user_semester}학기)",
                               req.tag=='required', req.confidence)
        return CheckResult('PASS', 'GRADE_PASS', "", req.tag=='required', req.confidence)
    m2 = RE_GRADE_ABOVE.search(rt)
    if m2:
        min_grade = int(m2.group(1))
        min_sem_req = (min_grade - 1) * 2 + 1
        if user_semester < min_sem_req:
            return CheckResult('FAIL', 'GRADE_FAIL_YEAR',
                               f"학년 미충족 (요구: {min_grade}학년 이상 | 현재: {user_semester}학기)",
                               req.tag=='required', req.confidence)
        return CheckResult('PASS', 'GRADE_PASS', "", req.tag=='required', req.confidence)
    return CheckResult('PASS', 'GRADE_PASS_AMBIGUOUS', "", req.tag=='required', req.confidence)

def _check_department(user_major: str, req: Requirement) -> CheckResult:
    if not user_major:
        return CheckResult('VERIFY', 'MAJOR_MISSING', "전공 정보 없음", req.tag=='required', req.confidence)
    if RE_ANY_DEPT_ANYONE.search(req.text):
        return CheckResult('PASS', 'DEPT_PASS_ANY', "", req.tag=='required', req.confidence)
    groups = DEPARTMENT_MAP.get(user_major, [user_major])
    txt = req.text.lower()
    # 포함 매칭 + 유사도 병렬
    for g in groups:
        if g.lower() in txt:
            return CheckResult('PASS', 'DEPT_PASS', "", req.tag=='required', req.confidence)
        if _trigram_similarity(g, txt) >= 0.33:
            return CheckResult('PASS', 'DEPT_PASS_FUZZY', "", req.tag=='required', req.confidence)
    return CheckResult('FAIL', 'DEPT_FAIL_MISMATCH', f"전공 미충족 (요구: {req.text})", req.tag=='required', req.confidence)

def _check_income(user_income: Optional[int], req: Requirement) -> CheckResult:
    if user_income is None:
        return CheckResult('VERIFY', 'INCOME_MISSING', "소득분위 정보 없음", req.tag=='required', req.confidence)
    m = re.search(r'(\d+)[\s]*분위', req.text)
    if m:
        try:
            cap = int(m.group(1))
            if user_income > cap:
                return CheckResult('FAIL', 'INCOME_FAIL_CAP',
                                   f"소득분위 초과 (요구≤{cap}분위 | 현재 {user_income}분위)",
                                   req.tag=='required', req.confidence)
            return CheckResult('PASS', 'INCOME_PASS', "", req.tag=='required', req.confidence)
        except Exception:
            pass
    if '기초생활수급' in req.text or '가계곤란' in req.text:
        return CheckResult('VERIFY', 'INCOME_VERIFY_RECIPIENT', "수급자/가계곤란 여부 확인 필요", req.tag=='required', req.confidence)
    return CheckResult('PASS', 'INCOME_PASS_AMBIGUOUS', "", req.tag=='required', req.confidence)

def _check_simple_text(user_value: Optional[str], req: Requirement, field_name: str) -> CheckResult:
    if not user_value:
        return CheckResult('VERIFY', f'{field_name.upper()}_MISSING', f"{field_name} 정보 없음", req.tag=='required', req.confidence)
    t = req.text.lower()
    if re.search(r'무관|없음|제한없음', t):
        return CheckResult('PASS', f'{field_name.upper()}_PASS_ANY', "", req.tag=='required', req.confidence)
    u = user_value.lower()
    if field_name == 'military_service' and (('군필' in t) or ('면제' in t)) and u == 'pending':
        return CheckResult('FAIL', 'MILITARY_FAIL', "병역 요건 미충족(군필/면제 요구)", req.tag=='required', req.confidence)
    if field_name == 'gender' and (('여성' in t) or ('여학생' in t)) and u == 'male':
        return CheckResult('FAIL', 'GENDER_FAIL', "성별 요건 불일치(여성 대상)", req.tag=='required', req.confidence)
    return CheckResult('PASS', f'{field_name.upper()}_PASS', "", req.tag=='required', req.confidence)

def _normalize_lang_key(s: str) -> Optional[str]:
    k = LANGUAGE_KEY_MAP.get(s.lower().strip())
    return k

def _norm_required_value(test_key: str, val: str) -> Optional[float]:
    """요구 텍스트의 점수/등급을 0~1로 정규화."""
    if test_key in LANGUAGE_LEVEL_MAP:
        # 등급형
        v = LANGUAGE_LEVEL_MAP[test_key].get(val.upper().strip())
        if v is None:
            return None
        return v / LANG_LEVEL_MAX[test_key]
    # 숫자형
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
    requirements = RE_LANG_REQ.findall(txt)
    if not requirements:
        if "우대" in txt:
            return CheckResult('PASS', 'LANG_PASS_PREFER', "", req.tag=='required', req.confidence)
        if "능통" in txt or "fluent" in txt.lower():
            return CheckResult('VERIFY', 'LANG_VERIFY_FLUENCY', "어학 능통 여부 확인 필요", req.tag=='required', req.confidence)
        return CheckResult('PASS', 'LANG_PASS_NONE', "", req.tag=='required', req.confidence)

    # 괄호/AND/OR 처리 (간단 토큰화)
    expr = txt
    # 우선 개별 요구를 평가하여 원자항 TRUE/FALSE/UNKNOWN으로 치환
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

    # OR/AND 판단
    # 간단히: '또는/or'가 한 번이라도 있으면 OR 그룹으로, 아니면 AND
    is_or = bool(RE_OR.search(txt)) and not bool(RE_AND.search(txt))
    # 괄호가 복잡해도, 원자평균/any/all로 처리(간소화)
    final_pass = any(atoms) if is_or else all(atoms) if atoms else True

    if final_pass:
        return CheckResult('PASS', 'LANG_PASS', "", req.tag=='required', req.confidence)
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

    반환:
      - eligibility: 'ELIGIBLE' | 'BORDERLINE' | 'INELIGIBLE'
      - suitable: bool
      - reason_codes: List[str]
      - reasons_human: List[str]
      - missing_info: List[str]
      - match_percentage: float (0~100)
    """
    # 정보성 공지(요건 없음)
    quals = notice_json.get("qualifications")
    if not quals or not isinstance(quals, dict) or not any(v not in [None, "N/A", ""] for v in quals.values()):
        return {
            "eligibility": "ELIGIBLE",
            "suitable": True,
            "reason_codes": ["INFO_NOTICE"],
            "reasons_human": ["정보성 공지 (특별한 자격 요건 없음)"],
            "missing_info": [],
            "match_percentage": 100.0
        }

    try:
        norm = _normalize_user_profile(user_profile)

        # 1) 태깅/신뢰도 포함 요건 리스트 구성
        reqs: Dict[str, Requirement] = {}
        for k, v in quals.items():
            tag, conf, txt = _infer_tag_and_conf(v)
            # 공정성 가드레일: 성별/병역은 공지에 명시된 경우에만 비교
            if k in ('gender', 'military_service') and not txt:
                continue
            reqs[k] = Requirement(k, txt, tag, conf)

        # 2) 키별 비교기 맵
        check_map = {
            'gpa_min': lambda r: _check_gpa(norm.get('gpa'), norm.get('gpa_scale'), r),
            'grade_level': lambda r: _check_grade_level(norm.get('norm_level'), norm.get('norm_semester'), r),
            'department': lambda r: _check_department(norm.get('major'), r),
            'income_status': lambda r: _check_income(norm.get('income_bracket'), r),
            'language_requirements_text': lambda r: _check_language(norm.get('norm_lang_scores', {}), r),
            'military_service': lambda r: _check_simple_text(norm.get('military_service'), r, 'military_service'),
            'gender': lambda r: _check_simple_text(norm.get('gender'), r, 'gender'),
            # 필요 시 확장: degree, certificate 등
        }

        # 3) 항목별 평가 + 가중합 점수(필수/우대/선택 * confidence)
        weighted_sum = 0.0
        weight_total = 0.0

        reasons: List[CheckResult] = []

        for key, req in reqs.items():
            if not req.text or req.text == 'N/A':
                continue
            check_fn = check_map.get(key)
            if not check_fn:
                # 기타 조건은 확인 필요로 처리(신뢰도 반영)
                reasons.append(CheckResult('VERIFY', 'OTHER_VERIFY', f"기타 조건 확인 필요: {req.text}",
                                           req.tag=='required', req.confidence))
                continue

            res = check_fn(req)
            reasons.append(res)

            # 점수 계산
            w_tag = CRITERIA_WEIGHTS.get(req.tag, 0.2)
            w = w_tag * _clamp(req.confidence, 0.0, 1.0)
            weight_total += w

            if res.status == 'PASS':
                weighted_sum += w
            elif res.status == 'FAIL':
                # 필수 실패는 별도 처리(라벨 단계), 점수에서는 0으로 둠
                pass
            elif res.status == 'VERIFY':
                # 확인 필요는 0.5 배점 정도로 중립 반영해도 되지만, 여기서는 점수화X
                pass

        base_score = (weighted_sum / weight_total) if weight_total > 1e-9 else 1.0

        # 4) 키워드 Jaccard 보너스
        user_kw = norm.get('keywords', set())
        notice_kw = set(notice_json.get('hashtags_ai', []) or [])
        kw_bonus = _jaccard_bonus(user_kw, notice_kw)

        # 5) 시간 가중치
        temporal_w = _temporal_weight(notice_json)

        final_score = _clamp(base_score * kw_bonus * temporal_w, 0.0, 1.0)

        # 6) 라벨 결정 (필수 실패 우선)
        required_fail = any((r.status == 'FAIL' and r.is_required) for r in reasons)
        if required_fail:
            eligibility = 'INELIGIBLE'
            suitable = False
        else:
            if final_score >= CUTOFFS['eligible']:
                eligibility = 'ELIGIBLE'
                suitable = True
            elif final_score >= CUTOFFS['borderline']:
                eligibility = 'BORDERLINE'
                suitable = True
            else:
                eligibility = 'INELIGIBLE'
                suitable = False

        # 7) 설명/결손 정보
        reason_codes = sorted(set(r.reason_code for r in reasons if r.reason_code))
        human_msgs: List[str] = []
        for r in reasons:
            if r.status == 'PASS':
                continue
            if r.message:
                human_msgs.append(r.message)
            elif r.reason_code in REASON_TEMPLATES:
                human_msgs.append(REASON_TEMPLATES[r.reason_code])

        missing_info = sorted(set(
            r.reason_code.split('_MISSING')[0].lower()
            for r in reasons
            if r.status == 'VERIFY' and r.reason_code.endswith('_MISSING')
        ))

        if not human_msgs and eligibility == 'ELIGIBLE':
            human_msgs.append("대부분의 핵심 요건에 부합합니다.")
        elif not human_msgs and eligibility == 'BORDERLINE':
            human_msgs.append("주요 요건은 충족하였으나, 일부 확인이 필요합니다.")

        return {
            "eligibility": eligibility,
            "suitable": suitable,
            "reason_codes": reason_codes,
            "reasons_human": sorted(set(human_msgs)),
            "missing_info": missing_info,
            "match_percentage": round(final_score * 100.0, 1),
        }

    except Exception as e:
        logger.error(f"[comparison_logic] 오류: {e}", exc_info=True)
        return {
            "eligibility": "BORDERLINE",
            "suitable": True,
            "reason_codes": ["COMPARISON_ERROR"],
            "reasons_human": ["적합도 비교 중 오류가 발생했습니다. 직접 확인해주세요."],
            "missing_info": [],
            "match_percentage": 50.0
        }
