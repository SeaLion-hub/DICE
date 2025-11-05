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


# --- [유지] 1단계: 제목+단과대 기반 배치 분류 프롬프트 (오류 수정을 위해 Few-shot 예시 추가) ---
SYSTEM_PROMPT_CLASSIFY_TITLE_BATCH = """
너는 연세대학교 공지사항의 단과대와 제목을 분석하여 가장 적합한 해시태그를 부여하는 AI 전문가다.
주어진 여러 개의 [공지사항] 목록 (각각 ID, 단과대, 제목 포함)을 읽고, 각 공지사항에 대해 아래 [카테고리 목록] 중에서 가장 적합한 해시태그를 **모두** 선택하라.
결과는 반드시 각 ID별로 해시태그 리스트를 포함하는 **단일 JSON 객체**로만 반환하라. (키: ID, 값: 해시태그 리스트)

[카테고리 목록]
- #학사: 수강신청, 졸업, 성적, 등록금, 시험, 재입학, 휴학, 복학, 교직과정 등 학업/학적 관련
- #장학: 국내/외 장학금, 학자금 대출, 근로장학생
- #행사: 특강, 워크숍, 설명회, 캠페인, 세미나, 포럼
- #취업: 채용, 인턴십, 창업 지원, 조교 모집, 리크루팅
- #국제교류: 교환학생, 해외 파견, 국제 계절학기
- #공모전/대회: 국내/외 공모전, 경진대회, 경시대회
- #일반: 다른 특정 카테고리에 속하지 않는 모든 공지 (시설, 규정 안내, 서비스 종료, 설문조사 등)

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

[학습 예시 (Few-shot Examples)]
- 제목: "2026학년도 교직과정 이수예정자 추가 선발 전형 안내" -> ["#학사"] (이유: '교직과정'은 학사 과정의 일부임. '선발' 단어에 혼동되지 말 것.)
- 제목: "객관식 OMR 채점 서비스 종료 및 대체 채점 안내" -> ["#일반"] (이유: 학사 일정이나 성적 자체가 아닌 '서비스'에 대한 행정 공지이므로 '#일반'임.)
- 제목: "2025-2학기 가계 곤란 장학금(Need-based) 시행 안내" -> ["#장학"] (이유: 명확한 '장학금' 공지.)
- 제목: "삼성전자 DS부문 채용 설명회 개최" -> ["#행사", "#취업"] (이유: '채용'에 대한 '설명회'이므로 두 태그 모두 해당.)
- 제목: "인문계열 융합전공(S/W) 신규 진입생 대상 설명회" -> ["#행사", "#학사"] (이유: '융합전공(학사)'에 대한 '설명회'이므로 두 태그 모두 해당.)
- 제목: "외솔관 승강기 안전검사 시행 안내" -> ["#일반"] (이유: '시설' 관련 공지이므로 '#일반'임.)

[중요 규칙]
1.  오직 [카테고리 목록]에 있는 7개의 태그만 사용해야 한다. (예: '#교과목' 같은 태그 생성 금지)
2.  제목만으로 판단이 애매하면 단과대 정보를 참고하되, [학습 예시]를 최우선으로 따른다.
3.  명확한 카테고리가 없으면 무조건 '#일반'을 선택한다.
4.  '#일반' 태그는 다른 태그와 절대 함께 사용할 수 없다. (결과는 `["#일반"]` 이어야 함)

**다른 설명 없이 위 [출력 형식]의 JSON 객체만 반환하라.**
"""

# --- [유지] 1단계: 분류 프롬프트 (기존 유지) ---
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

# --- [유지] 2단계: 추출 프롬프트 (프로필용 - 기존 유지) ---
PROMPT_SCHOLARSHIP = """
당신은 '장학금' 공지사항에서 프로필 비교에 사용할 수 있도록 핵심 자격 요건을 추출하는 AI입니다.
주어진 [공지 텍스트]를 꼼꼼히 분석하여, 아래 JSON 형식에 맞춰 **구조화된 정보**를 추출하세요.
[공지 텍스트]
{notice_text}
[/공지 텍스트]
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
JSON 출력 (다른 설명 없이 JSON만):
```json
{{
  "target_audience": "[참가/참여/적용/관련 대상 (예: '본교 학부생', '졸업예정자')]",
  "key_date_type": "[날짜 유형 (예: '접수 마감일')]",
  "key_date": "[핵심 날짜 (예: '11월 12일(수)까지')]"
}}
```"""

# --- [유지] 추출 프롬프트 매핑 (프로필용) ---
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


# --- [수정] 3단계: 세부 해시태그 추출을 위한 전문 프롬프트 (사용자 제공 버전) ---

# [수정] 모든 세부 프롬프트가 공유하는 기본 지시문 (JSON 반환 형식)
# (제목과 본문을 모두 분석하도록 복원)
SYSTEM_PROMPT_DETAIL_BASE = """
너는 주어진 [공지 제목]과 [공지 본문], 그리고 [대분류]를 참고하여, 사용자가 관심 있을 만한 **구체적인 키워드**를 해시태그로 추출하는 AI다.

[추출 규칙]
1.  **[공지 제목]과 [공지 본문]을 모두** 꼼꼼히 읽고 키워드를 찾아야 한다.
2.  결과는 반드시 JSON 리스트 형식(예: `["#태그1", "#태그2"]`)으로만 반환한다.
3.  추출할 태그가 없으면 빈 리스트 `[]`를 반환한다.
4.  1~5개의 가장 중요한 세부 해시태그만 추출한다.
5.  **[중요 규칙]** 아래 [대분류]별 [가이드라인]에 명시된 **[추출 목록]**에 있는 키워드만 해시태그로 추출해야 한다.
6.  **[특별 규칙]** 특정 키워드에 대한 예외 처리(추출 금지, 단어 변환)가 [가이드라인]에 명시된 경우, 반드시 그 규칙을 따른다.

아래는 [대분류]별 세부 추출 가이드라인이다.
"""

# [수정] 각 대분류별로 '세부 추출 가이드라인'을 다르게 설정
# (동적 키워드 예외는 제거, 문맥 예외는 유지)
DETAILED_HASHTAG_PROMPT_MAP = {
    "#학사": SYSTEM_PROMPT_DETAIL_BASE + """
[대분류] #학사
[추출 목록]
#소속변경, #ABEEK, #S/U, #신입생, #교직과정, #휴학, #복학, #수강신청, #졸업, #등록금, #교과목, #전공과목, #다전공
[가이드라인]
1.  [추출 목록]에서 [공지 제목] 또는 [공지 본문]과 일치하는 태그를 찾는다.
2.  **[예외: #휴학]** '#휴학' 태그는 공지 제목이나 본문이 '휴학 신청' 또는 '휴학 안내' 등 '휴학' 자체를 주제로 다룰 때만 추출한다. 만약 공지가 '휴학생'을 *대상*으로 하거나(예: '휴학생 대상 복학'), '휴학생 지원 가능' 등 조건부로만 언급한 경우, '#휴학' 태그를 **추출하지 않는다.**
3.  **[예외: #신입생]** '#신입생' 태그는 공지 제목이나 본문이 '신입생 OT', '신입생 수강신청' 등 '신입생' 자체를 주제로 다룰 때만 추출한다. 만약 공지가 '신입생 포함' 등 '신입생'을 *대상*의 일부로만 언급한 경우, '#신입생' 태그를 **추출하지 않는다.**
""",
    "#장학": SYSTEM_PROMPT_DETAIL_BASE + """
[대분류] #장학
[추출 목록]
#가계곤란, #국가장학, #근로장학, #성적우수, #생활비
[가이드라인]
1.  [추출 목록]에서 [공지 제목] 또는 [공지 본문]과 일치하는 태그를 찾는다.
2.  **[단어 변환 규칙]** [공지 제목] 또는 [공지 본문]에서 'need based', 'needbased' 또는 '가계곤란'이라는 단어가 발견되면, 모두 **"#가계곤란"** 태그 하나로 통일하여 추출한다.
3.  (그 외 예외 없음)
""",
    "#취업": SYSTEM_PROMPT_DETAIL_BASE + """
[대분류] #취업
[추출 목록]
#채용, #인턴십, #현장실습, #강사, #조교, #채용설명회, #취업특강, #창업
[가이드라인]
1.  오직 [추출 목록]에 있는 단어만 [공지 제목] 또는 [공지 본문]에서 해시태그로 추출한다.
2.  (예외 없음)
""",
    "#행사": SYSTEM_PROMPT_DETAIL_BASE + """
[대분류] #행사
[추출 목록]
#특강, #워크숍, #세미나, #설명회, #포럼, #지원, #교육, #프로그램
[가이드라인]
1.  오직 [추출 목록]에 있는 단어만 [공지 제목] 또는 [공지 본문]에서 해시태그로 추출한다.
2.  (예외 없음)
""",
    "#공모전/대회": SYSTEM_PROMPT_DETAIL_BASE + """
[대분류] #공모전/대회
[추출 목록]
#공모전, #경진대회, #디자인, #숏폼, #영상, #아이디어, #논문, #학생설계전공, #마이크로전공
[가이드라인]
1.  오직 [추출 목록]에 있는 단어만 [공지 제목] 또는 [공지 본문]에서 해시태그로 추출한다.
2.  (예외 없음)
""",
    "#국제교류": SYSTEM_PROMPT_DETAIL_BASE + """
[대분류] #국제교류
[추출 목록]
#교환학생, #파견, #campusasia, #글로벌, #단기, #하계, #동계, #어학연수, #해외봉사, #일본, #미국, #중국
[가이드라인]
1.  오직 [추출 목록]에 있는 단어만 [공지 제목] 또는 [공지 본문]에서 해시태그로 추출한다.
2.  (예외 없음)
"""
}


# --- [공통 함수] API 호출 및 JSON 정리 ---
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
            json_part = text[start_index : end_index + 1].strip()
        else:
            print(f"Warning: No clear JSON structure found in text: {text[:100]}...")
            return None

    # 추출된 JSON 문자열이 유효한지 최종 확인
    try:
        json.loads(json_part)
        return json_part
    except json.JSONDecodeError:
        print(f"Warning: Failed to parse potential JSON string: {json_part}")
        return None


# --- [유지] 1단계: 해시태그 분류 함수 (JSON 응답 처리) ---
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


# --- [유지] 제목+단과대 기반 배치 분류 함수 ---
def classify_hashtags_from_title_batch(notices_info: List[Dict[str, str]]) -> Dict[str, List[str]]:
    """
    Classifies hashtags for a batch of notices based on title and college name.
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
            SYSTEM_PROMPT_CLASSIFY_TITLE_BATCH, # [수정] 강화된 프롬프트 사용
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


# --- [유지] 2단계: 구조화된 정보 추출 함수 (프로필용) ---
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


# --- [유지] 3단계: 세부 해시태그 추출 함수 (제목/본문 동시 분석, #기타 반환) ---
def extract_detailed_hashtags(title: str, body_text: str, main_category: str) -> List[str]:
    """
    주어진 제목, 본문, 대분류에 따라 [필수 추출 목록] + [예외 규칙]에 기반한
    세부 해시태그를 추출합니다.
    (추출된 태그가 없으면 #기타 를 반환합니다)
    """
    if not main_category:
        return []
    if not body_text: # 본문이 비어있어도 제목으로 분석해야 하므로 body_text를 빈 문자열로 설정
        body_text = ""
    if not title: # 제목이 비어있어도 본문으로 분석해야 하므로 title을 빈 문자열로 설정
        title = ""

    # #일반 또는 맵에 없는 카테고리는 세부 추출 안 함 (빈 리스트 반환)
    if main_category not in DETAILED_HASHTAG_PROMPT_MAP:
        print(f"Skipping detailed extraction for '{main_category}' as it has no defined prompt.")
        return []

    system_prompt = DETAILED_HASHTAG_PROMPT_MAP[main_category]
    
    # [유지] 사용자 프롬프트에 제목(title)과 본문(body_text)을 모두 포함
    user_prompt = (
        f"[대분류]\n{main_category}\n\n"
        f"[공지 제목]\n{title}\n\n"
        f"[공지 본문]\n{body_text}\n[/공지 본문]"
    )
    
    valid_hashtags = [] # 추출된 태그를 담을 리스트

    try:
        # call_gemini_api는 이미 JSON 파싱 및 정리를 처리함
        json_response = call_gemini_api(
            system_prompt,
            user_prompt,
            is_json_output=True
        )

        if isinstance(json_response, list):
            # 응답이 문자열 리스트인지 한 번 더 확인
            valid_hashtags = [tag for tag in json_response if isinstance(tag, str) and tag.startswith("#")]
        else:
            print(f"Error: Detailed hashtag response was not a list for category {main_category}. Got: {json_response}")
            # valid_hashtags는 여전히 빈 리스트 []

    except Exception as e:
        print(f"Error during detailed hashtag extraction: {e}")
        if "429" in str(e): 
            raise e
        # valid_hashtags는 여전히 빈 리스트 []

    # --- [신규 규칙 적용] ---
    # main_category는 '#일반'이 아님이 보장됨 (위에서 return [] 처리됨)
    # 따라서, 처리 가능한 카테고리임에도 불구하고 세부 태그가 없다면 #기타 반환
    if not valid_hashtags:
        return ["#기타"]
    else:
        # 중복 제거 후 반환
        return list(dict.fromkeys(valid_hashtags))


# --- [수정] __main__ 테스트 블록 (모든 기능 테스트) ---
if __name__ == "__main__":
    
    # --- 1단계: 제목 기반 배치 분류 테스트 (Few-shot으로 강화된 프롬프트) ---
    print("\n===== 1단계: 제목 기반 배치 분류 테스트 (강화 프롬프트) =====")
    notices_info_batch = [
        # [버그 수정 테스트 1] (교직과정 -> #학사)
        {"id": "music_1", "title": "2026학년도 교직과정 이수예정자 추가 선발 전형 안내", "college_name": "음악대학"},
        # [버그 수정 테스트 2] (OMR 채점 -> #일반)
        {"id": "social_1", "title": "[학생용] 객관식 OMR 채점 서비스 종료 및 대체 채점 (Bubble Sheet) 안내", "college_name": "사회과학대학"},
        {"id": "n1", "title": "K-NIBRT 취업 특강 및 채용 세미나", "college_name": "약학대학"},
        {"id": "n3", "title": "학사포탈 졸업자가진단 점검 시 참고사항", "college_name": "사회과학대학"},
        {"id": "n4", "title": "[마감] 2025-2학기 강사 공개채용", "college_name": "교육과학대학"},
        {"id": "n5", "title": "태국 출라롱콘대 단기교류 파견학생 모집 안내", "college_name": "치과대학"},
        {"id": "n6", "title": "외솔관 승강기 검사 안내", "college_name": "문과대학"},
        {"id": "n7", "title": "국가장학금 2차 신청", "college_name": "의과대학"},
    ]
    print(f"--- 테스트 배치 (총 {len(notices_info_batch)}개) ---")
    batch_results = classify_hashtags_from_title_batch(notices_info_batch)
    print("배치 분류 결과 (Dict[id, List[tag]]):")
    print(json.dumps(batch_results, indent=2, ensure_ascii=False))


    # --- 3단계: 세부 해시태그 추출 테스트 ---
    print("\n\n===== 3단계: 세부 해시태그 추출 테스트 =====")
    
    # [테스트 1] (제목에서 #논문 추출 - 제목 분석 ON)
    test_title_contest = "2025 지역사회건강조사 결과 활용 학술논문 공모전"
    test_body_contest = "관리자 2025 06 30 조회수 873"
    tags_contest = extract_detailed_hashtags(test_title_contest, test_body_contest, "#공모전/대회")
    print(f"\n#공모전/대회 테스트 (제목: {test_title_contest[:30]}...)")
    print(f"-> 결과: {tags_contest}") # 기대: [#논문, #공모전]
    
    # [규칙 테스트 2: #학사 예외] (#휴학, #신입생 제외)
    test_title_academic = "2026학년도 교직과정 이수예정자 추가 선발"
    test_body_academic = "교직과정 안내. 지원자격: 휴학생 가능, 신입생 제외"
    tags_academic = extract_detailed_hashtags(test_title_academic, test_body_academic, "#학사")
    print(f"\n#학사 예외 테스트 (제목: {test_title_academic[:30]}...)")
    print(f"-> 결과: {tags_academic}") # 기대: [#교직과정] (not #휴학, #신입생)
    
    # [규칙 테스트 3: #장학 변환] (needbased -> #가계곤란)
    test_title_scholar = "2025-2학기 가계 곤란 장학금 (Need-based) 및 블루버터플라이 시행"
    test_body_scholar = "소득분위 8분위 이하. need based fellowship."
    tags_scholar = extract_detailed_hashtags(test_title_scholar, test_body_scholar, "#장학")
    print(f"\n#장학 변환 테스트 (제목: {test_title_scholar[:30]}...)")
    print(f"-> 결과: {tags_scholar}") # 기대: [#가계곤란] (#블루버터플라이는 동적 예외라 제거됨)
    
    # [규칙 테스트 4: #기타 반환] (#학사 목록에 없는 키워드)
    test_title_other = "OMR 채점 서비스 종료 안내"
    test_body_other = "Bubble Sheet 서비스로 대체됩니다."
    tags_other = extract_detailed_hashtags(test_title_other, test_body_other, "#학사")
    print(f"\n#기타 반환 테스트 (제목: {test_title_other[:30]}...)")
    print(f"-> 결과: {tags_other}") # 기대: [#기타]
    
    # [규칙 테스트 5: #취업] (제목 분석 ON, 동적 예외 OFF)
    test_title_job = "2025-2학기 일반조교 및 삼성전자 개발 직무 채용"
    test_body_job = "연세대학교 간호대학에서 2025-2학기 일반조교를 채용합니다. 삼성병원 출신 우대."
    tags_job = extract_detailed_hashtags(test_title_job, test_body_job, "#취업")
    print(f"\n#취업 테스트 (제목: {test_title_job[:30]}...)")
    print(f"-> 결과: {tags_job}") # 기대: [#조교, #채용] (#삼성전자, #개발은 동적 예외라 제거됨)
