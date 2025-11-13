import re
import datetime
from datetime import datetime as dt_datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

KST = timezone(timedelta(hours=9))

# [유지] 키워드 목록 (start/end 분류에 사용)
START_KEYWORDS = [
    "시작", "start", "개시", "개강", "오픈", "open", "모집 시작", "접수 시작",
    "부터", "일시", "기간", "개최"
]
END_KEYWORDS = [
    "마감", "deadline", "종료", "until", "마감일", "마감기한", "접수 마감", "마지막",
    "due", "마감 시한", "까지", "기한", "제출"
]


def normalize_datetime_for_calendar(key_date_text: str, notice_title: str, context_label: str = "") -> dict | None:
    """
    AI가 추출한 비정형 날짜 텍스트(key_date)와 컨텍스트(context_label)를 바탕으로
    캘린더 API가 이해할 수 있는 표준 포맷(dict)으로 변환합니다.

    Args:
        key_date_text (str): ai_processor.py가 추출한 'key_date' 필드 값.
        notice_title (str): 캘린더 제목으로 사용할 공지사항 원본 제목.
        context_label (str): "제출 기한", "시작일" 등 key_date_type 값.
                             (시간 기본값 설정을 위해 사용됨)

    Returns:
        dict | None: 캘린더 이벤트 객체
    """
    
    # 1. 현재 시간 기준으로 기본 연도 설정
    now = datetime.datetime.now()
    current_year = now.year

    # 2. 문자열 정제 (범위 문자 '~'는 제거하지 않음)
    text = key_date_text.strip().lstrip('~').rstrip('까지').rstrip('.')
    if '부터' in text:
        text = text.split('부터')[0].strip()
        
    year, month, day, hour, minute = current_year, None, None, None, None

    # --- 3. 정규표현식(Regex)으로 날짜/시간 파싱 (FIX 1: 시간 파싱 우선) ---

    # 3.1: 시간 (HH:MM, 오후 H시, H시)
    # [FIX 1] "18:00 까지" -> "18:00"을 먼저 잡도록 순서 변경 및 정규식 강화
    time_match_col = re.search(r'(\d{1,2}):(\d{2})', text)
    time_match_ampm = re.search(r'(오전|오후)\s*(\d{1,2})시\s*(\d{1,2})?분?', text)
    # [FIX 1] "17시" 케이스 (AM/PM이나 콜론(:) 없는 경우)
    time_match_kor = re.search(r'(\d{1,2})시\s*(\d{1,2})?분?', text)

    if time_match_col:
        hour, minute = int(time_match_col.group(1)), int(time_match_col.group(2))
        if '오후' in text and hour < 12: hour += 12
    elif time_match_ampm:
        hour = int(time_match_ampm.group(2))
        minute = int(time_match_ampm.group(3) or 0)
        if time_match_ampm.group(1) == '오후' and hour < 12: hour += 12
        if time_match_ampm.group(1) == '오전' and hour == 12: hour = 0
    elif '자정' in text:
        hour, minute = 23, 59
    elif time_match_kor:
        # (주의) 다른 시간 정규식과 겹치지 않도록 'elif' 유지
        hour = int(time_match_kor.group(1))
        minute = int(time_match_kor.group(2) or 0)
        if '오후' in text and hour < 12: hour += 12

    # 3.2: 연도/월/일 (YYYY.MM.DD, MM/DD, MM.DD., MM월 DD일)
    year_match_full = re.search(r'(202[4-9])\s*[\.년]\s*(\d{1,2})\s*[\.월]\s*(\d{1,2})', text)
    if year_match_full:
        year, month, day = int(year_match_full.group(1)), int(year_match_full.group(2)), int(year_match_full.group(3))
    
    if not month or not day:
        date_match = re.search(r'(\d{1,2})\s*[/\s월\s\.]+ *(\d{1,2})[일\s]?', text)
        if date_match:
            month, day = int(date_match.group(1)), int(date_match.group(2))
            
    # 3.3: 연도 재확인
    if not year_match_full:
        year_match_simple = re.search(r'(202[4-9])', text)
        if year_match_simple:
            year = int(year_match_simple.group(1))
            
    # --- 4. 유효성 검사 및 객체 생성 ---
    
    # 월/일 정보가 없으면 유효한 이벤트로 보지 않음
    if not all([month, day]):
        return None

    # [FIX 3] (사용자 요청) 시간 정보가 없는 경우, 컨텍스트 기반으로 기본값 설정
    if hour is None or minute is None:
        # context_label ("제출 기한")과 key_date_text ("...까지")를 모두 검사
        full_context = f"{context_label.lower()} {key_date_text.lower()}"
        
        is_end_hint = any(kw in full_context for kw in END_KEYWORDS)
        is_start_hint = any(kw in full_context for kw in START_KEYWORDS)

        if is_end_hint and not is_start_hint:
            hour, minute = 23, 59 # (사용자 요청) 마감일 기본값
        elif is_start_hint and not is_end_hint:
            hour, minute = 0, 0 # (사용자 요청) 시작일 기본값
        else:
            # "2025-10-10" 처럼 모호하거나, "시작 및 마감"처럼 둘 다 해당하면
            # 09:00 (업무 시작) 또는 23:59 (마감일 가능성)
            # 로그상 "마감일"이 09:00로 잡히는 문제를 봤으므로, 마감일 가능성이 더 높음.
            if is_end_hint:
                 hour, minute = 23, 59
            else:
                 hour, minute = 9, 0 # 기존 기본값 유지 (모호한 경우)

    try:
        dt = datetime.datetime(year, month, day, hour, minute)
        
        event_title_prefix = dt.strftime('%Y-%m-%d %H:%M')
        
        calendar_event = {
            "title": f"[{event_title_prefix}] {notice_title}",
            "start_time": dt.strftime('%Y-%m-%d %H:%M:%S') 
        }
        return calendar_event
        
    except ValueError as e:
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


# [FIX 2] 컨텍스트(context_label)를 normalize 함수로 전달
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
    """
    AI가 반환한 구조화 데이터에서 날짜 필드를 안전하게 UTC datetime으로 변환한다.
    (내부 헬퍼 함수, 수정 없음)
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

    # [FIX 2] classify_and_assign 함수 수정
    def classify_and_assign(type_label: Optional[str], date_text: Optional[str], iso_value: Optional[str] = None):
        nonlocal start_at, end_at
        
        # 1. 컨텍스트(키워드)부터 파악
        label = (type_label or "").lower()
        text_lower = (date_text or "").lower()
        context = f"{label} {text_lower}"
        
        is_end = any(keyword in context for keyword in END_KEYWORDS)
        is_start = any(keyword in context for keyword in START_KEYWORDS)
        
        # 2. 파서에게 컨텍스트 힌트 전달
        # (normalize_datetime_for_calendar가 이 힌트를 사용해 00:00 / 23:59 결정)
        context_hint = ""
        if is_end and not is_start:
            context_hint = "end"
        elif is_start and not is_end:
            context_hint = "start"
        elif is_end: # 둘 다 True ("~까지 시작")이면 end 우선
            context_hint = "end"
        
        # 3. 컨텍스트 힌트와 함께 파싱 실행
        candidate = _parse_iso_datetime(iso_value) or _parse_freetext_datetime(date_text, notice_title, context_hint)
        
        if not candidate:
            return

        # 4. 파싱된 datetime 객체를 start/end 변수에 할당
        if is_end and not is_start:
            if end_at is None or candidate > end_at:
                end_at = candidate
            return

        if is_start and not is_end:
            if start_at is None or candidate < start_at:
                start_at = candidate
            return

        # [FIX] 모호한 경우 (둘 다 True or 둘 다 False)
        if is_end: # "시작 마감일" (is_start=True, is_end=True) -> 마감일(end_at)
             if end_at is None or candidate > end_at:
                end_at = candidate
        elif is_start: # "2025-10-10 일시" (is_start=True, is_end=False) -> 시작일(start_at)
             if start_at is None or candidate < start_at:
                start_at = candidate
        else:
            # 둘 다 False (예: "2025-10-10"만)
            # 첫 번째는 start, 두 번째는 end로 할당 (범위 파싱에서 사용)
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
        
        label = entry.get("key_date_type") or entry.get("type") or entry.get("label") or entry.get("type_label") or ""
        text = entry.get("key_date") or entry.get("value") or entry.get("text") or ""
        iso = entry.get("iso") or entry.get("key_date_iso")

        # [유지] 범위 파싱 로직
        if text and isinstance(text, str):
            range_match = re.search(r'^(.*?)(\s*(?:∼|~)\s*)(.*)$', text)
        else:
            range_match = None

        if range_match:
            start_text = range_match.group(1).strip()
            end_text = range_match.group(3).strip()
            
            if start_text:
                # [FIX 2] "시작" 컨텍스트를 명시적으로 전달
                classify_and_assign(f"{label} (시작)", start_text, None)
            if end_text:
                # [FIX 2] "마감" 컨텍스트를 명시적으로 전달
                classify_and_assign(f"{label} (마감)", end_text, None)
        else:
            classify_and_assign(label, text, iso)


    # 루트 레벨의 key_date도 동일하게 처리
    root_label = structured_info.get("key_date_type") or structured_info.get("keyDateType") or ""
    root_text = structured_info.get("key_date") or structured_info.get("keyDate") or ""
    root_iso = structured_info.get("key_date_iso") or structured_info.get("keyDateIso")
    
    if root_text and isinstance(root_text, str):
        root_range_match = re.search(r'^(.*?)(\s*(?:∼|~)\s*)(.*)$', root_text)
    else:
        root_range_match = None
        
    if root_range_match:
        start_text = root_range_match.group(1).strip()
        end_text = root_range_match.group(3).strip()
        if start_text:
            classify_and_assign(f"{root_label} (시작)", start_text, None)
        if end_text:
            classify_and_assign(f"{root_label} (마감)", end_text, None)
    else:
        classify_and_assign(root_label, root_text, root_iso)


    start_at = _normalize_structured_datetime(start_at)
    end_at = _normalize_structured_datetime(end_at)

    if start_at and end_at and end_at < start_at:
        # 종료 시간이 시작 시간보다 이전인 경우, 종료 시간을 폐기
        end_at = None

    return (start_at, end_at)