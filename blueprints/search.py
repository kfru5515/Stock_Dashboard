from flask import Blueprint, render_template, request, session
import FinanceDataReader as fdr
import yfinance as yf
import pandas as pd

search_bp = Blueprint('search', __name__, url_prefix='/search')

# KRX 종목 목록 로딩
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
    ticker_suffix = ''

    if query in name_to_code:
        ticker_code = name_to_code[query]
        market = krx_list.loc[krx_list['Code'] == ticker_code, 'Market'].values[0]
        ticker_suffix = '.KQ' if market == 'KOSDAQ' else '.KS'
        ticker_code += ticker_suffix
    else:
        ticker_code = query.upper()

    try:
        ticker = yf.Ticker(ticker_code)
        info = ticker.info
        if info and 'shortName' in info and info.get('currentPrice'):
            results.append({
                'code': ticker_code,
                'name': info['shortName'],
                'currentPrice': info.get('currentPrice', 'N/A')
            })
        else:
            hist = ticker.history(period='1d')
            if not hist.empty:
                close_price = hist['Close'].iloc[-1]
                results.append({
                    'code': ticker_code,
                    'name': query,
                    'currentPrice': f"{close_price:,.2f}"
                })
    except Exception as e:
        print("yfinance 오류:", e)

    # 🔥 최근 조회 종목 세션에 저장
    recent = session.get('recent_stocks', [])
    stock_info = {'code': ticker_code, 'name': info['shortName']}
    if ticker_code not in recent:
        recent.insert(0, ticker_code)
        if len(recent) > 5:
            recent = recent[:5]
        session['recent_stocks'] = recent
        session.modified = True  

    return render_template('search_results.html', results=results, query=query)



@search_bp.route('/stock/<code>')
def stock_detail(code):
    try:
        ticker = yf.Ticker(code)
        info = ticker.info
    except Exception:
        info = {}

    if not info:
        return "종목 정보를 찾을 수 없습니다.", 404

    # 🔥 최근 종목 리스트 표시
    recent_codes = session.get('recent_stocks', [])
    recent_stocks = []

    for c in recent_codes:
        try:
            t = yf.Ticker(c)
            stock_info = t.info
            name = stock_info.get('shortName', c)
            price = stock_info.get('currentPrice', 'N/A')
            recent_stocks.append({'code': c, 'name': name, 'price': price})
        except Exception:
            recent_stocks.append({'code': c, 'name': c, 'price': 'N/A'})

    return render_template('stock_detail.html', stock=info, recent_stocks=recent_stocks)

