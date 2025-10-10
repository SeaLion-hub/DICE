# list_models.py
"""
네 Gemini API 키로 'generateContent'를 지원하는 모델들만 출력합니다.
- 출력 'raw_name'은 API가 보고하는 전체 이름 (예: models/gemini-1.5-flash-8b)
- 출력 'suggest'는 GenerativeModel(model=...)에 넣을 권장값 (예: gemini-1.5-flash-8b)
"""

from dotenv import load_dotenv
load_dotenv()

import os
import google.generativeai as genai

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("GEMINI_API_KEY is not set (check your .env or environment).")

genai.configure(api_key=api_key)

print("=== generateContent 지원 모델 목록 ===")
found = False
for m in genai.list_models():
    methods = getattr(m, "supported_generation_methods", []) or []
    if "generateContent" in methods:
        raw = m.name  # e.g., "models/gemini-1.5-flash-8b"
        # GenerativeModel()에는 prefix를 뺀 이름을 넣습니다.
        suggest = raw.replace("models/", "")
        print(f"- raw_name: {raw:35s}  |  suggest: {suggest}")
        found = True

if not found:
    print("(generateContent를 지원하는 모델이 이 키에서는 보이지 않습니다.)")

print("\n현재 .env의 GEMINI_MODEL =", os.getenv("GEMINI_MODEL"))
print("위 'suggest' 중 하나로 .env의 GEMINI_MODEL을 설정해 주세요.")
