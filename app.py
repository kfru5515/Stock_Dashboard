from flask import Flask, Blueprint, render_template, request, jsonify
from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp
import FinanceDataReader as fdr
import pandas as pd
from dotenv import load_dotenv

from pytz import timezone # 한국시간 사용 


from datetime import datetime, timedelta
import os, json

app = Flask(__name__)
app.secret_key = '1234'

# 거래량: 억/만 단위 + 원
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
    except:
        return value
    
# 종가: 숫자 + 원
@app.template_filter('format_price')
def format_price(value):
    try:
        return f"{int(value):,} 원"
    except:
        return value

# 거래대금: 만원 단위 절하 + '만원'
@app.template_filter('format_trading_value')
def format_trading_value(value):
    try:
        return f"{int(value) // 10000:,} 만원"
    except:
        return value
        
app.jinja_env.filters['format_kr'] = format_kr

@app.route('/')
@app.route('/index')
def index():
    import FinanceDataReader as fdr
    import pandas as pd

    kospi_df = fdr.DataReader('KS11').tail(180).reset_index()
    kosdaq_df = fdr.DataReader('KQ11').tail(180).reset_index()
    usdkrw_df = fdr.DataReader('USD/KRW').tail(180).reset_index()
    usdkrw_df.rename(columns={'index': 'Date'}, inplace=True)


    # 날짜를 UTC에서 서울 시간으로 변환 후 날짜만 추출
    seoul_tz = timezone('Asia/Seoul')
    kospi_df['Date'] = pd.to_datetime(kospi_df['Date']).dt.tz_localize('UTC').dt.tz_convert(seoul_tz).dt.date
    kosdaq_df['Date'] = pd.to_datetime(kosdaq_df['Date']).dt.tz_localize('UTC').dt.tz_convert(seoul_tz).dt.date
    usdkrw_df['Date'] = pd.to_datetime(usdkrw_df['Date']).dt.tz_localize('UTC').dt.tz_convert(seoul_tz).dt.date

    def resample_data(df, rule):
        if 'Date' not in df.columns:
            df = df.reset_index()
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        df_resampled = df['Close'].resample(rule).last().dropna()
        return [{'Date': d.strftime('%Y-%m-%d'), 'Close': float(c)} for d, c in df_resampled.items()]

    kospi_daily = kospi_df.tail(30)[['Date', 'Close']]
    kosdaq_daily = kosdaq_df.tail(30)[['Date', 'Close']]
    usdkrw_daily = usdkrw_df.tail(30)[['Date', 'Close']]

    kospi_weekly_data = resample_data(kospi_df.copy(), 'W')
    kosdaq_weekly_data = resample_data(kosdaq_df.copy(), 'W')
    usdkrw_weekly_data = resample_data(usdkrw_df.copy(), 'W')

    kospi_monthly_data = resample_data(kospi_df.copy(), 'ME')
    kosdaq_monthly_data = resample_data(kosdaq_df.copy(), 'ME')
    usdkrw_monthly_data = resample_data(usdkrw_df.copy(), 'ME')

 #거래량 상위 
    top_kospi_volume = get_top_volume_with_cache('KOSPI')
    top_kosdaq_volume = get_top_volume_with_cache('KOSDAQ')

    top_kospi_volume = add_trading_value(get_top_volume_with_cache('KOSPI'))
    top_kosdaq_volume = add_trading_value(get_top_volume_with_cache('KOSDAQ'))

    
# 거래대금 
    top_kospi_volume = add_trading_value(top_kospi_volume)
    top_kosdaq_volume = add_trading_value(top_kosdaq_volume)

#오늘날 
    yesterday = datetime.now() - timedelta(days=1)
    today_str = yesterday.strftime('%Y-%m-%d')


    return render_template('index.html',
        kospi_data=kospi_daily.to_dict(orient='records'),
        kosdaq_data=kosdaq_daily.to_dict(orient='records'),
        usdkrw_data=usdkrw_daily.to_dict(orient='records'),
        kospi_weekly_data=kospi_weekly_data,
        kosdaq_weekly_data=kosdaq_weekly_data,
        usdkrw_weekly_data=usdkrw_weekly_data,
        kospi_monthly_data=kospi_monthly_data,
        kosdaq_monthly_data=kosdaq_monthly_data,
        usdkrw_monthly_data=usdkrw_monthly_data,
        kospi_top_volume=top_kospi_volume,
        kosdaq_top_volume=top_kosdaq_volume,

        today=today_str
    )



    
def add_trading_value(stock_list):
    for item in stock_list:
        item['TradingValue'] = item['Close'] * item['Volume']
    return stock_list


def get_top_volume_stocks(market, top_n=10):
    stock_list = fdr.StockListing(market)[['Name', 'Code']]
    result = []

    for _, row in stock_list.iterrows():
        try:
            df = fdr.DataReader(row['Code']).tail(1)
            if not df.empty:
                volume = df['Volume'].iloc[0]
                close = df['Close'].iloc[0]
                trading_value = volume * close
                result.append({
                    'Name': row['Name'],
                    'Code': row['Code'],
                    'Close': float(close),  # 여기 변환
                    'Volume': int(volume)   # 여기 변환
                })
        except:
            continue  # 오류 무시하고 다음 종목 처리

    result_sorted = sorted(result, key=lambda x: x['Volume'], reverse=True)
    return result_sorted[:top_n]




cache_path = 'cache/top_volume.json'

import os
import json

def get_top_volume_with_cache(market, top_n=10):
        # cache 폴더가 없으면 생성
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            try:
                cache = json.load(f)
            except:
                cache = {}

    today = datetime.now().strftime('%Y-%m-%d')

    # 날짜가 다르면 초기화
    if cache.get('date') != today:
        cache = {'date': today}

    # 캐시에 이미 있으면 바로 리턴
    if f'top_{market.lower()}' in cache:
        return cache[f'top_{market.lower()}']

    # 없으면 데이터 새로 조회
    result = get_top_volume_stocks(market, top_n)
    cache[f'top_{market.lower()}'] = result

    # 다시 저장
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    return result

# 사용 예
top_kospi_volume = get_top_volume_with_cache('KOSPI')
top_kosdaq_volume = get_top_volume_with_cache('KOSDAQ')
    
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(analysis_bp)
app.register_blueprint(tables_bp)
app.register_blueprint(join_bp)
app.register_blueprint(data_bp)

if __name__ == '__main__':
    app.run(debug=True)