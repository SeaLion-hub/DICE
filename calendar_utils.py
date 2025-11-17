#calendar_utils.py
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
    
    PostgreSQL TIMESTAMPTZ, 문자열, naive datetime 모두 처리합니다.
    
    Args:
        dt_value: datetime 객체, ISO 문자열, 또는 None
    
    Returns:
        UTC timezone-aware datetime 또는 None (파싱 실패 시)
    """
    if dt_value is None:
        return None
    
    if isinstance(dt_value, dt_datetime):
        if dt_value.tzinfo is None:
            # naive datetime은 KST로 가정 (한국 공지사항이므로)
            return dt_value.replace(tzinfo=KST).astimezone(timezone.utc)
        return dt_value.astimezone(timezone.utc)
    
    if isinstance(dt_value, str):
        try:
            # ISO 형식 문자열 파싱
            parsed = dt_datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                # 타임존 정보가 없으면 KST로 가정
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

    text = key_date_text.strip().lstrip('~').rstrip('까지').rstrip('.')
    if '부터' in text:
        text = text.split('부터')[0].strip()
        
    year, month, day, hour, minute = current_year, None, None, None, None

    # --- 3. 정규표현식(Regex)으로 날짜/시간 파싱 ---

    # 3.1: 시간 파싱
    time_match_col = re.search(r'(\d{1,2}):(\d{2})', text)
    time_match_ampm = re.search(r'(오전|오후)\s*(\d{1,2})시\s*(\d{1,2})?분?', text)
    time_match_kor = re.search(r'(\d{1,2})시\s*(\d{1,2})?분?', text)

    if time_match_col:
        hour, minute = int(time_match_col.group(1)), int(time_match_col.group(2))
        if '오후' in text and hour < 12: hour += 12
        if hour == 24 and minute == 0: hour, minute = 23, 59
            
    elif time_match_ampm:
        hour = int(time_match_ampm.group(2))
        minute = int(time_match_ampm.group(3) or 0)
        if time_match_ampm.group(1) == '오후' and hour < 12: hour += 12
        if time_match_ampm.group(1) == '오전' and hour == 12: hour = 0
    elif '자정' in text:
        hour, minute = 23, 59
    elif time_match_kor:
        hour = int(time_match_kor.group(1))
        minute = int(time_match_kor.group(2) or 0)
        if '오후' in text and hour < 12: hour += 12

    # 3.2: 연도/월/일 파싱 (개선: 더 많은 형식 지원)
    year_match_full = re.search(r'(202[4-9]|20[3-9][0-9])\s*[\.년]\s*(\d{1,2})\s*[\.월]\s*(\d{1,2})', text)
    if year_match_full:
        year, month, day = int(year_match_full.group(1)), int(year_match_full.group(2)), int(year_match_full.group(3))
    
    if not month or not day:
        # 영어 월 이름 형식 (예: Jan 5, Feb 15)
        eng_date_match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})', text, re.IGNORECASE)
        if eng_date_match:
            month_str = eng_date_match.group(1).lower()
            month = ENG_MONTH_MAP.get(month_str)
            day = int(eng_date_match.group(2))
        else:
            # 다양한 날짜 형식 지원
            # 형식 1: "1월 5일", "1월5일"
            date_match_kor = re.search(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일?', text)
            if date_match_kor:
                month, day = int(date_match_kor.group(1)), int(date_match_kor.group(2))
            else:
                # 형식 2: "2025.1.5", "2025.01.05"
                date_match_dot = re.search(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})', text)
                if date_match_dot:
                    year = int(date_match_dot.group(1))
                    month, day = int(date_match_dot.group(2)), int(date_match_dot.group(3))
                else:
                    # 형식 3: "1/5", "1-5", "1월 5" (기존 패턴)
                    date_match = re.search(r'(\d{1,2})\s*[/\s월\s\.\-]+ *(\d{1,2})\s*[일\.]?', text)
                    if date_match:
                        month, day = int(date_match.group(1)), int(date_match.group(2))
            
    # 3.3: 연도 추론 (개선: 더 정확한 추론)
    if not year_match_full:
        year_match_simple = re.search(r'(202[4-9]|20[3-9][0-9])', text)
        if year_match_simple:
            year = int(year_match_simple.group(1))
        elif month and day:
            try:
                # 현재 연도 기준으로 ±1년 범위에서 가장 가까운 미래 날짜 선택
                candidates = [
                    datetime.datetime(current_year - 1, month, day, tzinfo=KST),
                    datetime.datetime(current_year, month, day, tzinfo=KST),
                    datetime.datetime(current_year + 1, month, day, tzinfo=KST),
                ]
                
                # 현재 시각과 가장 가까운 미래 날짜 선택
                year = current_year
                for candidate in candidates:
                    if candidate >= now:
                        year = candidate.year
                        break
                else:
                    # 모든 후보가 과거인 경우 가장 최근 것 선택
                    year = max(c.year for c in candidates)
            except ValueError as e:
                logger.warning(f"Invalid date values for year inference: month={month}, day={day}, error={e}")
                year = current_year
            
    # --- 4. 유효성 검사 및 객체 생성 ---
    
    if not all([month, day]):
        return None

    # [FIX 3] 시간 기본값 설정 로직 개선
    if hour is None or minute is None:
        full_context = f"{context_label.lower()} {key_date_text.lower()}"
        
        # [FIX 4] 상위 함수(extract_ai_time_window)에서 넘겨준 명시적 힌트('end'/'start')를 우선 확인
        # 이렇게 해야 "마감일" -> "end"로 변환된 힌트가 정확히 23:59 로직으로 연결됨
        is_explicit_end = (context_label == 'end')
        is_explicit_start = (context_label == 'start')

        is_end_kw = any(kw in full_context for kw in END_KEYWORDS)
        is_start_kw = any(kw in full_context for kw in START_KEYWORDS)
        
        is_end_hint = is_explicit_end or is_end_kw
        is_start_hint = is_explicit_start or is_start_kw

        # 1. 시작 키워드 존재 (마감 키워드 없음) -> 00:00
        if is_start_hint and not is_end_hint:
            hour, minute = 0, 0
            
        # 2. 마감 키워드 존재 (시작 키워드 없음) -> 23:59
        elif is_end_hint and not is_start_hint:
            hour, minute = 23, 59
            
        # 3. 시작/마감 모두 존재 (범위의 끝 등) -> 마감 우선 23:59
        elif is_end_hint and is_start_hint:
            hour, minute = 23, 59

        # 4. 키워드 없음 (순수 일회성 이벤트) -> 04:23
        else:
             hour, minute = 4, 23 

    try:
        dt = datetime.datetime(year, month, day, hour, minute, tzinfo=KST)
        
        event_title_prefix = dt.strftime('%Y-%m-%d %H:%M')
        
        calendar_event = {
            "title": f"[{event_title_prefix}] {notice_title}",
            "start_time": dt.strftime('%Y-%m-%d %H:%M:%S') 
        }
        return calendar_event
        
    except ValueError as e:
        logger.warning(
            f"Failed to create datetime for text '{key_date_text}' (notice: '{notice_title[:50]}...'): {e}",
            extra={
                "key_date_text": key_date_text,
                "notice_title": notice_title,
                "context_label": context_label,
                "year": year,
                "month": month,
                "day": day,
                "hour": hour,
                "minute": minute,
            }
        )
        return None
    except Exception as e:
        logger.error(
            f"Unexpected error creating datetime for text '{key_date_text}': {e}",
            exc_info=True,
            extra={
                "key_date_text": key_date_text,
                "notice_title": notice_title,
            }
        )
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
        
        label = (type_label or "").lower()
        text_lower = (date_text or "").lower()
        context = f"{label} {text_lower}"
        
        is_end = any(keyword in context for keyword in END_KEYWORDS)
        is_start = any(keyword in context for keyword in START_KEYWORDS)
        
        context_hint = ""
        if is_end and not is_start:
            context_hint = "end"
        elif is_start and not is_end:
            context_hint = "start"
        elif is_end:
            context_hint = "end"
        
        candidate = _parse_iso_datetime(iso_value) or _parse_freetext_datetime(date_text, notice_title, context_hint)
        
        if not candidate:
            return

        if is_end and not is_start:
            if end_at is None or candidate > end_at:
                end_at = candidate
            return

        if is_start and not is_end:
            if start_at is None or candidate < start_at:
                start_at = candidate
            return

        # 모호한 경우
        if is_end: 
             if end_at is None or candidate > end_at:
                end_at = candidate
        elif is_start:
             if start_at is None or candidate < start_at:
                start_at = candidate
        else:
            # 키워드 없음 (일회성 이벤트): 마감(end_at)에 우선 할당
            if end_at is None:
                end_at = candidate
            elif start_at is None:
                if candidate < end_at:
                    start_at = candidate
                else:
                    start_at = end_at
                    end_at = candidate
            else:
                if candidate > end_at:
                    end_at = candidate
                elif candidate < start_at:
                    start_at = candidate


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

        if text and isinstance(text, str):
            range_match = re.search(r'^(.*?)(\s*(?:∼|~)\s*)(.*)$', text)
        else:
            range_match = None

        if range_match:
            start_text = range_match.group(1).strip()
            end_text = range_match.group(3).strip()
            
            if start_text:
                classify_and_assign(f"{label} (시작)", start_text, None)
            if end_text:
                classify_and_assign(f"{label} (마감)", end_text, None)
        else:
            classify_and_assign(label, text, iso)


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

    # 데이터 검증 강화
    if start_at and end_at:
        if end_at < start_at:
            logger.warning(
                f"Invalid date range for '{notice_title[:50]}...': "
                f"start_at={start_at.isoformat()}, end_at={end_at.isoformat()}. Setting end_at to None.",
                extra={
                    "notice_title": notice_title,
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                }
            )
            end_at = None
        elif (end_at - start_at).days > 365:
            # 1년 이상 차이나는 경우 경고 (의심스러운 데이터)
            logger.warning(
                f"Suspiciously long date range for '{notice_title[:50]}...': "
                f"{(end_at - start_at).days} days",
                extra={
                    "notice_title": notice_title,
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                    "days_diff": (end_at - start_at).days,
                }
            )

    return (start_at, end_at)