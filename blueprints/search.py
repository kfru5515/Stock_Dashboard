from flask import Blueprint, render_template, request, session
import FinanceDataReader as fdr
import yfinance as yf
import pandas as pd
import joblib

# 모델, 인코더, 피처 목록 로드
model = joblib.load('models/trend_model.pkl')
le = joblib.load('models/label_encoder.pkl')
features = joblib.load('models/feature_list.pkl')

# Blueprint 설정
search_bp = Blueprint('search', __name__, url_prefix='/search')

@search_bp.route('/stock/<code>')
def stock_detail(code):
    try:
        ticker = yf.Ticker(code)
        info = ticker.info
    except Exception:
        info = {}

    if not info:
        return "종목 정보를 찾을 수 없습니다.", 404

    # 최근 검색 종목 표시
    recent_codes = session.get('recent_stocks', [])
    recent_stocks = []
    for c in recent_codes:
        try:
            t_info = yf.Ticker(c).info
            recent_stocks.append({
                'code': c,
                'name': t_info.get('shortName', c),
                'price': t_info.get('currentPrice', 'N/A')
            })
        except Exception:
            recent_stocks.append({'code': c, 'name': c, 'price': 'N/A'})

    return render_template('stock_detail.html', stock=info, recent_stocks=recent_stocks)


# KRX 종목 목록 로딩
try:
    GLOBAL_KRX_LISTING = fdr.StockListing('KRX')
    name_to_code = pd.Series(GLOBAL_KRX_LISTING.Code.values, index=GLOBAL_KRX_LISTING.Name).to_dict()
except Exception as e:
    print("KRX 리스트 로딩 실패:", e)
    GLOBAL_KRX_LISTING = pd.DataFrame()
    name_to_code = {}

@search_bp.route('/search')
def search():
    query = request.args.get('q', '').strip()
    results = []

    # 최근 검색 종목 처리
    recent_codes = session.get('recent_stocks', [])
    recent_stocks = []
    for c in recent_codes:
        try:
            info = yf.Ticker(c).info
            recent_stocks.append({
                'code': c,
                'name': info.get('shortName', c),
                'price': info.get('currentPrice', 'N/A')
            })
        except Exception:
            recent_stocks.append({'code': c, 'name': c, 'price': 'N/A'})

    if not query:
        return render_template('search_results.html', results=[], query=query, recent_stocks=recent_stocks)

    df = GLOBAL_KRX_LISTING.copy()
    matched = df[df['Name'].str.contains(query, case=False, na=False)]

    for _, row in matched.iterrows():
        code = row['Code']
        name = row['Name']
        market = row['Market']
        suffix = '.KQ' if market == 'KOSDAQ' else '.KS'
        full_code = f"{code}{suffix}"
        price = row['Close']
        volume = row['Volume']
        marketcap = row['Marcap']

        # 최근 5일 종가 차트용 데이터
        try:
            ticker = yf.Ticker(full_code)
            hist = ticker.history(period='5d')
            price_chart = hist['Close'].fillna(0).round(2).tolist()
        except Exception:
            price_chart = [0, 0, 0, 0, 0]

        # 예측
        try:
            intraday = ticker.history(period='1d', interval='5m')
            if not intraday.empty:
                latest = intraday.iloc[-1]
                open_, high, low, close, vol = latest['Open'], latest['High'], latest['Low'], latest['Close'], latest['Volume']
                range_ = high - low
                body = abs(close - open_)
                direction = close - open_
                volatility = (high - low) / open_ if open_ else 0

                input_df = pd.DataFrame([[open_, high, low, close, vol, range_, body, direction, volatility]],
                                        columns=features)
                pred = model.predict(input_df)
                label = le.inverse_transform(pred)[0]
            else:
                label = "예측 불가"
        except Exception as e:
            print("예측 오류:", e)
            label = "예측 실패"

        # 결과 추가
        results.append({
            'code': full_code,
            'name': name,
            'currentPrice': price,
            'volume': volume,
            'marketcap': marketcap,
            'prediction': label,
            'price_chart': price_chart,
            'financial': {
                '매출액': ['1000억', '950억', '900억'],
                '영업이익': ['150억', '140억', '130억'],
                '순이익': ['100억', '95억', '90억']
            }
        })

        # 세션 저장
        if full_code not in recent_codes:
            recent_codes.insert(0, full_code)
            if len(recent_codes) > 5:
                recent_codes = recent_codes[:5]
            session['recent_stocks'] = recent_codes
            session.modified = True

    return render_template('search_results.html', results=results, query=query, recent_stocks=recent_stocks)
