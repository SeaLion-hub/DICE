# ai_processor.py (배치 분류 기능 추가)
import google.generativeai as genai
import os
import re
import json
from dotenv import load_dotenv
from typing import Dict, Any, List

load_dotenv()

# --- API 설정 (기존과 동일) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env file")
genai.configure(api_key=GEMINI_API_KEY)

generation_config = genai.GenerationConfig(
    temperature=0.1,
    top_p=1.0,
    top_k=1,
    max_output_tokens=4096,  # 배치 처리를 위해 증가
    response_mime_type="application/json",
)

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

model = genai.GenerativeModel(
    model_name=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    generation_config=generation_config,
    safety_settings=safety_settings,
)
# --- API 설정 종료 ---


# --- [신규] 제목+단과대 기반 배치 분류 프롬프트 ---
SYSTEM_PROMPT_CLASSIFY_TITLE_BATCH = """
너는 연세대학교 공지사항의 단과대와 제목을 분석하여 가장 적합한 해시태그를 부여하는 AI 전문가다.
주어진 여러 개의 [공지사항] 목록 (각각 ID, 단과대, 제목 포함)을 읽고, 각 공지사항에 대해 아래 [카테고리 목록] 중에서 가장 적합한 해시태그를 **모두** 선택하라.
결과는 반드시 각 ID별로 해시태그 리스트를 포함하는 **단일 JSON 객체**로만 반환하라. (키: ID, 값: 해시태그 리스트)

[카테고리 목록]
- #학사: 수강신청, 졸업, 성적, 등록금, 시험, 재입학, 휴학, 복학 등 학업 관련
- #장학: 국내/외 장학금, 학자금 대출, 근로장학생
- #행사: 특강, 워크숍, 설명회, 캠페인, 세미나
- #취업: 채용, 인턴십, 창업 지원, 조교 모집
- #국제교류: 교환학생, 해외 파견, 국제 계절학기
- #공모전/대회: 국내/외 공모전, 경진대회, 경시대회
- #일반: 다른 특정 카테고리에 속하지 않는 모든 공지 (시설, 규정 안내 등)

[입력 형식] (JSON 배열)
[
  {"id": "고유ID1", "college": "단과대명1", "title": "공지 제목1"},
  {"id": "고유ID2", "college": "단과대명2", "title": "공지 제목2"},
  ...
]

[출력 형식] (단일 JSON 객체)
{
  "고유ID1": ["#태그A", "#태그B"],
  "고유ID2": ["#태그C"],
  ...
}

[작업 절차]
1. [우선순위 판단]: 제목에 '장학', '채용', '인턴', '공모전', '대회', '설명회', '워크숍' 키워드가 있는지 확인하고 후보 태그 선정.
2. [문맥적 예외 처리]: '모집' 단어 처리. 학업/진학 관련은 '#학사', 그 외는 '#취업'.
3. [종합 분석]: 단과대 정보와 제목 전체 내용을 종합적으로 고려하여 핵심 주제 파악.
4. [태그 선택]: 분석 내용 기반으로 [카테고리 목록]에서 가장 적합한 태그를 **모두** 선택.
5. [최종 규칙]:
   - 명확한 카테고리 없으면 '#일반' 선택.
   - '#일반'은 다른 태그와 함께 사용하지 않음 (결과는 ["#일반"] 이어야 함).
   - 만약 어떤 ID에 대해 태그를 찾지 못하면 빈 리스트 `[]`를 값으로 사용.

**다른 설명 없이 위 [출력 형식]의 JSON 객체만 반환하라.**
"""

# --- 1단계: 분류 프롬프트 (기존 유지) ---
SYSTEM_PROMPT_CLASSIFY = """
당신은 연세대학교 공지사항을 분류하는 AI입니다.
주어진 [공지 텍스트]를 읽고, 다음 7가지 카테고리 중 가장 적합한 해시태그 1개를 **JSON 배열 형식**으로 반환해 주세요:
[#학사, #장학, #취업, #행사, #공모전/대회, #국제교류, #일반]

규칙:
1. 오직 7개의 태그 중 **하나만** 선택해야 합니다.
2. 응답은 반드시 `["#선택된태그"]` 형식의 JSON 배열이어야 합니다.
3. 추가 설명이나 다른 텍스트 없이 JSON 배열만 반환해야 합니다. (예: `["#학사"]`)
4. 2개 이상 해당되면, 가장 중요하다고 생각되는 1개만 선택하여 JSON 배열에 넣습니다.
"""

# --- 2단계: 추출 프롬프트 (기존과 동일 유지) ---
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

JSON 출력 (다른 설명 없이 JSON만):
```json
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
```"""

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

JSON 출력 (다른 설명 없이 JSON만):
```json
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
```"""

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

JSON 출력 (다른 설명 없이 JSON만):
```json
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
```"""

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

JSON 출력 (다른 설명 없이 JSON만):
```json
{{
  "target_audience": "[참가/참여/적용/관련 대상 (예: '본교 학부생', '졸업예정자')]",
  "key_date_type": "[날짜 유형 (예: '접수 마감일')]",
  "key_date": "[핵심 날짜 (예: '11월 12일(수)까지')]"
}}
```"""

# --- 추출 프롬프트 매핑 (기존과 동일) ---
EXTRACTION_PROMPT_MAP = {
    "#장학": PROMPT_SCHOLARSHIP,
    "#취업": PROMPT_RECRUITMENT,
    "#국제교류": PROMPT_INTERNATIONAL,
    "#학사": PROMPT_SIMPLE_DEFAULT,
    "#행사": PROMPT_SIMPLE_DEFAULT,
    "#공모전/대회": PROMPT_SIMPLE_DEFAULT,
    "#일반": PROMPT_SIMPLE_DEFAULT,
}
# --- 허용된 카테고리 목록 (검증용) ---
ALLOWED_CATEGORIES = list(EXTRACTION_PROMPT_MAP.keys())


def call_gemini_api(system_prompt, user_prompt, is_json_output=False):
    """
    Helper function to call the Gemini API.
    """
    try:
        chat_session = model.start_chat(history=[
            {'role': 'user', 'parts': [system_prompt]},
            {'role': 'model', 'parts': ["OK. JSON 형식 규칙을 이해했습니다. 텍스트를 제공해주세요."]}
        ])
        response = chat_session.send_message(user_prompt)

        if is_json_output:
            try:
                cleaned_response_text = clean_json_string(response.text)
                if cleaned_response_text:
                    return json.loads(cleaned_response_text)
                else:
                    print(f"Error: clean_json_string returned None for response: {response.text}")
                    return None
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON from Gemini: {e}. Response text: {response.text}")
                return None
            except Exception as e:
                print(f"Unexpected error parsing JSON response: {e}. Response text: {response.text}")
                return None
        else:
            return response.text

    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        if "429" in str(e):
            raise e
        return None

def clean_json_string(text):
    """
    모델의 출력을 정리하여 유효한 JSON 문자열을 추출합니다 (마크다운 포함 처리).
    후행 문자에 대해 더 견고하게 처리합니다.
    """
    if not text:
        return None

    # 먼저 마크다운 블록 찾기 시도
    match = re.search(r'```json\s*([\s\S]*?)\s*```', text, re.IGNORECASE | re.MULTILINE)
    if match:
        json_part = match.group(1).strip()
        # JSON 객체/배열 부분만 유지하도록 보장
        first_brace = json_part.find('{')
        first_bracket = json_part.find('[')
        last_brace = json_part.rfind('}')
        last_bracket = json_part.rfind(']')

        start_index = -1
        if first_brace != -1 and first_bracket != -1:
            start_index = min(first_brace, first_bracket)
        elif first_brace != -1:
            start_index = first_brace
        elif first_bracket != -1:
            start_index = first_bracket

        end_index = -1
        if last_brace != -1 and last_bracket != -1:
            end_index = max(last_brace, last_bracket)
        elif last_brace != -1:
            end_index = last_brace
        elif last_bracket != -1:
            end_index = last_bracket

        if start_index != -1 and end_index != -1 and end_index >= start_index:
             # 마크다운 내에서 JSON 부분만 안정적으로 추출
             try:
                 # 추출된 부분이 유효한 JSON인지 확인 시도
                 json.loads(json_part[start_index : end_index + 1])
                 return json_part[start_index : end_index + 1]
             except json.JSONDecodeError:
                 # 마크다운 블록 내에서도 JSON 파싱 실패 시 None 반환
                 print(f"Warning: Failed to parse JSON even within markdown: {json_part[start_index : end_index + 1]}")
                 return None
        else:
             print(f"Warning: Could not reliably extract JSON boundaries from markdown block: {json_part}")
             return None # 마크다운 블록에서 JSON 경계를 안정적으로 추출할 수 없음

    else:
        # 마크다운이 없으면, 첫 { 또는 [ 와 마지막 } 또는 ] 찾기
        first_brace = text.find('{')
        first_bracket = text.find('[')
        last_brace = text.rfind('}')
        last_bracket = text.rfind(']')

        start_index = -1
        if first_brace != -1 and first_bracket != -1:
            start_index = min(first_brace, first_bracket)
        elif first_brace != -1:
            start_index = first_brace
        elif first_bracket != -1:
            start_index = first_bracket

        end_index = -1
        if last_brace != -1 and last_bracket != -1:
            end_index = max(last_brace, last_bracket)
        elif last_brace != -1:
            end_index = last_brace
        elif last_bracket != -1:
            end_index = last_bracket

        if start_index != -1 and end_index != -1 and end_index >= start_index:
            # 찾은 괄호/대괄호 사이의 부분만 추출
            potential_json = text[start_index : end_index + 1].strip()
            # 추출된 부분이 유효한 JSON인지 확인 시도
            try:
                json.loads(potential_json)
                return potential_json
            except json.JSONDecodeError:
                # 파싱 실패 시 None 반환
                print(f"Warning: Failed to parse potential JSON string: {potential_json}")
                return None
        else:
            # 명확한 JSON 구조를 찾을 수 없음
            print(f"Warning: No clear JSON structure found in text: {text[:100]}...")
            return None


# --- [기존] 1단계: 해시태그 분류 함수 (JSON 응답 처리) ---
def classify_notice_category(title: str, body: str) -> str:
    """
    Processes notice content to classify a single category hashtag.
    Returns the hashtag string (e.g., "#학사") or "#일반" on failure.
    """
    full_text = f"제목: {title}\n\n본문: {body}"
    hashtag = "#일반"

    try:
        json_response = call_gemini_api(SYSTEM_PROMPT_CLASSIFY, full_text, is_json_output=True)

        if isinstance(json_response, list) and len(json_response) == 1:
            potential_hashtag = json_response[0]
            if isinstance(potential_hashtag, str) and potential_hashtag in ALLOWED_CATEGORIES:
                hashtag = potential_hashtag
            else:
                print(f"Invalid or unknown hashtag '{potential_hashtag}' received in JSON, defaulting to #일반 for: {title[:30]}...")
        else:
            print(f"Unexpected JSON format received for classification: {json_response}. Defaulting to #일반 for: {title[:30]}...")

    except Exception as e:
        print(f"Error during classification for '{title[:30]}...': {e}")

    return hashtag


# --- [신규] 제목+단과대 기반 배치 분류 함수 ---
def classify_hashtags_from_title_batch(notices_info: List[Dict[str, str]]) -> Dict[str, List[str]]:
    """
    Classifies hashtags for a batch of notices based on title and college name.

    Args:
        notices_info: List of dicts, each like {"id": "unique_id", "title": "...", "college_name": "..."}.

    Returns:
        Dict mapping notice 'id' to a list of hashtags (e.g., {"id1": ["#tagA"], "id2": []}).
        Returns an empty dict on major failure.
    """
    if not notices_info:
        return {}

    input_data = [
        {"id": info.get('id', ''), "college": info.get('college_name', ''), "title": info.get('title', '')}
        for info in notices_info
    ]
    user_prompt_json = json.dumps(input_data, ensure_ascii=False, indent=2)

    results = {}
    for info in notices_info:
        results[info.get('id', '')] = []

    try:
        batch_response = call_gemini_api(
            SYSTEM_PROMPT_CLASSIFY_TITLE_BATCH,
            user_prompt_json,
            is_json_output=True
        )

        if isinstance(batch_response, dict):
            for notice_id, hashtags in batch_response.items():
                if notice_id in results:
                    if isinstance(hashtags, list):
                        valid_hashtags = [tag for tag in hashtags if isinstance(tag, str) and tag in ALLOWED_CATEGORIES]

                        if "#일반" in valid_hashtags:
                            results[notice_id] = ["#일반"]
                        elif valid_hashtags:
                            results[notice_id] = valid_hashtags
                        else:
                            if hashtags:
                                print(f"Warning: Rcvd invalid tags {hashtags} for ID '{notice_id}'. Defaulting to [].")
                            results[notice_id] = []
                    else:
                        print(f"Warning: Hashtags for ID '{notice_id}' is not a list: {hashtags}. Defaulting to [].")
                        results[notice_id] = []
                else:
                    print(f"Warning: Received result for unknown ID '{notice_id}' in batch response.")
        else:
            print(f"Error: Batch classification response was not a dict: {batch_response}")

    except Exception as e:
        print(f"Error during batch classification API call: {e}")
        if "429" in str(e):
            raise e

    return results


# --- 2단계: 구조화된 정보 추출 함수 (JSON 응답 처리 강화) ---
def extract_structured_info(title: str, body: str, category: str) -> Dict[str, Any]:
    """
    Extracts structured JSON based on the provided category hashtag.
    Handles potential JSON parsing errors.
    """
    full_text = f"제목: {title}\n\n본문: {body}"
    ai_extracted_json = None

    if not category or category not in EXTRACTION_PROMPT_MAP:
        category = "#일반"

    try:
        extraction_prompt_template = EXTRACTION_PROMPT_MAP.get(category, PROMPT_SIMPLE_DEFAULT)

        if extraction_prompt_template == PROMPT_SIMPLE_DEFAULT:
            system_prompt_for_extraction = extraction_prompt_template.replace(
                "{category_name}", category
            ).replace("{notice_text}", "")
        else:
            system_prompt_for_extraction = extraction_prompt_template.replace(
                "{notice_text}", ""
            )

        json_response = call_gemini_api(system_prompt_for_extraction, full_text, is_json_output=True)

        if isinstance(json_response, dict):
            ai_extracted_json = json_response
        elif json_response is None:
            ai_extracted_json = {"error": "Failed to get or parse JSON response from AI."}
        else:
            print(f"Unexpected data type received from structured extraction: {type(json_response)}. Response: {json_response}")
            ai_extracted_json = {"error": f"Unexpected data type: {type(json_response)}"}

    except Exception as e:
        print(f"Error during extraction for '{title[:30]}...': {e}")
        ai_extracted_json = {"error": f"An unexpected error occurred during extraction: {e}"}
        if "429" in str(e):
            raise e

    return ai_extracted_json if ai_extracted_json else {"error": "Extraction failed"}


# --- 기존 제목 기반 해시태그 추출 함수 (유지) ---
def extract_hashtags_from_title(title: str, college: str = None):
    print(f"Warning: extract_hashtags_from_title called but has no implementation for title: {title}")
    return {"hashtags": None}

# --- 기존 테스트용 main (유지) ---
if __name__ == "__main__":
    # 테스트 1: #장학 (분류 -> 추출)
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

    print("--- 테스트 1: #장학 (분류 -> 추출) ---")
    tag1 = classify_notice_category(title1, body1)
    print(f"분류된 태그: {tag1}")
    if tag1:
        json1 = extract_structured_info(title1, body1, tag1)
        print(f"추출된 JSON: {json.dumps(json1, indent=2, ensure_ascii=False)}\n")

    # 테스트 2: #국제교류 (분류 -> 추출)
    title2 = "[CAMPUS Asia] 2025년 하반기 태국 출라롱콘대 단기교류 파견학생 모집"
    body2 = """
    [CAMPUS Asia사업] 2025년 하반기 CAMPUS Asia 사업 태국 출라롱콘대 단기교류 파견학생 모집 안내
    1. 지원 자격:
       - 학부 2~7학기 이수자
       - 이 평량평균 3.0/4.3 이상
       - 어학성적: TOEIC 850점 또는 TOEFL iBT 90점 이상
       - CAMPUS Asia 사업 참여 학과(경영학과, 경제학부) 학생
    2. 마감 기한: ~10/10(금) 17시까지
    """
    print("--- 테스트 2: #국제교류 (분류 -> 추출) ---")
    tag2 = classify_notice_category(title2, body2)
    print(f"분류된 태그: {tag2}")
    if tag2:
         json2 = extract_structured_info(title2, body2, tag2)
         print(f"추출된 JSON: {json.dumps(json2, indent=2, ensure_ascii=False)}\n")

   # ai_processor.py 파일 맨 아래



    # 예시 1: #장학 (기존)
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

    # 예시 2: #국제교류 (기존)
    title2 = "[CAMPUS Asia] 2025년 하반기 태국 출라롱콘대 단기교류 파견학생 모집"
    body2 = """
    [CAMPUS Asia사업] 2025년 하반기 CAMPUS Asia 사업 태국 출라롱콘대 단기교류 파견학생 모집 안내
    1. 지원 자격:
       - 학부 2~7학기 이수자
       - 이 평량평균 3.0/4.3 이상
       - 어학성적: TOEIC 850점 또는 TOEFL iBT 90점 이상
       - CAMPUS Asia 사업 참여 학과(경영학과, 경제학부) 학생
    2. 마감 기한: ~10/10(금) 17시까지
    """

    # 예시 3: #행사 (기존)
    title3 = "26학년도 전기 디지털애널리틱스융합협동과정 입학설명회 개최"
    body3 = """
    연세대학교 인공지능융합대학 디지털애널리틱스융합협동과정에서 26학년도 전기 입학설명회를 개최합니다.
    - 대상: 본교 학부생, 대학원생 및 외부 관심자 누구나
    - 일시 : 9월 29일(월) 오후 2시
    - 장소 : 온라인(Zoom) 및 오프라인(연세대학교 제1공학관 A528호) 동시 진행
    """

    # 예시 4: #취업 (자격 요건, 날짜 포함)
    title4 = "[공채모집] 2025-2 실습교육코디네이터 및 조교 공채 모집"
    body4 = """
    간호대학 2025-2학기 실습교육코디네이터 및 조교(순환,일반) 채용공고
    - 지원 자격: 간호학 석사 학위 이상 소지자, 임상 경력 3년 이상
    - 우대 사항: 교육 경력자
    - 접수 기간: 2025년 6월 9일(월) ~ 6월 19일(목) 17:00 까지
    - 제출 서류: 지원서, 학위증명서, 경력증명서 등
    """ # 부분 참조하여 생성

    # 예시 5: #공모전/대회 (대상, 날짜 포함)
    title5 = "[통일보건의료센터] 제11회 세브란스 통일의 밤 - 숏폼 영상 공모전 참여 안내"
    body5 = """
    제11회 세브란스 통일의 밤 숏폼 영상 공모전
    - 주제: "통일 이후, 보건의료인으로서 나의 역할을 상상하다."
    - 참가자격: 연세의료원 교직원, 의대, 치대, 간호대, 약대, 보건대학원 학생 (팀 또는 개인)
    - 접수기한: 2025년 11월 12일 수요일 자정까지
    - 참가 방법: 신청 링크 통해 접수
    """ # 부분 참조하여 생성

    # --- 테스트 실행 ---
    test_cases = [
        {"title": title1, "body": body1, "expected_category": "#장학"},
        {"title": title2, "body": body2, "expected_category": "#국제교류"},
        {"title": title3, "body": body3, "expected_category": "#행사"},
        {"title": title4, "body": body4, "expected_category": "#취업"},
        {"title": title5, "body": body5, "expected_category": "#공모전/대회"},
    ]

    print("===== 자격 요건 및 시간 추출 프롬프트 테스트 시작 =====")

    for i, case in enumerate(test_cases):
        print(f"\n--- 테스트 {i+1}: {case['expected_category']} ---")
        title = case["title"]
        body = case["body"]

        # 1단계: 카테고리 분류 (추출 프롬프트 선택을 위해)
        # 실제 운영 시에는 이미 분류된 카테고리를 사용하면 됩니다.
        classified_category = classify_notice_category(title, body)
        print(f"분류된 카테고리: {classified_category} (예상: {case['expected_category']})")

        # 카테고리가 예상과 다를 경우 경고
        if classified_category != case['expected_category']:
            print(f"⚠️ 경고: 분류된 카테고리가 예상과 다릅니다! 추출은 '{classified_category}' 기준으로 진행됩니다.")
 
        # 2단계: 구조화된 정보 추출 (자격 요건, 시간 등)
        extracted_json = extract_structured_info(title, body, classified_category) # 분류된 카테고리 사용

        # 결과 출력 (JSON 형식으로 보기 좋게)
        print("추출된 JSON:")
        print(json.dumps(extracted_json, indent=2, ensure_ascii=False))
        print("-" * (len(f"--- 테스트 {i+1}: {case['expected_category']} ---"))) # 구분선

    print("\n===== 테스트 종료 =====")

    # --- 기존 배치 분류 테스트 (옵션) ---
    # print("\n===== 제목 기반 배치 분류 테스트 =====")
    # notices_info_batch = [ ... ] # 기존 배치 테스트 코드
    # batch_results = classify_hashtags_from_title_batch(notices_info_batch)
    # print("배치 분류 결과 (Dict[id, List[tag]]):")
    # print(json.dumps(batch_results, indent=2, ensure_ascii=False))

    # 테스트 4: 배치 분류
    print("\n===== 제목 기반 배치 분류 테스트 =====")
    notices_info_batch = [
        {"id": "n1", "title": "K-NIBRT 취업 특강 및 채용 세미나", "college_name": "약학대학"},
        {"id": "n2", "title": "Predoc Fellow(박사전 과정) 모집", "college_name": "상경대학"},
        {"id": "n3", "title": "학사포탈 졸업자가진단 점검 시 참고사항", "college_name": "사회과학대학"},
        {"id": "n4", "title": "[마감] 2025-2학기 강사 공개채용 (대학원_정신병리)", "college_name": "교육과학대학"},
        {"id": "n5", "title": "태국 출라롱콘대 단기교류 파견학생 모집 안내", "college_name": "치과대학"},
        {"id": "n6", "title": "외솔관 승강기 검사 안내", "college_name": "문과대학"},
        {"id": "n7", "title": "국가장학금 2차 신청 [트랙2 참여자 포함]", "college_name": "의과대학"},
        {"id": "n8", "title": "잘못된 제목 정보", "college_name": "알수없음"},
    ]
    print(f"--- 테스트 배치 (총 {len(notices_info_batch)}개) ---")
    batch_results = classify_hashtags_from_title_batch(notices_info_batch)
    print("배치 분류 결과 (Dict[id, List[tag]]):")
    print(json.dumps(batch_results, indent=2, ensure_ascii=False))