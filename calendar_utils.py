import re
import datetime

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