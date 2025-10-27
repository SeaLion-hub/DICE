import google.generativeai as genai
import os
import re
import json
from dotenv import load_dotenv

load_dotenv()

# --- 기존 코드 (API 설정) ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY not found in .env file")

genai.configure(api_key=GOOGLE_API_KEY)

generation_config = genai.GenerationConfig(
    temperature=0.1,
    top_p=1.0,
    top_k=1,
    max_output_tokens=2048,
    response_mime_type="text/plain",
)

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

model = genai.GenerativeModel(
    model_name="gemini-1.5-pro-latest",
    generation_config=generation_config,
    safety_settings=safety_settings,
)
# --- 기존 코드 종료 ---


# --- 1단계: 분류 프롬프트 (기존과 동일) ---
SYSTEM_PROMPT_CLASSIFY = """
당신은 연세대학교 공지사항을 분류하는 AI입니다. 
주어진 [공지 텍스트]를 읽고, 다음 7가지 카테고리 중 가장 적합한 해시태그 1개만 반환해 주세요:
[#학사, #장학, #취업, #행사, #공모전/대회, #국제교류, #일반]

규칙:
1. 오직 7개의 태그 중 하나만 선택해야 합니다.
2. 추가 설명 없이 해시태그만 반환해야 합니다. (예: #학사)
3. 2개 이상 해당되면, 가장 중요하다고 생각되는 1개만 반환합니다.
"""

# --- 2단계: 추출 프롬프트 (신규 추가 및 수정) ---
# 기존 SYSTEM_PROMPT_EXTRACT_JSON 삭제

# [신규] #장학 프롬프트
PROMPT_SCHOLARSHIP = """
당신은 '장학금' 공지사항에서 프로필 비교에 사용할 수 있도록 핵심 자격 요건을 추출하는 AI입니다.
주어진 [공지 텍스트]를 꼼꼼히 분석하여, 아래 JSON 형식에 맞춰 **구조화된 정보**를 추출하세요.

[공지 텍스트]
{notice_text}
[/공지 텍스트]

추출 규칙:
1.  `target_audience_raw`: 원본 텍스트의 '지원 자격'을 그대로 요약합니다 (Fallback 용도).
2.  `qualifications`:
    * `gpa_min`: "4.3 만점에 3.0" 또는 "3.0/4.3" 등의 내용을 발견하면, 최소 학점 숫자만 추출합니다 (예: "3.0").
    * `grade_level`: "1학년", "2~4학년", "학부 재학생", "대학원생" 등 학년/학적 정보를 추출합니다.
    * `income_status`: "가계 곤란", "소득분위 8분위 이하" 등 소득 관련 정보를 추출합니다.
    * `department`: "경영대학", "AI·ICT 분야" 등 특정 단과대학/학과 정보를 추출합니다.
    * `other`: 위의 4가지 외 다른 핵심 자격 (예: '2026-1학기 파견 예정자')
3.  `key_date_type`: 날짜의 유형을 '신청 마감일' 또는 '신청 기간'으로 명시합니다.
4.  `key_date`: 장학금 '신청 마감 일시' 또는 '신청 기간'을 텍스트 원본에서 그대로 추출합니다.
5.  정보가 없는 필드는 "N/A"로 처리합니다.

JSON 출력:
{{
  "target_audience_raw": "[지원 자격 원본 텍스트 요약]",
  "qualifications": {{
    "gpa_min": "[추출된 최소 학점 (예: '3.0')]",
    "grade_level": "[대상 학년 (예: '1학년', '학부 재학생')]",
    "income_status": "[소득 요건 (예: '가계 곤란 학생', '8분위 이하')]",
    "department": "[대상 학과 (예: '상경대학')]",
    "other": "[기타 자격 (예: '2026-1학기 파견 예정자')]"
  }},
  "key_date_type": "[날짜 유형]",
  "key_date": "[핵심 날짜 (예: '10/19(일)')]"
}}
"""

# [신규] #취업 프롬프트
PROMPT_RECRUITMENT = """
당신은 '채용 및 취업' 공지사항에서 프로필 비교에 사용할 수 있도록 핵심 자격 요건을 추출하는 AI입니다.
주어진 [공지 텍스트]를 꼼꼼히 분석하여, 아래 JSON 형식에 맞춰 **구조화된 정보**를 추출하세요.

[공지 텍스트]
{notice_text}
[/공지 텍스트]

추출 규칙:
1.  `target_audience_raw`: 원본 텍스트의 '지원 자격'을 그대로 요약합니다.
2.  `qualifications`:
    * `degree`: "교육학 박사", "졸업(예정)자", "석사 과정생", "학부 3학년 이상" 등 학력/학위 정보를 추출합니다.
    * `military_service`: "군필자", "전문연구요원", "군필 또는 면제" 등 병역 관련 정보를 추출합니다.
    * `gender`: "여학생 대상 멘토링" 등 성별 관련 요건이 있다면 추출합니다.
    * `language_requirements_text`: 본문에서 요구하는 모든 공인 어학 성적 요건 (TOEIC, OPIc 등)을 **하나의 텍스트 필드**로 묶어서 추출합니다 (예: "TOEIC 850점 이상", "영어 능통자 우대").
    * `other`: 위의 4가지 외 다른 핵심 자격 (예: '이공계 전공자')
3.  `key_date_type`: 날짜의 유형을 '접수 마감일', '채용 기간', '설명회 일시' 중 가장 핵심적인 것으로 명시합니다.
4.  `key_date`: 채용 '접수 마감 일시' 또는 '특강/설명회 일시'를 텍스트 원본에서 그대로 추출합니다.
5.  정보가 없는 필드는 "N/A"로 처리합니다.

JSON 출력:
{{
  "target_audience_raw": "[지원 자격 원본 텍스트 요약 (예: 이공계 여성 학부생)]",
  "qualifications": {{
    "degree": "[필요 학력 (예: '교육학 박사', '학부 재학생')]",
    "military_service": "[병역 요건 (예: '군필 또는 면제')]",
    "gender": "[대상 성별 (예: '여학생')]",
    "language_requirements_text": "[하나로 묶인 어학 요건 텍스트 (예: 'TOEIC 800점 이상')]"
  }},
  "key_date_type": "[날짜 유형]",
  "key_date": "[핵심 날짜 (예: '10/10(금) 17시')]"
}}
"""

# [신규] #국제교류 프롬프트
PROMPT_INTERNATIONAL = """
당신은 '국제교류 프로그램' 공지사항에서 프로필 비교에 사용할 수 있도록 핵심 자격 요건을 추출하는 AI입니다.
주어진 [공지 텍스트]를 꼼꼼히 분석하여, 아래 JSON 형식에 맞춰 **구조화된 정보**를 추출하세요.

[공지 텍스트]
{notice_text}
[/공지 텍스트]

추출 규칙:
1.  `target_audience_raw`: 원본 텍스트의 '지원 자격'을 그대로 요약합니다 (Fallback 용도).
2.  `qualifications`:
    * `gpa_min`: "4.3 만점에 3.0" 등의 내용을 발견하면, 최소 학점 숫자만 추출합니다 (예: "3.0").
    * `grade_level`: "학부 2~7학기 이수자" 등 학년/학적 정보를 추출합니다.
    * `language_requirements_text`: 본문에서 요구하는 **모든** 공인 어학 성적 요건 (TOEFL, TEPS, JLPT, HSK 등)을 **하나의 텍스트 필드**로 묶어서 추출합니다 (예: "TOEFL iBT 100점 이상 또는 IELTS 7.0 이상", "JLPT N2 이상").
    * `other`: 위의 3가지 외 다른 핵심 자격 (예: 'CAMPUS Asia 사업 참여 학과')
3.  `key_date_type`: 날짜의 유형을 '모집 마감일' 또는 '신청 기간'으로 명시합니다.
4.  `key_date`: '모집 마감 일시'를 텍스트 원본에서 그대로 추출합니다.
5.  정보가 없는 필드는 "N/A"로 처리합니다.

JSON 출력:
{{
  "target_audience_raw": "[지원 자격 원본 텍스트 요약 (예: CAMPUS Asia 사업 참여 학과)]",
  "qualifications": {{
    "gpa_min": "[추출된 최소 학점 (예: '3.0')]",
    "grade_level": "[대상 학년 (예: '학부 2~7학기 이수자')]",
    "language_requirements_text": "[하나로 묶인 어학 요건 텍스트 (예: 'TOEIC 850점 또는 TOEFL iBT 90점 이상')]"
  }},
  "key_date_type": "모집 마감일",
  "key_date": "[모집 마감 일시 (예: '~10/10(금) 17시')]"
}}
"""

# [신규] #학사, #행사, #공모전/대회, #일반 을 위한 단순 프롬프트
PROMPT_SIMPLE_DEFAULT = """
당신은 '{category_name}' 공지사항에서 '대상'과 '핵심 날짜'를 추출하는 AI입니다.
주어진 [공지 텍스트]를 꼼꼼히 분석하여, 아래 JSON 형식에 맞춰 정보를 추출하세요.

[공지 텍스트]
{notice_text}
[/공지 텍스트]

추출 규칙:
1.  `target_audience`: 공모전 '참가 자격' ('본교 학부생'), 행사 '참여 대상' ('학부생 누구나'), 학사 '적용 대상' ('졸업예정자'), 일반 '관련 대상' ('전체 구성원')을 추출합니다.
2.  `key_date_type`: 날짜의 유형을 명시합니다 (예: '접수 마감일', '행사 일시', '신청 기간', '이수 기간').
3.  `key_date`: 공지사항에서 가장 중요한 날짜(마감일, 행사일 등)를 원본 텍스트 그대로 추출합니다.
4.  정보가 없는 필드는 "N/A"로 처리합니다.

JSON 출력:
{{
  "target_audience": "[참가/참여/적용/관련 대상 (예: '본교 학부생', '졸업예정자')]",
  "key_date_type": "[날짜 유형 (예: '접수 마감일')]",
  "key_date": "[핵심 날짜 (예: '11월 12일(수)까지')]"
}}
"""

# [신규] 프롬프트 선택을 위한 매핑
EXTRACTION_PROMPT_MAP = {
    "#장학": PROMPT_SCHOLARSHIP,
    "#취업": PROMPT_RECRUITMENT,
    "#국제교류": PROMPT_INTERNATIONAL,
    # 나머지는 단순/기본 프롬프트 사용
    "#학사": PROMPT_SIMPLE_DEFAULT,
    "#행사": PROMPT_SIMPLE_DEFAULT,
    "#공모전/대회": PROMPT_SIMPLE_DEFAULT,
    "#일반": PROMPT_SIMPLE_DEFAULT,
}


def call_gemini_api(system_prompt, user_prompt):
    """
    Helper function to call the Gemini API.
    """
    try:
        chat_session = model.start_chat(history=[])
        response = chat_session.send_message(
            f"SYSTEM_PROMPT: {system_prompt}\n\nUSER_PROMPT: {user_prompt}"
        )
        return response.text
    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return None

def clean_json_string(text):
    """
    Cleans the model's output to extract a valid JSON string.
    """
    # Find the first '{' and the last '}'
    start_index = text.find('{')
    end_index = text.rfind('}')
    
    if start_index != -1 and end_index != -1 and end_index > start_index:
        json_part = text[start_index : end_index + 1]
        # Remove common markdown artifacts like "```json\n" and "\n```"
        json_part = re.sub(r'^```json\s*', '', json_part, flags=re.IGNORECASE | re.MULTILINE)
        json_part = re.sub(r'\s*```$', '', json_part, flags=re.IGNORECASE | re.MULTILINE)
        return json_part.strip()
    return None

# --- process_notice_content 함수 (수정됨) ---
async def process_notice_content(title: str, body: str):
    """
    Processes notice content using a 2-step AI chain:
    1. Classify hashtag
    2. Extract structured JSON based on the hashtag
    """
    full_text = f"제목: {title}\n\n본문: {body}"
    ai_extracted_json = None
    hashtag = None
    
    # --- Step 1: Classify Hashtag ---
    try:
        hashtag_response = call_gemini_api(SYSTEM_PROMPT_CLASSIFY, full_text)
        if hashtag_response:
            # 해시태그가 여러 개 반환될 경우 (예: #장학, #국제교류) 첫 번째 것을 선택
            hashtag = hashtag_response.strip().split(',')[0].strip()
            if not hashtag.startswith('#'):
                hashtag = None # 유효하지 않은 응답
    except Exception as e:
        print(f"Error in Step 1 (Classification): {e}")
        hashtag = None # 실패 시
    
    if not hashtag or hashtag not in EXTRACTION_PROMPT_MAP:
        hashtag = "#일반" # 분류 실패 시 '일반'으로 강제 지정

    # --- Step 2: Extract JSON based on Hashtag ---
    try:
        # [수정] 해시태그에 맞는 프롬프트를 맵에서 선택
        # .get()을 사용하여 기본 프롬프트를 안전하게 참조
        extraction_prompt_template = EXTRACTION_PROMPT_MAP.get(hashtag, PROMPT_SIMPLE_DEFAULT)
        
        # [수정] 단순 프롬프트의 경우, {category_name}을 동적으로 삽입
        if extraction_prompt_template == PROMPT_SIMPLE_DEFAULT:
            extraction_prompt = extraction_prompt_template.format(
                category_name=hashtag,
                notice_text=full_text
            )
        else:
            # 정교한 프롬프트는 {notice_text}만 포맷팅
            extraction_prompt = extraction_prompt_template.format(notice_text=full_text)
            
        json_string_response = call_gemini_api(extraction_prompt, full_text)
        
        if json_string_response:
            cleaned_json_str = clean_json_string(json_string_response)
            if cleaned_json_str:
                ai_extracted_json = json.loads(cleaned_json_str)
            else:
                print(f"Could not find valid JSON in response for: {title}")
                ai_extracted_json = {"error": "Failed to parse JSON from AI response."}
        else:
            ai_extracted_json = {"error": "AI response was empty."}
            
    except json.JSONDecodeError as e:
        print(f"JSONDecodeError: {e} - Response was: {json_string_response}")
        ai_extracted_json = {"error": "Invalid JSON format received from AI."}
    except Exception as e:
        print(f"Error in Step 2 (Extraction): {e}")
        ai_extracted_json = {"error": f"An unexpected error occurred during extraction: {e}"}

    return hashtag, ai_extracted_json

# --- 기존 테스트용 main (수정됨) ---
if __name__ == "__main__":
    import asyncio

    async def main_test():
        # 테스트 1: #장학 (정교한 추출)
        title1 = "[Notice] 2025 Fall Semester Underwood Legacy Scholarship Notice"
        body1 = """
        1. Number of Recipients: 4 students per semester
        2. Scholarship Amount: KRW 2,000,000 per student
        3. Eligibility Requirements
           - Completion of at least four semesters of enrollment
           - Cumulative GPA of 3.5 or higher (on a 4.3 scale)
           - Enrollment in at least 12 credits in the preceding semester
        4. Application Timeline
           - Application Deadline: Oct 17 (Fri) 17:00, 2025 KST (Late submissions will NOT be accepted)
        """
        
        print("--- 테스트 1: #장학 (정교한 추출) ---")
        tag1, json1 = await process_notice_content(title1, body1)
        print(f"분류된 태그: {tag1}")
        print(f"추출된 JSON: {json.dumps(json1, indent=2, ensure_ascii=False)}\n")

        # 테스트 2: #국제교류 (정교한 추출 + 어학)
        title2 = "[CAMPUS Asia] 2025년 하반기 태국 출라롱콘대 단기교류 파견학생 모집"
        body2 = """
        [CAMPUS Asia사업] 2025년 하반기 CAMPUS Asia 사업 태국 출라롱콘대 단기교류 파견학생 모집 안내
        1. 지원 자격:
           - 학부 2~7학기 이수자
           - 총 평량평균 3.0/4.3 이상
           - 어학성적: TOEIC 850점 또는 TOEFL iBT 90점 이상
           - CAMPUS Asia 사업 참여 학과(경영학과, 경제학부) 학생
        2. 마감 기한: ~10/10(금) 17시까지
        """
        print("--- 테스트 2: #국제교류 (정교한 추출) ---")
        tag2, json2 = await process_notice_content(title2, body2)
        print(f"분류된 태그: {tag2}")
        print(f"추출된 JSON: {json.dumps(json2, indent=2, ensure_ascii=False)}\n")

        # 테스트 3: #행사 (단순 추출)
        title3 = "26학년도 전기 디지털애널리틱스융합협동과정 입학설명회 개최"
        body3 = """
        연세대학교 인공지능융합대학 디지털애널리틱스융합협동과정에서 26학년도 전기 입학설명회를 개최합니다.
        - 대상: 본교 학부생, 대학원생 및 외부 관심자 누구나
        - 일시 : 9월 29일(월) 오후 2시
        - 장소 : 온라인(Zoom) 및 오프라인(연세대학교 제1공학관 A528호) 동시 진행
        """
        print("--- 테스트 3: #행사 (단순 추출) ---")
        tag3, json3 = await process_notice_content(title3, body3)
        print(f"분류된 태그: {tag3}")
        print(f"추출된 JSON: {json.dumps(json3, indent=2, ensure_ascii=False)}\n")

    asyncio.run(main_test())