from flask import Blueprint, render_template, request
import FinanceDataReader as fdr
import yfinance as yf
import pandas as pd

search_bp = Blueprint('search', __name__, url_prefix='/search')

# 최초에 한번만 KRX 종목 목록 로딩
try:
    krx_list = fdr.StockListing('KRX')
    name_to_code = pd.Series(krx_list.Code.values, index=krx_list.Name).to_dict()
except Exception as e:
    print("KRX 종목 리스트 로딩 실패:", e)
    krx_list = pd.DataFrame()
    name_to_code = {}

@search_bp.route('/search')
def search():
    query = request.args.get('q', '').strip()
    results = []

    if not query:
        return render_template('search_results.html', results=[], query=query)

    ticker_code = ''
    
    # 입력이 한글 종목명일 경우 → 종목코드 변환
    if query in name_to_code:
        ticker_code = name_to_code[query]
        ticker_code += ".KS"  # 한국 거래소용 yfinance 포맷
    else:
        # 코드나 외국 종목 직접 입력일 경우 그대로 시도
        ticker_code = query.upper()

    try:
        ticker = yf.Ticker(ticker_code)
        info = ticker.info

        # yfinance에서 데이터 가져오기 성공 시
        if 'shortName' in info:
            results.append({
                'code': ticker_code,
                'name': info['shortName'],
                'currentPrice': info.get('currentPrice', 'N/A')
            })
    except Exception as e:
        print("yfinance 오류:", e)

    return render_template('search_results.html', results=results, query=query)

def stock_detail(code):
    ticker_code_yf = f"{code}.KS"  # KRX 종목이라 가정
    try:
        ticker = yf.Ticker(ticker_code_yf)
        info = ticker.info
    except Exception as e:
        info = {}

    if not info:
        info = {}

    return render_template('stock_detail.html', stock=info)