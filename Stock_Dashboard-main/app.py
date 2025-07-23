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


from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp
from blueprints import askfin
from blueprints.askfin import askfin_bp, initialize_global_data
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
        return f"{int(float(value)):,}원" 
    except (ValueError, TypeError):
        return value

app.jinja_env.filters['format_kr'] = format_kr
app.jinja_env.filters['format_price'] = format_price

# --- Data Fetching Functions ---
CACHE_PATH = 'cache/market_data.json'

def get_latest_business_day():
    """pykrx를 사용해 가장 최근의 영업일을 안정적으로 찾습니다."""
    return stock.get_nearest_business_day_in_a_week()


def get_market_rank_data(date_str):
    """지정된 날짜의 KOSPI, KOSDAQ 순위 정보를 가져옵니다."""
    
    kospi_df = stock.get_market_ohlcv(date_str, market="KOSPI").reset_index()
    kosdaq_df = stock.get_market_ohlcv(date_str, market="KOSDAQ").reset_index()

    for df in [kospi_df, kosdaq_df]:
        tickers = df['티커']
        names = [askfin.GLOBAL_TICKER_NAME_MAP.get(ticker, ticker) for ticker in tickers]
        df['Name'] = names
        
        df.rename(columns={
            '티커': 'Code',
            '종가': 'Close',
            '거래량': 'Volume',
            '거래대금': 'TradingValue',
            '등락률': 'ChangeRatio'
        }, inplace=True)
        
        df['Close'] = df['Close'].fillna(0)
        df['Volume'] = df['Volume'].fillna(0)
        df['TradingValue'] = df['TradingValue'].fillna(0)
        df['ChangeRatio'] = df['ChangeRatio'].fillna(0)
        
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
    """
    특정 종목에 대한 뉴스를 가져오는 기존 함수 (네이버 금융 스크래핑).
    """
    try:
        url = f"https://m.stock.naver.com/api/news/stock/{code}?pageSize=10&page=1"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)
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

def _get_news_from_naver_scraping():
    """
    NewsAPI.org 호출 실패 시 폴백으로 사용될 네이버 금융 메인 뉴스 스크래핑 함수.
    주의: 웹사이트 구조 변경에 매우 취약하며, IP 차단 가능성이 있습니다.
    """
    news_list = []
    print("DEBUG: Attempting to scrape news from Naver Finance.") # 디버그 추가
    try:
        url = "https://finance.naver.com/news/mainnews.naver"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

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
        print(f"DEBUG: Naver scraping successful. Found {len(news_list)} news items.") # 디버그 추가
    except Exception as e:
        print(f"DEBUG: Error fetching general market news via scraping: {e}") # 디버그 추가
        news_list.append({'title': '일반 시장 뉴스를 불러오는 데 실패했습니다 (크롤링 오류).', 'press': 'N/A', 'date': 'N/A', 'url': '#'})
    return news_list

def get_latest_indicator_value(stats_code, item_code, indicator_name):
    """한국은행(BOK) ECOS API를 통해 특정 지표의 최신 값을 가져옵니다."""
    bok_api_key = os.getenv("ECOS_API_KEY")
    # 기본 오류/반환 형태 정의
    default_response = {
        'name': indicator_name,
        'value': 'N/A',
        'date': 'N/A',
        'change': 'N/A',
        'change_pct': 'N/A',
        'raw_change': 0,
        'error': None
    }

    if not bok_api_key:
        default_response['error'] = 'ECOS API 키가 없습니다.'
        return default_response

    try:
        end_date_str = datetime.now().strftime('%Y%m')
        # 조회 기간을 60일에서 365일로 늘립니다.
        start_date_str = (datetime.now() - timedelta(days=365)).strftime('%Y%m')
        
        url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{bok_api_key}/json/kr/1/10/"
               f"{stats_code}/MM/{start_date_str}/{end_date_str}/{item_code}")

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        rows = data.get("StatisticSearch", {}).get("row", [])
        
        if len(rows) == 0:
            default_response['error'] = '조회된 데이터가 없습니다.'
            return default_response

        latest = rows[-1]
        latest_value = float(latest['DATA_VALUE'])

        if len(rows) < 2:
            # 데이터가 1개만 있을 경우
            return {
                'name': indicator_name,
                'value': f"{latest_value:,.2f}",
                'date': f"{latest['TIME'][:4]}.{latest['TIME'][4:]}",
                'change': 'N/A',
                'change_pct': 'N/A',
                'raw_change': 0,
                'error': None
            }

        previous = rows[-2]
        previous_value = float(previous['DATA_VALUE'])
        change = latest_value - previous_value
        change_pct = (change / previous_value) * 100 if previous_value != 0 else 0

        return {
            'name': indicator_name,
            'value': f"{latest_value:,.2f}",
            'date': f"{latest['TIME'][:4]}.{latest['TIME'][4:]}",
            'change': f"{change:,.2f}",
            'change_pct': f"{change_pct:+.2f}%",
            'raw_change': change,
            'error': None
        }
    except Exception as e:
        default_response['error'] = f'데이터 조회 실패: {e}'
        return default_response
    
def get_general_market_news():
    """
    NewsAPI.org를 통해 일반 시장 뉴스를 가져옵니다.
    API 키가 없거나 호출 실패 시 네이버 금융 스크래핑으로 폴백합니다.
    """
    news_api_key = os.getenv("NEWS_API_KEY")
    
    if not news_api_key:
        print("DEBUG: NEWS_API_KEY not set. Falling back to Naver scraping.") 
        return _get_news_from_naver_scraping()

    news_list = []
    print("DEBUG: Attempting to fetch news from NewsAPI.org.") 
    try:
        query = "거시경제 OR 금리 OR 환율 OR CPI OR 인플레이션 OR GDP OR 연준 OR 한국은행 OR 무역수지 OR 통화정책 OR 재정정책 OR 경기 침체 OR 세계 경제 OR 공급망 OR 국채 OR 부동산"
        api_url = f"https://newsapi.org/v2/everything?q={query}&language=ko&sortBy=publishedAt&apiKey={news_api_key}&pageSize=10"
        
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        data = response.json()

        print(f"DEBUG: NewsAPI.org raw response status: {data.get('status')}") 
        print(f"DEBUG: NewsAPI.org raw response message: {data.get('message')}") 
        
        if data.get('status') == 'ok' and data.get('articles'):
            for article in data['articles']:
                news_list.append({
                    'title': article.get('title', '제목 없음'),
                    'press': article.get('source', {}).get('name', 'N/A'),
                    'date': article.get('publishedAt', '')[:10], # YYYY-MM-DD 형식으로 자르기
                    'url': article.get('url', '#')
                })
            print(f"DEBUG: NewsAPI.org successful. Found {len(news_list)} news items.")
        else:
            print(f"DEBUG: NewsAPI.org responded with error or no articles. Falling back to Naver scraping.")
            return _get_news_from_naver_scraping()

    except requests.exceptions.RequestException as e:
        print(f"DEBUG: NewsAPI.org network error: {e}. Falling back to Naver scraping.")
        return _get_news_from_naver_scraping()
    except Exception as e:
        print(f"DEBUG: NewsAPI.org unexpected error: {e}. Falling back to Naver scraping.")
        return _get_news_from_naver_scraping()
    
    return news_list

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
def index_main():
    """첫 랜딩 페이지를 렌더링합니다."""
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
        print(f"[{datetime.now()}] Creating new cache for date: {latest_bday}")
        new_cache = {'date': latest_bday}
        print(f"DEBUG: Attempting to fetch data for business day: {latest_bday}")

        try:
            kospi_all_data, kosdaq_all_data = get_market_rank_data(latest_bday)

            new_cache['kospi_all_data'] = kospi_all_data
            new_cache['kosdaq_all_data'] = kosdaq_all_data
            
            new_cache['cpi_info'] = get_latest_indicator_value("901Y001", "0", "소비자물가지수")
            new_cache['interest_rate_info'] = get_latest_indicator_value("722Y001", "0001000", "기준금리")
            new_cache['market_news'] = get_general_market_news() # NewsAPI.org 또는 스크래핑
            # ----------------------------------------------------

        except Exception as e:
            print(f"Error fetching pykrx data for index page: {e}")
            new_cache['kospi_all_data'] = []
            new_cache['kosdaq_all_data'] = []

            new_cache['cpi_info'] = {'name': '소비자물가지수', 'value': 'N/A', 'date': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0, 'error': str(e)}
            new_cache['interest_rate_info'] = {'name': '기준금리', 'value': 'N/A', 'date': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0, 'error': str(e)}
            new_cache['market_news'] = [{'title': '데이터 로딩 오류', 'press': 'N/A', 'date': 'N/A', 'url': '#'}]

        cache = new_cache
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    kospi_all_data = cache.get('kospi_all_data', [])
    kosdaq_all_data = cache.get('kosdaq_all_data', [])
    
    cpi_info = cache.get('cpi_info', {'name': '소비자물가지수', 'value': 'N/A', 'date': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0, 'error': '데이터 없음'})
    interest_rate_info = cache.get('interest_rate_info', {'name': '기준금리', 'value': 'N/A', 'date': 'N/A', 'change': 'N/A', 'change_pct': 'N/A', 'raw_change': 0, 'error': '데이터 없음'})
    market_news = cache.get('market_news', [{'title': '뉴스를 불러올 수 없습니다.', 'press': 'N/A', 'date': 'N/A', 'url': '#'}])

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
        if 'index' in kospi_df_full.columns:
            kospi_df_full.rename(columns={'index': 'Date'}, inplace=True)
        kospi_df_full['Date'] = pd.to_datetime(kospi_df_full['Date'])
        kospi_data = kospi_df_full.tail(30).to_dict('records')
    except Exception as e: print(f"Error fetching KOSPI data: {e}")

    try:
        kosdaq_df_full = fdr.DataReader('KQ11', start=start_date, end=end_date)
        kosdaq_info = calculate_change_info(kosdaq_df_full, 'KOSDAQ')
        kosdaq_df_full.reset_index(inplace=True)
        
        if 'index' in kosdaq_df_full.columns:
            kosdaq_df_full.rename(columns={'index': 'Date'}, inplace=True)
        kosdaq_df_full['Date'] = pd.to_datetime(kosdaq_df_full['Date'])
        kosdaq_data = kosdaq_df_full.tail(30).to_dict('records')
    except Exception as e: print(f"Error fetching KOSDAQ data: {e}")

    try:
        usdkrw_df_full = fdr.DataReader('USD/KRW', start=start_date, end=end_date)
        usdkrw_info = calculate_change_info(usdkrw_df_full, 'USD/KRW')
        usdkrw_df_full.reset_index(inplace=True)
        if 'index' in usdkrw_df_full.columns:
            usdkrw_df_full.rename(columns={'index': 'Date'}, inplace=True)
        usdkrw_df_full['Date'] = pd.to_datetime(usdkrw_df_full['Date'])
        usdkrw_data = usdkrw_df_full.tail(30).to_dict('records')
    except Exception as e: print(f"Error fetching USD/KRW data: {e}")

    try:
        wti_df_full = get_wti_data(days_to_fetch)
        wti_df_full['Date'] = pd.to_datetime(wti_df_full['Date'])

        wti_df_full_for_calc = wti_df_full.copy()
    
        wti_df_full_for_calc.set_index('Date', inplace=True)
        wti_info = calculate_change_info(wti_df_full_for_calc, 'WTI')
        wti_data = wti_df_full.tail(30).to_dict('records')
    except Exception as e: print(f"Error fetching WTI data: {e}")
        
    for data_list in [kospi_data, kosdaq_data, usdkrw_data, wti_data]:
        for item in data_list:
            if 'Date' in item:
                if isinstance(item['Date'], datetime):
                    item['Date'] = item['Date'].strftime('%Y-%m-%d')
                elif isinstance(item['Date'], pd.Timestamp):
                    item['Date'] = item['Date'].strftime('%Y-%m-%d')
                elif isinstance(item['Date'], str):
                    pass
                else:
                    item['Date'] = str(item['Date'])
    
    for item in kospi_all_data:
        item.setdefault('TradingValue', 0) 
        item.setdefault('Changes', 0) 
        item.setdefault('ChagesRatio', 0) 
    for item in kosdaq_all_data:
        item.setdefault('TradingValue', 0)
        item.setdefault('Changes', 0) 
        item.setdefault('ChagesRatio', 0) 

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
        
        today=today_str_display,
        
        cpi_info=cpi_info,
        interest_rate_info=interest_rate_info,
        market_news=market_news
        )

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

@app.context_processor
def inject_recent_stocks():
    def get_recent_stocks():
        recent_codes = session.get('recent_stocks', [])
        recent_stocks = []
        for code in recent_codes:
            try:
                ticker = yf.Ticker(code)
                info = ticker.info
                name = info.get('shortName', code)
                price = info.get('currentPrice', 'N/A')
                recent_stocks.append({'code': code, 'name': name, 'price': price})
            except Exception:
                recent_stocks.append({'code': code, 'name': code, 'price': 'N/A'})
        return recent_stocks

    return dict(recent_stocks=get_recent_stocks())

if __name__ == '__main__':
    app.run(debug=True, port=5000)