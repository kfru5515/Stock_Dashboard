from flask import Flask, render_template, jsonify
import FinanceDataReader as fdr
import pandas as pd
from pytz import timezone
from datetime import datetime, timedelta
import os
import json
import yfinance as yf
from pykrx import stock
import requests

# --- Blueprints Import ---
from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp

from blueprints.askfin import askfin_bp, initialize_global_data, GLOBAL_TICKER_NAME_MAP
from blueprints.search import search_bp

from db.extensions import db

app = Flask(__name__)
app.secret_key = os.urandom(24)

@app.template_filter('format_kr')
def format_kr(value):
    try:
        num = int(value)
        if num >= 100000000:
            return f"{num // 100000000}억 { (num % 100000000) // 10000 }만"
        elif num >= 10000:
            return f"{num // 10000}만"
        else:
            return str(num)
    except (ValueError, TypeError):
        return value

@app.template_filter('format_price')
def format_price(value):
    try:
        return f"{float(value):,.2f}"
    except (ValueError, TypeError):
        return value

app.jinja_env.filters['format_kr'] = format_kr
app.jinja_env.filters['format_price'] = format_price

CACHE_PATH = 'cache/market_data.json'

def get_latest_business_day():
    today = datetime.now(timezone('Asia/Seoul'))

    if today.hour < 15 or (today.hour == 15 and today.minute < 40):
        today = today - timedelta(days=1)
    
    for i in range(10): 
        date_to_check = today - timedelta(days=i)
        date_str = date_to_check.strftime('%Y%m%d')
        try:

            df = stock.get_market_ohlcv(date_str, date_str, "005930")
            if not df.empty:
                return date_str
        except Exception:
            continue
    return today.strftime('%Y%m%d')

def get_market_rank_data(date_str):
    kospi_df = stock.get_market_ohlcv(date_str, market="KOSPI").reset_index()
    kosdaq_df = stock.get_market_ohlcv(date_str, market="KOSDAQ").reset_index()
    
    for df in [kospi_df, kosdaq_df]:
        tickers = df['티커']
        
        names = [GLOBAL_TICKER_NAME_MAP.get(ticker, ticker) for ticker in tickers]

        df['Name'] = names
        df.rename(columns={'티커': 'Code', '종가': 'Close', '거래량': 'Volume', '거래대금': 'TradingValue'}, inplace=True)
    return kospi_df.to_dict('records'), kosdaq_df.to_dict('records')

def get_wti_data(days=60):
    ticker = yf.Ticker("CL=F")
    df = ticker.history(period=f"{days}d")
    return df.reset_index()[['Date', 'Close']].dropna()

def calculate_change_info(df, name):
    if df is None or len(df.index) < 2:
        return {'name': name, 'value': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0}
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    value = latest['Close']
    change = value - previous['Close']
    change_pct = (change / previous['Close']) * 100 if previous['Close'] != 0 else 0
    return {
        'name': name, 'value': f"{value:,.2f}", 'change': f"{change:,.2f}",
        'change_pct': f"{change_pct:+.2f}%", 'raw_change': change
    }

@app.route('/news/<string:code>')
def get_news(code):
    try:
        url = f"https://m.stock.naver.com/api/news/stock/{code}?pageSize=10&page=1"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        raw_data = response.json()
        formatted_news = []
        if isinstance(raw_data, list):
            for data_group in raw_data:
                if isinstance(data_group, dict) and 'items' in data_group:
                    for item in data_group.get('items', []):
                        if isinstance(item, dict):
                            office_id, article_id = item.get('officeId'), item.get('articleId')
                            if office_id and article_id:
                                raw_dt = item.get('datetime', '')
                                f_date = f"{raw_dt[0:4]}-{raw_dt[4:6]}-{raw_dt[6:8]} {raw_dt[8:10]}:{raw_dt[10:12]}" if len(raw_dt) >= 12 else raw_dt
                                formatted_news.append({
                                    'title': item.get('title'), 'press': item.get('officeName'),
                                    'date': f_date, 'url': f"https://n.news.naver.com/mnews/article/{office_id}/{article_id}"
                                })
        return jsonify(formatted_news[:10])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 실시간 데이터 API 라우트 ---
@app.route('/api/latest-data')
def get_latest_data():
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=10)
        kospi_df = fdr.DataReader('KS11', start=start_date, end=end_date)
        kosdaq_df = fdr.DataReader('KQ11', start=start_date, end=end_date)
        usdkrw_df = fdr.DataReader('USD/KRW', start=start_date, end=end_date)
        wti_df = get_wti_data(10)
        wti_df.set_index('Date', inplace=True)
        return jsonify({
            'kospi': calculate_change_info(kospi_df, 'KOSPI'),
            'kosdaq': calculate_change_info(kosdaq_df, 'KOSDAQ'),
            'usdkrw': calculate_change_info(usdkrw_df, 'USD/KRW'),
            'wti': calculate_change_info(wti_df, 'WTI')
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/')
@app.route('/index')
def index():
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    
    latest_bday = get_latest_business_day()

    if cache.get('date') != latest_bday:
        print(f"[{datetime.now()}] Creating new cache for date: {latest_bday}")
        new_cache = {'date': latest_bday}
        try:
            kospi_all_data, kosdaq_all_data = get_market_rank_data(latest_bday)
            new_cache['kospi_all_data'] = kospi_all_data
            new_cache['kosdaq_all_data'] = kosdaq_all_data
        except Exception as e:
            print(f"Error fetching pykrx data for index page: {e}")
        cache = new_cache
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    kospi_all_data = cache.get('kospi_all_data', [])
    kosdaq_all_data = cache.get('kosdaq_all_data', [])

    default_info = {'value': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0}
    kospi_info, kosdaq_info, usdkrw_info, wti_info = default_info, default_info, default_info, default_info
    kospi_data, kosdaq_data, usdkrw_data, wti_data = [], [], [], []

    days_to_fetch = 60
    start_date = datetime.now() - timedelta(days=days_to_fetch)
    end_date = datetime.now()

    try:
        kospi_df_full = fdr.DataReader('KS11', start=start_date, end=end_date)
        kospi_info = calculate_change_info(kospi_df_full, 'KOSPI')
        kospi_df_full.reset_index(inplace=True)
        kospi_data = kospi_df_full.tail(30).to_dict('records')
    except Exception as e:
        print(f"Error fetching KOSPI data: {e}")

    try:
        kosdaq_df_full = fdr.DataReader('KQ11', start=start_date, end=end_date)
        kosdaq_info = calculate_change_info(kosdaq_df_full, 'KOSDAQ')
        kosdaq_df_full.reset_index(inplace=True)
        kosdaq_data = kosdaq_df_full.tail(30).to_dict('records')
    except Exception as e:
        print(f"Error fetching KOSDAQ data: {e}")

    try:
        usdkrw_df_full = fdr.DataReader('USD/KRW', start=start_date, end=end_date)
        usdkrw_info = calculate_change_info(usdkrw_df_full, 'USD/KRW')
        usdkrw_df_full.reset_index(inplace=True)
        usdkrw_data = usdkrw_df_full.tail(30).to_dict('records')
    except Exception as e:
        print(f"Error fetching USD/KRW data: {e}")

    try:
        wti_df_full = get_wti_data(days_to_fetch)
        wti_df_full_for_calc = wti_df_full.copy()
        wti_df_full_for_calc.set_index('Date', inplace=True)
        wti_info = calculate_change_info(wti_df_full_for_calc, 'WTI')
        wti_data = wti_df_full.tail(30).to_dict('records')
    except Exception as e:
        print(f"Error fetching WTI data: {e}")
        
    for data_list in [kospi_data, kosdaq_data, usdkrw_data, wti_data]:
        for item in data_list:
            if isinstance(item.get('Date'), datetime):
                item['Date'] = item['Date'].strftime('%Y-%m-%d')
    
    top_kospi_volume = sorted(kospi_all_data, key=lambda x: x.get('Volume', 0), reverse=True)[:10]
    top_kospi_value = sorted(kospi_all_data, key=lambda x: x.get('TradingValue', 0), reverse=True)[:10]
    top_kosdaq_volume = sorted(kosdaq_all_data, key=lambda x: x.get('Volume', 0), reverse=True)[:10]
    top_kosdaq_value = sorted(kosdaq_all_data, key=lambda x: x.get('TradingValue', 0), reverse=True)[:10]
    
    today_str_display = datetime.strptime(latest_bday, '%Y%m%d').strftime('%Y-%m-%d')

    return render_template('index.html',
        kospi_data=kospi_data, kosdaq_data=kosdaq_data, usdkrw_data=usdkrw_data, wti_data=wti_data,
        kospi_info=kospi_info, kosdaq_info=kosdaq_info, usdkrw_info=usdkrw_info, wti_info=wti_info,
        kospi_top_volume=top_kospi_volume, kosdaq_top_volume=top_kosdaq_volume,
        kospi_top_value=top_kospi_value, kosdaq_top_value=top_kosdaq_value,
        today=today_str_display)


app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(analysis_bp)
app.register_blueprint(tables_bp)
app.register_blueprint(join_bp)
app.register_blueprint(data_bp)
app.register_blueprint(askfin_bp) 
app.register_blueprint(search_bp)

app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://humanda5:humanda5@localhost/final_join'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)


with app.app_context():
    initialize_global_data()
    print("--- 모든 초기 데이터 로딩 완료 ---")


if __name__ == '__main__':

    app.run(debug=True, port=5000)