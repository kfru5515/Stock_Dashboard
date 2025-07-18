from flask import Flask, render_template, jsonify, session
import FinanceDataReader as fdr
import pandas as pd
from pytz import timezone
from datetime import datetime, timedelta
import os
import json
import yfinance as yf
from pykrx import stock
import requests
from bs4 import BeautifulSoup 
import traceback 

from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp
from blueprints import askfin
from blueprints.askfin import askfin_bp, initialize_global_data, GLOBAL_TICKER_NAME_MAP 
from blueprints.search import search_bp
from dotenv import load_dotenv

from db.extensions import db
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

@app.template_filter('format_kr')
def format_kr(value):
    try:
        num = int(value)
        if num >= 100000000:
            return f"{num // 100000000}억"
        elif num >= 10000:
            return f"{num // 10000}만"
        else:
            return str(num)
    except (ValueError, TypeError):
        return value

@app.template_filter('format_price')
def format_price(value):
    try:
        return f"{int(float(value)):,}" 
    except (ValueError, TypeError):
        return value

@app.template_filter('format_value')
def format_value(value):
    try:
        return f"{int(value) // 100000000:,.0f}억"
    except (ValueError, TypeError):
        return value

app.jinja_env.filters['format_kr'] = format_kr
app.jinja_env.filters['format_price'] = format_price
app.jinja_env.filters['format_value'] = format_value

CACHE_PATH = 'cache/market_data.json'

def get_latest_business_day():
    return stock.get_nearest_business_day_in_a_week()

def get_market_rank_data(date_str):
    kospi_df = stock.get_market_ohlcv(date_str, market="KOSPI").reset_index()
    kosdaq_df = stock.get_market_ohlcv(date_str, market="KOSDAQ").reset_index()
    for df in [kospi_df, kosdaq_df]:
        tickers = df['티커']
        if askfin.GLOBAL_TICKER_NAME_MAP is None:
            askfin.initialize_global_data()
        names = [askfin.GLOBAL_TICKER_NAME_MAP.get(ticker, ticker) for ticker in tickers]
        df['Name'] = names
        df.rename(columns={'티커': 'Code', '종가': 'Close', '거래량': 'Volume', '거래대금': 'TradingValue', '등락률': 'ChangeRatio'}, inplace=True)
        for col in ['Close', 'Volume', 'TradingValue', 'ChangeRatio']:
            df[col] = df[col].fillna(0)
    return kospi_df.to_dict('records'), kosdaq_df.to_dict('records')

def get_wti_data(days=60):
    ticker = yf.Ticker("CL=F")
    df = ticker.history(period=f"{days}d")
    return df.reset_index()[['Date', 'Close']].dropna()

def calculate_change_info(df, name):
    if df is None or len(df.index) < 2:
        return {'name': name, 'value': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0}
    df_no_tz = df.copy()
    if isinstance(df_no_tz.index, pd.DatetimeIndex):
       df_no_tz.index = df_no_tz.index.tz_localize(None)
    df_no_tz = df_no_tz.sort_index().dropna()
    if len(df_no_tz) < 2:
        return {'name': name, 'value': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0}
    latest = df_no_tz.iloc[-1]
    previous = df_no_tz.iloc[-2]
    value = latest['Close']
    change = value - previous['Close']
    change_pct = (change / previous['Close']) * 100 if previous['Close'] != 0 else 0
    return {'name': name, 'value': f"{value:,.2f}", 'change': f"{change:,.2f}", 'change_pct': f"{change_pct:+.2f}%", 'raw_change': change}

def get_fdr_or_yf_data(ticker, start, end, interval='1d'):
    if interval != '1d':
        try:
            print(f"Attempting to fetch '{ticker}' with yfinance (interval: {interval})...")
            yf_ticker_map = {'USD/KRW': 'KRW=X', 'KS11': '^KS11', 'KQ11': '^KQ11', 'CL=F': 'CL=F'}
            if ticker in yf_ticker_map:
                yf_df = yf.download(yf_ticker_map[ticker], start=start, end=end, interval=interval, auto_adjust=True)
                if not yf_df.empty:
                    print("yfinance fetch successful.")
                    return yf_df.reset_index()
            raise ValueError("Ticker not in yfinance map or fetch failed")
        except Exception as yf_e:
            print(f"yfinance (interval) failed for '{ticker}': {yf_e}. Falling back to fdr.")

    try:
        print(f"Attempting to fetch '{ticker}' with fdr...")
        df = fdr.DataReader(ticker, start, end)
        if df.empty: raise ValueError("FDR returned empty dataframe")
        print("FDR fetch successful.")
        df.reset_index(inplace=True)
        df.rename(columns={'index': 'Date'}, inplace=True, errors='ignore')
        return df
    except Exception as e:
        print(f"FDR failed for '{ticker}': {e}. Falling back to yfinance (daily).")
        try:
            yf_ticker_map = {'USD/KRW': 'KRW=X', 'KS11': '^KS11', 'KQ11': '^KQ11', 'CL=F': 'CL=F'}
            if ticker not in yf_ticker_map: return pd.DataFrame()
            yf_df = yf.download(yf_ticker_map[ticker], start=start, end=end, interval='1d', auto_adjust=True)
            if yf_df.empty: return pd.DataFrame()
            print("yfinance (daily) fetch successful.")
            return yf_df.reset_index()
        except Exception as yf_e:
            print(f"yfinance (daily) also failed for '{ticker}': {yf_e}")
            return pd.DataFrame()

# ==============================================================================
# 뉴스 가져오기 함수 수정 (BeautifulSoup으로 종목별 뉴스 스크래핑)
# ==============================================================================
@app.route('/news/<string:code>')
def get_news(code):
    """
    특정 종목에 대한 뉴스를 네이버 금융에서 스크래핑합니다.
    """
    formatted_news = []
    print(f"DEBUG: '{code}' 종목 코드에 대해 네이버 금융에서 뉴스 스크래핑 시도.")
    try:
        url = f"https://finance.naver.com/item/news_news.naver?code={code}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=10) # 타임아웃 10초
        response.raise_for_status() # HTTP 오류 시 예외 발생
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 뉴스 목록이 있는 테이블 선택 (네이버 증권 종목 뉴스 페이지 구조 분석)
        # class="type5" 테이블이 뉴스 목록을 담고 있음
        news_table = soup.find('table', class_='type5')
        
        if news_table:
            rows = news_table.find_all('tr')
            for row in rows:
                title_tag = row.find('td', class_='title')
                source_tag = row.find('td', class_='source')
                date_tag = row.find('td', class_='date')
                
                if title_tag and source_tag and date_tag:
                    link = title_tag.find('a')
                    if link and link.get('href'):
                        title = link.get_text(strip=True)
                        full_link = f"https://finance.naver.com{link.get('href')}"
                        source = source_tag.get_text(strip=True)
                        raw_date = date_tag.get_text(strip=True)
                        
                        # 날짜 형식 조정 (예: "2025.07.18 16:00" -> "2025-07-18")
                        date_parts = raw_date.split(' ')[0].split('.') # '2025.07.18' 부분만
                        f_date = f"{date_parts[0]}-{date_parts[1].zfill(2)}-{date_parts[2].zfill(2)}" if len(date_parts) == 3 else raw_date

                        formatted_news.append({
                            'title': title,
                            'press': source,
                            'date': f_date,
                            'url': full_link
                        })
        
        if not formatted_news:
            print(f"DEBUG: '{code}'에 대한 뉴스를 스크래핑했으나 찾은 뉴스 항목이 0개입니다.")
            return jsonify({"error": "관련 뉴스가 없습니다. (스크래핑 결과 없음)"}), 500

        print(f"DEBUG: '{code}'에 대한 뉴스 {len(formatted_news)}개 성공적으로 스크래핑.")
        return jsonify(formatted_news[:10]) # 최대 10개 반환

    except requests.exceptions.Timeout:
        print(f"DEBUG: 뉴스 스크래핑 요청 타임아웃 발생 (종목코드: {code})")
        return jsonify({"error": "뉴스 요청 시간 초과 (스크래핑 실패)"}), 500
    except requests.exceptions.RequestException as e:
        print(f"DEBUG: 뉴스 스크래핑 요청 오류 (종목코드: {code}): {e}")
        return jsonify({"error": f"뉴스 스크래핑 실패: {str(e)}"}), 500
    except Exception as e:
        print(f"DEBUG: 뉴스 스크래핑 처리 중 알 수 없는 오류 발생 (종목코드: {code}): {e}")
        traceback.print_exc()
        return jsonify({"error": f"뉴스 스크래핑 중 알 수 없는 오류: {str(e)}"}), 500

def _get_news_from_naver_scraping():
    """
    네이버 금융 메인 뉴스 페이지에서 일반 시장 뉴스를 스크래핑하는 함수 (폴백용).
    """
    news_list = []
    print("DEBUG: Attempting to scrape general market news from Naver Finance.")
    try:
        url = "https://finance.naver.com/news/mainnews.naver"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # 네이버 금융 메인 뉴스 페이지의 최신 구조에 맞게 선택자 업데이트
        # 일반적으로 'newsList' 내부에 뉴스 항목이 있음.
        news_items = soup.select('.main_news .newsList .articleSubject a')
        press_items = soup.select('.main_news .newsList .articleSummary .press')
        date_items = soup.select('.main_news .newsList .articleSummary .wdate')

        for i in range(min(len(news_items), 10)): # 최대 10개 뉴스
            title = news_items[i].get_text(strip=True)
            link = news_items[i]['href']
            press = press_items[i].get_text(strip=True) if i < len(press_items) else 'N/A'
            date_time = date_items[i].get_text(strip=True) if i < len(date_items) else 'N/A'

            if link.startswith('/'):
                link = f"https://finance.naver.com{link}"

            news_list.append({
                'title': title,
                'press': press,
                'date': date_time,
                'url': link
            })
        print(f"DEBUG: Naver general news scraping successful. Found {len(news_list)} news items.")
    except Exception as e:
        print(f"DEBUG: Error fetching general market news via scraping: {e}")
        traceback.print_exc()
        news_list.append({'title': '일반 시장 뉴스를 불러오는 데 실패했습니다 (크롤링 오류).', 'press': 'N/A', 'date': 'N/A', 'url': '#'})
    return news_list

def get_latest_indicator_value(stats_code, item_code, indicator_name):
    default_response = {'name': indicator_name, 'value': 'N/A', 'date': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0, 'error': None}
    bok_api_key = os.getenv("ECOS_API_KEY")
    if not bok_api_key:
        default_response['error'] = 'ECOS API 키가 없습니다.'
        return default_response
    try:
        end_date_str = datetime.now().strftime('%Y%m')
        start_date_str = (datetime.now() - timedelta(days=365)).strftime('%Y%m')
        url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{bok_api_key}/json/kr/1/10/{stats_code}/MM/{start_date_str}/{end_date_str}/{item_code}")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        rows = data.get("StatisticSearch", {}).get("row", [])
        if not rows:
            default_response['error'] = '조회된 데이터가 없습니다.'
            return default_response
        latest = rows[-1]
        latest_value = float(latest['DATA_VALUE'])
        latest_date = f"{latest['TIME'][:4]}.{latest['TIME'][4:]}"
        if len(rows) < 2:
            return {'name': indicator_name, 'value': f"{latest_value:,.2f}", 'date': latest_date, 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0, 'error': None}
        previous = rows[-2]
        previous_value = float(previous['DATA_VALUE'])
        change = latest_value - previous_value
        change_pct = (change / previous_value) * 100 if previous_value != 0 else 0
        return {'name': indicator_name, 'value': f"{latest_value:,.2f}", 'date': latest_date, 'change': f"{change:,.2f}", 'change_pct': f"{change_pct:+.2f}%", 'raw_change': change, 'error': None}
    except Exception as e:
        default_response['error'] = f'데이터 조회 실패: {e}'
        return default_response
    
def get_general_market_news():
    news_api_key = os.getenv("NEWS_API_KEY")
    # NewsAPI.org 키가 없으면 바로 네이버 스크래핑으로 폴백
    if not news_api_key: 
        print("DEBUG: NEWS_API_KEY 없음. 네이버 일반 시장 뉴스 스크래핑으로 폴백합니다.")
        return _get_news_from_naver_scraping()
    try:
        query = "거시경제 OR 금리 OR 환율 OR CPI OR 인플레이션 OR GDP OR 연준 OR 한국은행 OR 무역수지 OR 통화정책 OR 재정정책 OR 경기 침체 OR 세계 경제 OR 공급망 OR 국채 OR 부동산"
        api_url = f"https://newsapi.org/v2/everything?q={query}&language=ko&sortBy=publishedAt&apiKey={news_api_key}&pageSize=10"
        print("DEBUG: NewsAPI.org로 일반 시장 뉴스 검색 시도...")
        response = requests.get(api_url, timeout=7) # 타임아웃 7초로 늘림
        response.raise_for_status()
        data = response.json()
        if data.get('status') == 'ok' and data.get('articles'):
            print(f"DEBUG: NewsAPI.org에서 일반 시장 뉴스 {len(data['articles'])}개 성공적으로 가져옴.")
            return [{'title': a.get('title', '제목 없음'), 'press': a.get('source', {}).get('name', 'N/A'), 'date': a.get('publishedAt', '')[:10], 'url': a.get('url', '#')} for a in data['articles']]
        else:
            print("DEBUG: NewsAPI.org 응답 상태 'ok' 아님 또는 기사 없음. 네이버 일반 시장 뉴스 스크래핑으로 폴백합니다.")
            return _get_news_from_naver_scraping()
    except Exception as e:
        print(f"DEBUG: NewsAPI.org 오류: {e}. 네이버 일반 시장 뉴스 스크래핑으로 폴백합니다.")
        traceback.print_exc()
        return _get_news_from_naver_scraping()

@app.route('/api/latest-data')
def get_latest_data():
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=10)
        data = {}
        for ticker, name in [('KS11', 'kospi'), ('KQ11', 'kosdaq'), ('USD/KRW', 'usdkrw')]:
            df = get_fdr_or_yf_data(ticker, start_date, end_date)
            if ticker == 'USD/KRW' and 'Close' not in df.columns and 'USD/KRW' in df.columns:
                df.rename(columns={'USD/KRW': 'Close'}, inplace=True)
            if not df.empty and 'Date' in df.columns:
                df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
                df.set_index('Date', inplace=True)
            data[name] = calculate_change_info(df.copy().set_index('Date'), name.upper())
        
        wti_df = get_wti_data(10)
        if not wti_df.empty: wti_df.set_index('Date', inplace=True)
        data['wti'] = calculate_change_info(wti_df, 'WTI')
        
        return jsonify(data)
    except Exception as e:
        print(f"Error in /api/latest-data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index_main():
    return render_template('index_main.html')

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
        new_cache = {'date': latest_bday}
        try:
            kospi_all_data, kosdaq_all_data = get_market_rank_data(latest_bday)
            new_cache.update({'kospi_all_data': kospi_all_data, 'kosdaq_all_data': kosdaq_all_data, 'cpi_info': get_latest_indicator_value("901Y001", "0", "소비자물가지수"), 'interest_rate_info': get_latest_indicator_value("722Y001", "0001000", "기준금리"), 'market_news': get_general_market_news()})
        except Exception as e:
            print(f"Error creating cache: {e}")
            new_cache.update({'kospi_all_data': [], 'kosdaq_all_data': [], 'market_news': []})
        cache = new_cache
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    context = {'today': datetime.strptime(latest_bday, '%Y%m%d').strftime('%Y-%m-%d'), **cache}
    days_to_fetch = 60
    start_date, end_date = datetime.now() - timedelta(days=days_to_fetch), datetime.now()
    
    for ticker, name in [('KS11', 'kospi'), ('KQ11', 'kosdaq'), ('USD/KRW', 'usdkrw')]:
        try:
            df = get_fdr_or_yf_data(ticker, start_date, end_date)
            if ticker == 'USD/KRW' and 'Close' not in df.columns and 'USD/KRW' in df.columns:
                df.rename(columns={'USD/KRW': 'Close'}, inplace=True)
            df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
            context[f'{name}_info'] = calculate_change_info(df.copy().set_index('Date'), name.upper())
            df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
            context[f'{name}_data'] = df.tail(30).to_dict('records')
        except Exception:
            context[f'{name}_data'], context[f'{name}_info'] = [], {'value': 'N/A'}

    try:
        wti_df = get_wti_data(days_to_fetch)
        context['wti_info'] = calculate_change_info(wti_df.copy().set_index('Date'), 'WTI')
        wti_df['Date'] = pd.to_datetime(wti_df['Date']).dt.strftime('%Y-%m-%d')
        context['wti_data'] = wti_df.tail(30).to_dict('records')
    except Exception:
        context['wti_data'], context['wti_info'] = [], {'value': 'N/A'}
        
    kospi_all_data = cache.get('kospi_all_data', [])
    kosdaq_all_data = cache.get('kosdaq_all_data', [])
    context['kospi_top_volume'] = sorted(kospi_all_data, key=lambda x: x.get('Volume', 0), reverse=True)[:10]
    context['kospi_top_value'] = sorted(kospi_all_data, key=lambda x: x.get('TradingValue', 0), reverse=True)[:10]
    context['kospi_top_gainers'] = sorted(kospi_all_data, key=lambda x: x.get('ChangeRatio', -1000), reverse=True)[:10]
    context['kospi_top_losers'] = sorted(kospi_all_data, key=lambda x: x.get('ChangeRatio', 1000))[:10]
    context['kosdaq_top_volume'] = sorted(kosdaq_all_data, key=lambda x: x.get('Volume', 0), reverse=True)[:10]
    context['kosdaq_top_value'] = sorted(kosdaq_all_data, key=lambda x: x.get('TradingValue', 0), reverse=True)[:10]
    context['kosdaq_top_gainers'] = sorted(kosdaq_all_data, key=lambda x: x.get('ChangeRatio', -1000), reverse=True)[:10]
    context['kosdaq_top_losers'] = sorted(kosdaq_all_data, key=lambda x: x.get('ChangeRatio', 1000))[:10]


    return render_template('index.html', **context)

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

@app.context_processor
def inject_current_year():
    return {'current_year': datetime.utcnow().year}

@app.route('/api/chart_data/<string:ticker>/<string:interval>')
def get_chart_data(ticker, interval):
    """
    [수정됨] yfinance의 멀티 인덱스 컬럼을 처리하여 안정성을 확보하는 최종 로직
    """
    try:
        end_date = datetime.now()
        yf_interval = '1d'
        if interval == 'daily':
            start_date = end_date - timedelta(days=90)
        elif interval == 'weekly':
            yf_interval = '1wk'
            start_date = end_date - timedelta(days=365 * 3)
        elif interval == 'monthly':
            yf_interval = '1mo'
            start_date = end_date - timedelta(days=365 * 10)
        else:
            return jsonify({"error": "Invalid interval"}), 400

        raw_df = get_fdr_or_yf_data(ticker, start_date, end_date, interval=yf_interval)
        if raw_df.empty:
            return jsonify({"error": "No data found for ticker"}), 404

        # --- 문제 해결 코드 시작 ---
        # yfinance가 반환하는 MultiIndex 컬럼을 단일 레벨로 변환합니다.
        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)
        # --- 문제 해결 코드 끝 ---

        # 1. 원본 데이터에서 중복 컬럼 제거
        raw_df = raw_df.loc[:, ~raw_df.columns.duplicated()]

        # 2. 날짜와 종가 컬럼명 식별
        date_col_name = next((col for col in ['Date', 'Datetime', 'index'] if col in raw_df.columns), None)
        close_col_name = next((col for col in ['Close', 'Adj Close', ticker] if col in raw_df.columns), None)

        if not date_col_name or not close_col_name:
            print(f"DEBUG: Could not find Date or Close column in {raw_df.columns}")
            return jsonify({"error": "Could not identify Date or Close column"}), 500

        # 3. 필요한 컬럼만으로 새 데이터프레임 생성
        clean_df = pd.DataFrame({
            'Date': raw_df[date_col_name],
            'Close': pd.to_numeric(raw_df[close_col_name], errors='coerce')
        })

        # 4. 데이터 정제
        clean_df.dropna(inplace=True)
        if clean_df.empty:
            return jsonify({"error": "Data became empty after cleaning"}), 404

        # 5. 최종 데이터 가공 및 반환
        clean_df['Date'] = pd.to_datetime(clean_df['Date']).dt.strftime('%Y-%m-%d')
        
        return jsonify(clean_df[['Date', 'Close']].to_dict('records'))
        
    except Exception as e:
        import traceback
        print(f"Error in get_chart_data for {ticker}/{interval}: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
            
if __name__ == '__main__':
    app.run(debug=True, port=5000)