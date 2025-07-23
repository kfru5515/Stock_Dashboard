# build_keyword_processor.py
#기업명 추출용 pickle 생성기
import os
import pandas as pd
import pickle
from flashtext import KeywordProcessor

# 1) 기업명 리스트 로드
corp_df = pd.read_csv('data_files/corp_names.csv', encoding='utf-8-sig')
names   = corp_df['corp_name'].astype(str).tolist()

# 2) KeywordProcessor에 등록
kp = KeywordProcessor(case_sensitive=False)
for name in names:
    if len(name) >= 2:
        kp.add_keyword(name)

# 3) pickle로 저장
os.makedirs('data_files', exist_ok=True)
with open('data_files/keyword_processor.pkl', 'wb') as f:
    pickle.dump(kp, f)

print(f"✅ keyword_processor.pkl 생성 완료: 총 {len(names)}개 키워드 등록")
