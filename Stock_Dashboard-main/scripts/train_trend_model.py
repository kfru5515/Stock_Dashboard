import yfinance as yf
import pandas as pd
import joblib
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier
import os

# ▶ Step 1: Download 5-minute stock data
df = yf.download('AAPL', interval='5m', period='5d')
df.reset_index(inplace=True)
# ✔ 종목명(AAPL), 공백 제거
df.columns = [col[0].split(' ')[0].strip() if isinstance(col, tuple) else col.split(' ')[0].strip() for col in df.columns]

# ▶ Step 2: 라벨링 함수 개선 (Open vs Close 기준)
def label_trend(df, threshold=0.07):
    df['change'] = df['Close'] - df['Open']
    df['label'] = '횡보'
    df.loc[df['change'] > threshold, 'label'] = '상승'
    df.loc[df['change'] < -threshold, 'label'] = '하락'
    return df.dropna()

df = label_trend(df)

# ▶ Step 3: 파생 피처 추가
df['range'] = df['High'] - df['Low']
df['body'] = abs(df['Close'] - df['Open'])
df['direction'] = df['Close'] - df['Open']
df['volatility'] = (df['High'] - df['Low']) / df['Open']

# ▶ Step 4: 라벨 인코딩 순서 명시
le = LabelEncoder()
le.fit(['상승', '하락', '횡보'])  # ✔ 순서 강제 정하기
y = df['label']
y_encoded = le.transform(y)

# ▶ Step 5: Feature 배열 결정
X = df[['Open', 'High', 'Low', 'Close', 'Volume', 'range', 'body', 'direction', 'volatility']]

# ▶ Step 6: XGBoost 학습
model = XGBClassifier(
    max_depth=4,
    learning_rate=0.1,
    n_estimators=100,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42
)
model.fit(X, y_encoded)

# ▶ Step 7: 학습 결과 조회
print("라벨 분포:")
print(y.value_counts())
print("비율:")
print(y.value_counts(normalize=True))
print("인코딩 순서:", list(le.classes_))

# ▶ Step 8: 테스트 예제 입력
sample = pd.DataFrame([[
    100.0, 120.0, 99.0, 119.5, 800000, 21.0, 19.5, 19.5, 0.21
]], columns=X.columns)

test_pred = model.predict(sample)
print("테스트 예측 결과:", test_pred)
print("예측된 라벨:", le.inverse_transform(test_pred))

# ▶ Step 9: 파일 저장
os.makedirs('models', exist_ok=True)
joblib.dump(model, 'models/trend_model.pkl')
joblib.dump(le, 'models/label_encoder.pkl')
feature_list = list(X.columns)
joblib.dump(feature_list, 'models/feature_list.pkl')
