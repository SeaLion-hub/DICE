-- 009_add_raw_text.sql (수정된 전체 코드)
-- body_text 갱신 후, search_vector를 A, B, C, D 가중치 모두 포함하여 갱신합니다.
-- [수정] 'B' 가중치 로직을 005_search_fts.sql(트리거)와 동일하게 수정 (detailed_hashtags 포함)

BEGIN;

UPDATE notices n
SET search_vector =
  setweight(to_tsvector('simple', coalesce(n.title, '')), 'A') ||
  
  -- [수정됨] B 가중치: hashtags_ai와 detailed_hashtags 모두 포함
  setweight(
    to_tsvector(
      'simple',
      array_to_string(
        array_cat(
          COALESCE(n.hashtags_ai, ARRAY[]::text[]),
          COALESCE(n.detailed_hashtags, ARRAY[]::text[])
        ),
        ' '
      )
    ),
    'B'
  ) ||
  
  setweight(to_tsvector('simple', coalesce(n.body_text, '')), 'C') ||
  setweight(to_tsvector('simple', coalesce(c_syn.synonyms, '')), 'D') -- 'D' 가중치 (단과대학 동의어)
FROM (
  -- 005_search_fts.sql의 논리와 동일하게 동의어 생성
  SELECT key,
    CASE key
      WHEN 'main' THEN '메인'
      WHEN 'liberal' THEN '문과'
      WHEN 'business' THEN '상경'
      WHEN 'management' THEN '경영'
      WHEN 'engineering' THEN '공과 공학 공대'
      WHEN 'life' THEN '생명 생시 생명시스템'
      WHEN 'ai' THEN '인공지능 ai'
      WHEN 'theology' THEN '신학 신과'
      WHEN 'social' THEN '사회과학 사과'
      WHEN 'music' THEN '음악 음대'
      WHEN 'human' THEN '생활과학 생과'
      WHEN 'education' THEN '교육과학 교과'
      WHEN 'underwood' THEN '언더우드 uic 국제'
      WHEN 'global' THEN '글로벌인재 glc'
      WHEN 'medicine' THEN '의과 의대'
      WHEN 'dentistry' THEN '치과 치대'
      WHEN 'nursing' THEN '간호'
      WHEN 'pharmacy' THEN '약학 약대'
      ELSE ''
    END AS synonyms
  FROM colleges
) AS c_syn
WHERE n.college_key = c_syn.key;

COMMIT;