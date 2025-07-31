import yfinance as yf
import pandas as pd
import joblib
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.metrics import classification_report
import os

# ▶ Step 1: 데이터 수집 및 전처리
df = yf.download('AAPL', interval='5m', period='5d')
df.reset_index(inplace=True)
df.columns = [col[0].split(' ')[0].strip() if isinstance(col, tuple) else col.split(' ')[0].strip() for col in df.columns]

def label_trend(df, threshold=0.07):
    df['change'] = df['Close'] - df['Open']
    df['label'] = '횡보'
    df.loc[df['change'] > threshold, 'label'] = '상승'
    df.loc[df['change'] < -threshold, 'label'] = '하락'
    return df.dropna()

df = label_trend(df)

df['range'] = df['High'] - df['Low']
df['body'] = abs(df['Close'] - df['Open'])
df['direction'] = df['Close'] - df['Open']
df['volatility'] = (df['High'] - df['Low']) / df['Open']

feature_list = ['Open', 'High', 'Low', 'Close', 'Volume', 'range', 'body', 'direction', 'volatility']
X = df[feature_list]

le = LabelEncoder()
le.fit(['상승', '하락', '횡보'])
y_encoded = le.transform(df['label'])

# ▶ Step 2: 모델 비교 실험
print("📊 모델 비교 실험")
models = {
    'XGBoost': XGBClassifier(max_depth=4, learning_rate=0.1, n_estimators=100),
    'RandomForest': RandomForestClassifier(),
    'LogisticRegression': LogisticRegression(max_iter=1000),
    'SVM': SVC()
}

for name, model in models.items():
    model.fit(X, y_encoded)
    preds = model.predict(X)
    print(f'\n{name} 성능:')
    print(classification_report(y_encoded, preds, target_names=le.classes_))

# ▶ Step 3: Threshold 변화 실험
print("\n📐 Threshold 변화 실험")
thresholds = [0.05, 0.06, 0.07, 0.08]

for t in thresholds:
    df_labeled = label_trend(df.copy(), threshold=t)
    df_labeled['range'] = df_labeled['High'] - df_labeled['Low']
    df_labeled['body'] = abs(df_labeled['Close'] - df_labeled['Open'])
    df_labeled['direction'] = df_labeled['Close'] - df_labeled['Open']
    df_labeled['volatility'] = (df_labeled['High'] - df_labeled['Low']) / df_labeled['Open']
    
    X = df_labeled[feature_list]
    y = le.transform(df_labeled['label'])
    
    model = XGBClassifier()
    model.fit(X, y)
    preds = model.predict(X)
    
    print(f"\n[Threshold={t}] 라벨 비율:")
    print(df_labeled['label'].value_counts(normalize=True))
    print(classification_report(y, preds, target_names=le.classes_))
