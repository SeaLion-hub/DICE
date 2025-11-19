# calendar_utils.py
import re
import datetime
import logging
from datetime import datetime as dt_datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

KST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)

# [유지] 키워드 목록
START_KEYWORDS = [
    "시작", "start", "개시", "개강", "오픈", "open", "모집 시작", "접수 시작",
    "부터", "기간", "개최"
]
END_KEYWORDS = [
    "마감", "deadline", "종료", "until", "마감일", "마감기한", "접수 마감", "마지막",
    "due", "마감 시한", "까지", "기한", "제출"
]

ENG_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}


def ensure_utc_datetime(dt_value: Any) -> Optional[dt_datetime]:
    """
    다양한 형식의 datetime 값을 UTC datetime으로 정규화합니다.
    """
    if dt_value is None:
        return None
    
    if isinstance(dt_value, dt_datetime):
        if dt_value.tzinfo is None:
            return dt_value.replace(tzinfo=KST).astimezone(timezone.utc)
        return dt_value.astimezone(timezone.utc)
    
    if isinstance(dt_value, str):
        try:
            parsed = dt_datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=KST).astimezone(timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return parsed
        except (ValueError, TypeError) as e:
            logger.debug(f"Failed to parse datetime string '{dt_value}': {e}")
            return None
    
    return None


def normalize_datetime_for_calendar(key_date_text: str, notice_title: str, context_label: str = "") -> dict | None:
    """
    AI가 추출한 비정형 날짜 텍스트(key_date)와 컨텍스트(context_label)를 바탕으로
    캘린더 API가 이해할 수 있는 표준 포맷(dict)으로 변환합니다.
    """
    
    now = datetime.datetime.now(KST)
    current_year = now.year

    # [FIX 1] 텍스트 전처리 강화 (서수 제거, 쉼표 제거)
    # "Oct 27th" -> "Oct 27", "2025," -> "2025"
    text = key_date_text.strip().lstrip('~').rstrip('까지').rstrip('.')
    text = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', text, flags=re.IGNORECASE)  # 서수 제거
    text = text.replace(',', ' ')  # 쉼표 제거
    
    if '부터' in text:
        text = text.split('부터')[0].strip()
        
    year, month, day, hour, minute = current_year, None, None, None, None

    # --- 3. 정규표현식(Regex)으로 날짜/시간 파싱 ---

    # 3.1: 시간 파싱
    time_match_col = re.search(r'(\d{1,2}):(\d{2})', text)
    # [FIX 2] PM/AM 및 영어 포맷 지원 강화
    time_match_ampm_kor = re.search(r'(오전|오후)\s*(\d{1,2})시\s*(\d{1,2})?분?', text)
    time_match_ampm_eng = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)', text, re.IGNORECASE)

    if time_match_col:
        hour, minute = int(time_match_col.group(1)), int(time_match_col.group(2))
        # "5:00 PM" 같은 케이스 처리 (time_match_col은 5:00만 잡음)
        if 'pm' in text.lower() or '오후' in text:
            if hour < 12: hour += 12
        elif 'am' in text.lower() or '오전' in text:
            if hour == 12: hour = 0
        elif hour == 24 and minute == 0: 
            hour, minute = 23, 59
            
    elif time_match_ampm_kor:
        hour = int(time_match_ampm_kor.group(2))
        minute = int(time_match_ampm_kor.group(3) or 0)
        if time_match_ampm_kor.group(1) == '오후' and hour < 12: hour += 12
        if time_match_ampm_kor.group(1) == '오전' and hour == 12: hour = 0
        
    elif time_match_ampm_eng:
        # "5 PM", "5:00 PM" 처리
        hour = int(time_match_ampm_eng.group(1))
        minute = int(time_match_ampm_eng.group(2) or 0)
        ampm = time_match_ampm_eng.group(3).upper()
        if ampm == 'PM' and hour < 12: hour += 12
        if ampm == 'AM' and hour == 12: hour = 0

    elif '자정' in text:
        hour, minute = 23, 59
    
    # 한국어 "17시" 패턴
    elif re.search(r'(\d{1,2})시', text) and not time_match_ampm_kor: 
         # 분이 없는 경우 등을 위해 별도 체크
         k_time = re.search(r'(\d{1,2})시\s*(\d{1,2})?분?', text)
         if k_time:
            hour = int(k_time.group(1))
            minute = int(k_time.group(2) or 0)
            if ('오후' in text or 'pm' in text.lower()) and hour < 12: hour += 12


    # 3.2: 연도/월/일 파싱
    year_match_full = re.search(r'(202[4-9]|20[3-9][0-9])\s*[\.년]\s*(\d{1,2})\s*[\.월]\s*(\d{1,2})', text)
    if year_match_full:
        year, month, day = int(year_match_full.group(1)), int(year_match_full.group(2)), int(year_match_full.group(3))
    
    if not month or not day:
        # 영어 월 이름 형식 (예: Jan 5, Oct 27) - 서수 제거됨
        eng_date_match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{1,2})', text, re.IGNORECASE)
        if eng_date_match:
            month_str = eng_date_match.group(1).lower()[:3]
            month = ENG_MONTH_MAP.get(month_str)
            day = int(eng_date_match.group(2))
        else:
            # 한국어 형식 등
            date_match_kor = re.search(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일?', text)
            if date_match_kor:
                month, day = int(date_match_kor.group(1)), int(date_match_kor.group(2))
            else:
                date_match_dot = re.search(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})', text)
                if date_match_dot:
                    year = int(date_match_dot.group(1))
                    month, day = int(date_match_dot.group(2)), int(date_match_dot.group(3))
                else:
                    date_match = re.search(r'(\d{1,2})\s*[/\s\.\-]+ *(\d{1,2})\s*[일\.]?', text)
                    if date_match:
                        # 월/일 구분 모호성 주의 (여기선 월 우선)
                        month, day = int(date_match.group(1)), int(date_match.group(2))
            
    # 3.3: 연도 추론
    # 텍스트 내에 명시적 연도가 있으면 최우선 (예: "Oct 31 2025")
    year_match_explicit = re.search(r'(202[4-9]|20[3-9][0-9])', text)
    if year_match_explicit:
        year = int(year_match_explicit.group(1))
    elif month and day:
        # 연도가 텍스트에 없으면 현재/미래/과거 추론
        try:
            candidates = [
                datetime.datetime(current_year - 1, month, day, tzinfo=KST),
                datetime.datetime(current_year, month, day, tzinfo=KST),
                datetime.datetime(current_year + 1, month, day, tzinfo=KST),
            ]
            
            year = current_year
            # 미래 날짜 우선 (단, 너무 먼 미래가 아니면)
            for candidate in candidates:
                if candidate.date() >= now.date(): # 오늘 포함 미래
                    year = candidate.year
                    break
            else:
                year = max(c.year for c in candidates)
        except ValueError:
            year = current_year
            
    # --- 4. 유효성 검사 및 객체 생성 ---
    if not all([month, day]):
        return None

    # 시간 기본값 설정
    if hour is None or minute is None:
        full_context = f"{context_label.lower()} {key_date_text.lower()}"
        is_explicit_end = (context_label == 'end')
        is_explicit_start = (context_label == 'start')

        is_end_kw = any(kw in full_context for kw in END_KEYWORDS)
        is_start_kw = any(kw in full_context for kw in START_KEYWORDS)
        
        is_end_hint = is_explicit_end or is_end_kw
        is_start_hint = is_explicit_start or is_start_kw

        if is_start_hint and not is_end_hint:
            hour, minute = 0, 0
        elif is_end_hint and not is_start_hint:
            hour, minute = 23, 59
        elif is_end_hint and is_start_hint:
            hour, minute = 23, 59
        else:
            if is_explicit_start:
                hour, minute = 0, 0
            else:
                hour, minute = 23, 59 

    try:
        dt = datetime.datetime(year, month, day, hour, minute, tzinfo=KST)
        
        event_title_prefix = dt.strftime('%Y-%m-%d %H:%M')
        calendar_event = {
            "title": f"[{event_title_prefix}] {notice_title}",
            "start_time": dt.strftime('%Y-%m-%d %H:%M:%S') 
        }
        return calendar_event
        
    except ValueError:
        return None
    except Exception as e:
        logger.error(f"Error creating datetime: {e}")
        return None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = dt_datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_freetext_datetime(text: Optional[str], title: str, context_label: str = "") -> Optional[datetime.datetime]:
    if not text or not isinstance(text, str):
        return None
    calendar_event = normalize_datetime_for_calendar(text, title, context_label)
    if not calendar_event:
        return None
    start_time = calendar_event.get("start_time")
    if not start_time:
        return None
    try:
        naive = dt_datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        aware = naive.replace(tzinfo=KST)
        return aware.astimezone(timezone.utc)
    except ValueError:
        return None


def _normalize_structured_datetime(value: Any) -> Optional[datetime.datetime]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            candidate = _normalize_structured_datetime(item)
            if candidate: return candidate
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=KST).astimezone(timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "" or stripped.lower() in {"null", "[null]", "none"}:
            return None
        parsed = _parse_iso_datetime(stripped)
        if parsed: return parsed
        return None
    return None


def extract_ai_time_window(structured_info: Dict[str, Any] | None, notice_title: str) -> Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    if not isinstance(structured_info, dict):
        return (None, None)

    start_at = None
    end_at = None

    def classify_and_assign(type_label: Optional[str], date_text: Optional[str], iso_value: Optional[str] = None):
        nonlocal start_at, end_at
        
        label = (type_label or "").lower()
        text_lower = (date_text or "").lower()
        context = f"{label} {text_lower}"
        
        is_end = any(keyword in context for keyword in END_KEYWORDS)
        is_start = any(keyword in context for keyword in START_KEYWORDS)
        
        context_hint = ""
        if is_end and not is_start: context_hint = "end"
        elif is_start and not is_end: context_hint = "start"
        elif is_end: context_hint = "end"
        
        candidate = _parse_iso_datetime(iso_value) or _parse_freetext_datetime(date_text, notice_title, context_hint)
        
        if not candidate:
            return

        if is_end and not is_start:
            if end_at is None or candidate > end_at: end_at = candidate
            return

        if is_start and not is_end:
            if start_at is None or candidate < start_at: start_at = candidate
            return

        if is_end: 
             if end_at is None or candidate > end_at: end_at = candidate
        elif is_start:
             if start_at is None or candidate < start_at: start_at = candidate
        else:
            if end_at is None:
                end_at = candidate
            elif start_at is None:
                if candidate < end_at:
                    start_at = candidate
                    if start_at.hour == 4 and start_at.minute == 23:
                        start_at = start_at.replace(hour=0, minute=0)
                else:
                    start_at = end_at
                    end_at = candidate
                    if end_at.hour == 4 and end_at.minute == 23:
                        end_at = end_at.replace(hour=23, minute=59)
            else:
                if candidate > end_at:
                    end_at = candidate
                    if end_at.hour == 4 and end_at.minute == 23:
                        end_at = end_at.replace(hour=23, minute=59)
                elif candidate < start_at:
                    start_at = candidate
                    if start_at.hour == 4 and start_at.minute == 23:
                        start_at = start_at.replace(hour=0, minute=0)

    # Key Dates 추출 및 루프
    key_dates = []
    if isinstance(structured_info.get("key_dates"), list): key_dates.extend(structured_info["key_dates"])
    if isinstance(structured_info.get("keyDates"), list): key_dates.extend(structured_info["keyDates"])

    # [FIX 3] 날짜 범위 파싱 시, 뒤쪽에만 연도가 있으면 앞쪽으로 전파 (Year Propagation)
    # 예: "Oct 27 ~ Oct 31, 2025" -> Start에 2025가 없어서 내년으로 오인하는 문제 해결
    def process_range_and_classify(label, text, iso=None):
        if text and isinstance(text, str):
            range_match = re.search(r'^(.*?)(\s*(?:∼|~)\s*)(.*)$', text)
            if range_match:
                start_text = range_match.group(1).strip()
                end_text = range_match.group(3).strip()
                
                # 연도 전파 로직
                start_year_match = re.search(r'\d{4}', start_text)
                end_year_match = re.search(r'\d{4}', end_text)
                
                # 뒤에는 연도가 있는데 앞에는 없으면, 뒤의 연도를 앞에 붙여줌
                if end_year_match and not start_year_match:
                    start_text = f"{start_text} {end_year_match.group(0)}"

                if start_text: classify_and_assign(f"{label} (시작)", start_text, None)
                if end_text: classify_and_assign(f"{label} (마감)", end_text, None)
            else:
                classify_and_assign(label, text, iso)
        else:
            classify_and_assign(label, text, iso)

    for entry in key_dates:
        if not isinstance(entry, dict): continue
        label = entry.get("key_date_type") or entry.get("type") or entry.get("label") or entry.get("type_label") or ""
        text = entry.get("key_date") or entry.get("value") or entry.get("text") or ""
        iso = entry.get("iso") or entry.get("key_date_iso")
        process_range_and_classify(label, text, iso)

    root_label = structured_info.get("key_date_type") or structured_info.get("keyDateType") or ""
    root_text = structured_info.get("key_date") or structured_info.get("keyDate") or ""
    root_iso = structured_info.get("key_date_iso") or structured_info.get("keyDateIso")
    
    process_range_and_classify(root_label, root_text, root_iso)

    start_at = _normalize_structured_datetime(start_at)
    end_at = _normalize_structured_datetime(end_at)

    if end_at and end_at.hour == 4 and end_at.minute == 23:
        if start_at:
            end_at = end_at.replace(hour=23, minute=59)
    if start_at and start_at.hour == 4 and start_at.minute == 23:
        start_at = start_at.replace(hour=0, minute=0)

    if start_at and end_at:
        if end_at < start_at:
            end_at = None
        elif (end_at - start_at).days > 365:
            end_at = None

    return (start_at, end_at)