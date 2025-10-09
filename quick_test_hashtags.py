# quick_test_hashtags.py (임시)
from ai_processor import extract_hashtags_from_title

print(extract_hashtags_from_title("[대학원] 2025학년도 2학기 재입학 안내(Re-Admission)"))
# 기대: {'hashtags': ['#학사']}

print(extract_hashtags_from_title("신학기 출판물 불법복제 예방활동"))
# 기대: {'hashtags': ['#일반']}

print(extract_hashtags_from_title("[국제처] 2026학년도 Google 해외 인턴십 참가자 모집"))
# 기대: {'hashtags': ['#취업', '#국제교류']}
