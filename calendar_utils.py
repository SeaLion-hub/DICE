import re
import datetime
from datetime import datetime as dt_datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

KST = timezone(timedelta(hours=9))
START_KEYWORDS = ["시작", "start", "개시", "개강", "오픈", "open", "모집 시작", "접수 시작"]
END_KEYWORDS = [
    "마감",
    "deadline",
    "종료",
    "until",
    "마감일",
    "마감기한",
    "접수 마감",
    "마지막",
    "due",
    "마감 시한",
]


def normalize_datetime_for_calendar(key_date_text: str, notice_title: str) -> dict | None:
    """
    AI가 추출한 비정형 날짜 텍스트(key_date)를 
    캘린더 API가 이해할 수 있는 표준 포맷(dict)으로 변환합니다.

    Args:
        key_date_text (str): ai_processor.py가 추출한 'key_date' 필드 값.
        notice_title (str): 캘린더 제목으로 사용할 공지사항 원본 제목.

    Returns:
        dict | None: 캘린더 이벤트 객체 (예: {'title': '...', 'start_time': 'YYYY-MM-DD HH:MM:SS'})
                     파싱이 불가능하거나 의미 없는 날짜(예: '2025 여름')이면 None 반환.
    """
    
    # 1. 현재 시간 기준으로 기본 연도 설정 (사용자 요청)
    now = datetime.datetime.now() # (예: 현재 2025-10-27)
    current_year = now.year

    # 2. 문자열 정제: 불필요한 기호 제거
    # 예: "~10/10(금) 17시까지" -> "10/10(금) 17시"
    # 예: "2025. 10. 10.~10. 22." -> "10. 22." (기간의 경우, 마감일/종료일 우선)
    text = key_date_text.strip().lstrip('~').rstrip('까지').rstrip('.')
    if '~' in text:
        text = text.split('~')[-1].strip()
    if '부터' in text: # "9월 15일부터" -> 날짜만 추출
        text = text.split('부터')[0].strip()
        
    year, month, day, hour, minute = current_year, None, None, None, None

    # --- 3. 정규표현식(Regex)으로 날짜/시간 파싱 ---

    # 패턴 1: "9/29(월) 14:00", "9월 29일(월) 14:00"
    match = re.search(r'(\d{1,2})[/\s월\s]+(\d{1,2})[일\s]?.*?(\d{1,2}):(\d{2})', text)
    
    # 패턴 2: "9월 29일(월) 오후 2시" (시간만 따로)
    if not match:
        time_match = re.search(r'(오전|오후)\s*(\d{1,2})시', text)
        date_match = re.search(r'(\d{1,2})[/\s월\s]+(\d{1,2})[일\s]?', text)
        
        if date_match and time_match:
            month, day = int(date_match.group(1)), int(date_match.group(2))
            hour = int(time_match.group(2))
            if time_match.group(1) == '오후' and hour < 12:
                hour += 12
            minute = 0
            
    # 패턴 3: "10월 15일 (수) 자정"
    elif '자정' in text:
        date_match = re.search(r'(\d{1,2})[/\s월\s]+(\d{1,2})[일\s]?', text)
        if date_match:
            month, day = int(date_match.group(1)), int(date_match.group(2))
            hour, minute = 23, 59 # 마감일 자정은 23:59로 처리
            
    if match and not (month or day): # 패턴 1에서 찾은 경우
        month, day = int(match.group(1)), int(match.group(2))
        hour, minute = int(match.group(3)), int(match.group(4))
        if '오후' in text and hour < 12: # "오후 2:00" 같은 케이스
            hour += 12
            
    # 패턴 4: 연도 정보가 있는지 확인 (예: "2025. 10. 17.")
    year_match = re.search(r'(202[4-9])[\s*년\s*\.]*(\d{1,2})[\s*월\s*\.]*(\d{1,2})', text)
    if year_match:
        year = int(year_match.group(1))
        if not month: # 월/일을 위에서 찾지 못한 경우
            month = int(year_match.group(2))
            day = int(year_match.group(3))

    # --- 4. 유효성 검사 및 객체 생성 ---
    
    # 월/일 정보가 없으면 유효한 이벤트로 보지 않음 (예: "2025 여름")
    if not all([month, day]):
        return None

    # 시간 정보가 없는 경우 (예: "10/10(금)"),
    # 마감/접수/기한 등은 23:59로, 나머지는 09:00 (업무 시작)로 설정
    if hour is None or minute is None:
        if any(kw in key_date_text for kw in ['마감', '까지', 'Deadline', '기한', '접수']):
            hour, minute = 23, 59
        else:
            hour, minute = 9, 0 # 기본 시작 시간

    try:
        # 유효한 날짜인지 datetime 객체 생성 시도
        dt = datetime.datetime(year, month, day, hour, minute)
        
        # 캘린더 제목 생성 (사용자 예시 반영)
        # 예: [2025-10-27 14:00] 26학년도 전기 ... 입학설명회 개최
        event_title_prefix = dt.strftime('%Y-%m-%d %H:%M')
        
        calendar_event = {
            "title": f"[{event_title_prefix}] {notice_title}",
            "start_time": dt.strftime('%Y-%m-%d %H:%M:%S') # ISO 8601의 일부
        }
        return calendar_event
        
    except ValueError as e:
        # 2월 30일 등 잘못된 날짜 파싱 시
        print(f"Error creating datetime for text '{key_date_text}': {e}")
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


def _parse_freetext_datetime(text: Optional[str], title: str) -> Optional[datetime.datetime]:
    if not text or not isinstance(text, str):
        return None
    calendar_event = normalize_datetime_for_calendar(text, title)
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
    """
    AI가 반환한 구조화 데이터에서 날짜 필드를 안전하게 UTC datetime으로 변환한다.
    - 문자열 "[null]", "null" 등은 None으로 간주
    - 리스트/튜플이 들어오면 첫 번째 유효한 값을 사용
    - naive datetime은 KST 기준으로 해석 후 UTC로 변환
    """
    if value is None:
        return None

    if isinstance(value, (list, tuple)):
        for item in value:
            candidate = _normalize_structured_datetime(item)
            if candidate:
                return candidate
        return None

    if isinstance(value, datetime.datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=KST).astimezone(timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        lowered = stripped.lower()
        if lowered in {"null", "[null]", "none"}:
            return None
        parsed = _parse_iso_datetime(stripped)
        if parsed:
            return parsed
        return None

    return None


def extract_ai_time_window(structured_info: Dict[str, Any] | None, notice_title: str) -> Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    if not isinstance(structured_info, dict):
        return (None, None)

    start_at = None
    end_at = None

    def classify_and_assign(type_label: Optional[str], date_text: Optional[str], iso_value: Optional[str] = None):
        nonlocal start_at, end_at
        candidate = _parse_iso_datetime(iso_value) or _parse_freetext_datetime(date_text, notice_title)
        if not candidate:
            return

        label = (type_label or "").lower()
        text_lower = (date_text or "").lower()

        is_end = any(keyword in label for keyword in END_KEYWORDS) or any(keyword in text_lower for keyword in END_KEYWORDS)
        is_start = any(keyword in label for keyword in START_KEYWORDS) or any(keyword in text_lower for keyword in START_KEYWORDS)

        if is_end and not is_start:
            if end_at is None or candidate > end_at:
                end_at = candidate
            return

        if is_start and not is_end:
            if start_at is None or candidate < start_at:
                start_at = candidate
            return

        if start_at is None:
            start_at = candidate
        elif end_at is None or candidate > end_at:
            end_at = candidate

    key_dates = []
    if isinstance(structured_info.get("key_dates"), list):
        key_dates.extend(structured_info["key_dates"])
    if isinstance(structured_info.get("keyDates"), list):
        key_dates.extend(structured_info["keyDates"])

    for entry in key_dates:
        if not isinstance(entry, dict):
            continue
        classify_and_assign(
            entry.get("key_date_type") or entry.get("type") or entry.get("label") or entry.get("type_label"),
            entry.get("key_date") or entry.get("value") or entry.get("text"),
            entry.get("iso") or entry.get("key_date_iso"),
        )

    classify_and_assign(
        structured_info.get("key_date_type") or structured_info.get("keyDateType"),
        structured_info.get("key_date") or structured_info.get("keyDate"),
        structured_info.get("key_date_iso") or structured_info.get("keyDateIso"),
    )

    start_at = _normalize_structured_datetime(start_at)
    end_at = _normalize_structured_datetime(end_at)

    if start_at and end_at and end_at < start_at:
        # 종료 시간이 시작 시간보다 이전인 경우, 불확실한 값으로 판단하여 종료 시간을 폐기
        end_at = None

    return (start_at, end_at)