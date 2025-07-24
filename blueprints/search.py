from flask import Blueprint, render_template, request, session
import FinanceDataReader as fdr
import yfinance as yf
import pandas as pd
import joblib
from datetime import datetime, timedelta # datetime 모듈에서 datetime과 timedelta를 명시적으로 임포트

# 모델, 인코더, 피처 목록 로드
model = joblib.load('models/trend_model.pkl')
le = joblib.load('models/label_encoder.pkl')
features = joblib.load('models/feature_list.pkl')

# Blueprint 설정
search_bp = Blueprint('search', __name__, url_prefix='/search')

# KRX 종목 목록 로딩 (견고성 강화 및 FullCode 추가)
GLOBAL_KRX_LISTING = pd.DataFrame() # 초기화

try:
    # app.py (askfin)에서 이미 초기화된 전역 변수를 임포트 시도
    from blueprints.askfin import GLOBAL_KRX_LISTING as imported_krx_listing
    if imported_krx_listing is not None and not imported_krx_listing.empty:
        GLOBAL_KRX_LISTING = imported_krx_listing
        # FullCode 컬럼이 없을 경우를 대비 (추가된 코드)
        if 'FullCode' not in GLOBAL_KRX_LISTING.columns:
             GLOBAL_KRX_LISTING['FullCode'] = GLOBAL_KRX_LISTING.apply(
                lambda row: f"{row['Code']}.KQ" if row['Market'] == 'KOSDAQ' else f"{row['Code'].zfill(6)}.KS", axis=1
            )
        print(f"search.py: GLOBAL_KRX_LISTING (from askfin) loaded. Total {len(GLOBAL_KRX_LISTING)} stocks.")
    else:
        # 임포트 실패 또는 빈 값일 경우, 직접 FDR로 로드 (폴백)
        print("search.py: WARN - GLOBAL_KRX_LISTING from askfin is empty/None. Attempting direct FDR load.")
        temp_krx_listing = fdr.StockListing('KRX')
        if not temp_krx_listing.empty:
            temp_krx_listing['FullCode'] = temp_krx_listing.apply(
                lambda row: f"{row['Code']}.KQ" if row['Market'] == 'KOSDAQ' else f"{row['Code'].zfill(6)}.KS", axis=1
            )
            GLOBAL_KRX_LISTING = temp_krx_listing
            print(f"search.py: Direct FDR StockListing loaded. Total {len(GLOBAL_KRX_LISTING)} stocks.")
        else:
            print("search.py: CRITICAL ERROR - Direct FDR StockListing also failed. KRX list is empty.")
except Exception as e:
    print(f"search.py: CRITICAL ERROR - Could not load KRX StockListing at all: {e}")
    GLOBAL_KRX_LISTING = pd.DataFrame(columns=['Code', 'Name', 'Market', 'Close', 'Volume', 'Marcap', 'FullCode'])


@search_bp.route('/stock/<code>')
def stock_detail(code):
    print(f"DEBUG stock_detail: Request for stock code: {code}")
    stock_name_from_krx = code.split('.')[0] # 기본값

    try:
        # KRX 리스팅에서 이름 먼저 찾기 (빠름)
        name_row = GLOBAL_KRX_LISTING[GLOBAL_KRX_LISTING['FullCode'] == code]
        if not name_row.empty:
            stock_name_from_krx = name_row['Name'].iloc[0]

        ticker = yf.Ticker(code)
        info = ticker.info
    except Exception as e:
        print(f"DEBUG stock_detail: yf.Ticker({code}).info error: {e}")
        info = {}

    if not info:
        print(f"DEBUG stock_detail: No info found for {code}. Returning 404.")
        return "종목 정보를 찾을 수 없습니다.", 404

    # ✅ 여기에서만 최근 종목 추가
    recent_codes = session.get('recent_stocks', [])
    if code not in recent_codes:
        recent_codes.insert(0, code)
        if len(recent_codes) > 5:
            recent_codes = recent_codes[:5]
        session['recent_stocks'] = recent_codes
        session.modified = True

    # 최근 종목 목록 렌더링용 (기존 안정적인 방식 유지)
    recent_stocks = []
    for c in recent_codes:
        current_name = c.split('.')[0] # 기본값

        try:
            # KRX 리스팅에서 이름 찾기 (추가된 로직)
            name_row_recent = GLOBAL_KRX_LISTING[GLOBAL_KRX_LISTING['FullCode'] == c]
            if not name_row_recent.empty:
                current_name = name_row_recent['Name'].iloc[0]

            t_info = yf.Ticker(c).info
            current_price = t_info.get('currentPrice', 'N/A')
            if isinstance(current_price, (float, int)):
                current_price = round(current_price, 0)
            else:
                current_price = 'N/A'
            recent_stocks.append({
                'code': c,
                'name': current_name,
                'price': current_price
            })
        except Exception as e:
            print(f"DEBUG stock_detail: Failed to fetch recent stock info for {c}: {e}")
            recent_stocks.append({'code': c, 'name': current_name, 'price': 'N/A'})

    return render_template('stock_detail.html', stock=info, recent_stocks=recent_stocks)


@search_bp.route('/search')
def search():
    query = request.args.get('q', '').strip()
    results = []

    # 최근 검색 종목 처리 (기존 안정적인 방식 유지)
    recent_codes = session.get('recent_stocks', [])
    recent_stocks = []
    for c in recent_codes:
        current_name = c.split('.')[0] # 기본값
        current_price = 'N/A'
        try:
            # KRX 리스팅에서 이름 찾기 (추가된 로직)
            name_row_recent = GLOBAL_KRX_LISTING[GLOBAL_KRX_LISTING['FullCode'] == c]
            if not name_row_recent.empty:
                current_name = name_row_recent['Name'].iloc[0]

            info_recent = yf.Ticker(c).info
            current_price = info_recent.get('currentPrice', 'N/A')
            if isinstance(current_price, (float, int)):
                current_price = round(current_price, 0)
            else:
                current_price = 'N/A'
            recent_stocks.append({
                'code': c,
                'name': current_name,
                'price': current_price
            })
        except Exception as e:
            print(f"DEBUG search: Failed to fetch recent stock info for display {c}: {e}")
            recent_stocks.append({'code': c, 'name': current_name, 'price': 'N/A'})


    if not query:
        print("DEBUG search: No query provided.")
        return render_template('search_results.html', results=[], query=query, recent_stocks=recent_stocks)

    df = GLOBAL_KRX_LISTING.copy()
    
    # 디버깅을 위해 로딩 상태 출력
    print(f"DEBUG search: Total stocks in GLOBAL_KRX_LISTING: {len(GLOBAL_KRX_LISTING)}")
    print(f"DEBUG search: Query: '{query}'")

    if GLOBAL_KRX_LISTING.empty: # KRX 리스트가 비어있으면 검색 불가
        print("DEBUG search: GLOBAL_KRX_LISTING is empty. Cannot perform search.")
        return render_template('search_results.html', results=[], query=query, recent_stocks=recent_stocks, error_message="종목 목록을 불러올 수 없습니다. 서버 로그를 확인하세요.")


    # 쿼리어가 숫자 6자리인 경우, 코드와 이름 모두에서 찾도록 함 (정확도 향상)
    if query.isdigit() and len(query) == 6:
        matched = df[
            (df['Name'].str.contains(query, case=False, na=False)) |
            (df['Code'] == query) # 코드 일치도 확인
        ]
    else:
        matched = df[df['Name'].str.contains(query, case=False, na=False)]

    # 검색 결과 개수 제한
    MAX_SEARCH_RESULTS = 20 
    matched = matched.head(MAX_SEARCH_RESULTS)
    
    print(f"DEBUG search: Filtered stocks count: {len(matched)}")

    if matched.empty:
        print(f"DEBUG search: No matching stocks found for query '{query}'.")
        return render_template('search_results.html', results=[], query=query, recent_stocks=recent_stocks)


    for _, row in matched.iterrows():
        code_krx = row['Code'] # KRX Listing의 원래 코드 (6자리)
        name = row['Name']
        market = row['Market']
        suffix = '.KQ' if market == 'KOSDAQ' else '.KS'
        full_code = f"{code_krx}{suffix}" # yfinance용 전체 코드

        # KRX 리스팅에서 가져온 기본 가격 및 정보
        current_price = row['Close']
        volume = row['Volume']
        marketcap = row['Marcap']

        price_chart = [0, 0, 0, 0, 0] # 기본값
        label = "예측 불가" # 기본 예측 결과

        try:
            ticker_obj = yf.Ticker(full_code)
            
            # 최근 5일 종가 차트용 데이터 (period='5d'로 안정성 확보)
            hist = ticker_obj.history(period='5d')
            if not hist.empty:
                price_chart = hist['Close'].fillna(0).round(2).tolist()
                while len(price_chart) < 5:
                    price_chart.insert(0, 0)
                price_chart = price_chart[-5:]
            else:
                print(f"DEBUG search: Historical data empty for {full_code}.")
                price_chart = [0, 0, 0, 0, 0]

            # 예측용 5분봉 데이터 (period='1d', interval='5m'으로 안정성 확보)
            intraday = ticker_obj.history(period='1d', interval='5m')
            if not intraday.empty:
                latest = intraday.iloc[-1]
                
                open_, high, low, close, vol = latest['Open'], latest['High'], latest['Low'], latest['Close'], latest['Volume']
                
                if all(pd.notna(x) for x in [open_, high, low, close, vol]):
                    range_ = high - low
                    body = abs(close - open_)
                    direction = close - open_
                    volatility = (high - low) / open_ if open_ else 0

                    input_df = pd.DataFrame([[open_, high, low, close, vol, range_, body, direction, volatility]],
                                            columns=features)
                    try:
                        pred = model.predict(input_df)
                        label = le.inverse_transform(pred)[0]
                        current_price = close # 예측 성공하면 yf 최신 종가 사용
                    except Exception as e:
                        print(f"DEBUG search: 예측 모델 오류 ({full_code}): {e}")
                        label = "예측 실패 (모델 오류)"
                else:
                    label = "예측 불가 (데이터 누락)"
            else:
                label = "예측 불가 (금일 5분봉 데이터 없음)"
        except Exception as e:
            print(f"DEBUG search: yfinance data fetch error for {full_code}: {e}")
            label = "예측 불가 (조회 오류)"
            # current_price는 KRX 리스트에서 가져온 값을 그대로 사용 (폴백)

        results.append({
            'code': full_code, # yfinance용 코드 (예: 005930.KS)
            'krx_code': code_krx, # KRX 고유 6자리 코드 (예: 005930) <--- 이 부분을 추가했습니다.
            'name': name,
            'currentPrice': round(current_price, 0) if isinstance(current_price, (float, int)) else 'N/A',
            'volume': volume,
            'marketcap': marketcap,
            'prediction': label,
            'price_chart': price_chart,
            'financial': {}
        })

    return render_template('search_results.html', results=results, query=query, recent_stocks=recent_stocks)