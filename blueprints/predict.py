from flask import Blueprint, request, jsonify
import pandas as pd
import joblib
import os

predict_bp = Blueprint('predict', __name__)

# ✅ 모델 및 라벨 인코더 경로 설정
model_path = os.path.join(os.path.dirname(__file__), '../models/trend_model.pkl')
encoder_path = os.path.join(os.path.dirname(__file__), '../models/label_encoder.pkl')

# ✅ 모델과 인코더 불러오기
model = joblib.load(model_path)
label_encoder = joblib.load(encoder_path)

@predict_bp.route('/api/predict_trend', methods=['POST'])
def predict_trend():
    try:
        data = request.json.get('data')
        df = pd.DataFrame(data)

        # ✅ 예측
        preds = model.predict(df)

        # ✅ 숫자 라벨 → 문자열 복원
        decoded_preds = label_encoder.inverse_transform(preds)

        return jsonify({'predictions': decoded_preds.tolist()})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
