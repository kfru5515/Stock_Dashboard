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
from urllib.parse import urljoin
from readability import Document
import pickle
import re

from run import EnhancedStockPredictor

from transformers import AutoTokenizer, pipeline
from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp
from blueprints import askfin
from blueprints.askfin import askfin_bp, initialize_global_data, GLOBAL_TICKER_NAME_MAP #
from blueprints.search import search_bp
from dotenv import load_dotenv

from db.extensions import db
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ── 금융 키워드 세트 (data‑files/finance.csv) ─────────────────────────
finance_df = pd.read_csv(
    os.path.join(os.path.dirname(__file__), "data_files", "finance.csv"),
    encoding="utf-8-sig"
)
# CSV에 'keyword' 컬럼이 있다고 가정
FINANCE_KEYWORDS = set(
    finance_df['keyword']
    .dropna()
    .astype(str)
    .str.lower()
    .str.strip()
)

# ── Sentiment model & pipelines 로딩 ─────────────────────────
SAVED_MODEL_DIR = os.path.join(os.path.dirname(__file__), "data_files", "saved_model")
tokenizer = AutoTokenizer.from_pretrained(
    SAVED_MODEL_DIR, use_fast=True, trust_remote_code=True
)
sentiment_pipeline = pipeline(
    'sentiment-analysis',
    model=SAVED_MODEL_DIR,
    tokenizer=SAVED_MODEL_DIR,
    return_all_scores=False,
    device=-1
)

# 불용 문자/토큰 제거용 (필요시 더 추가)
STOP_CHARS_PATTERN = re.compile(r"[.,·()\[\]{}!?;:“”\"'`…]")

def clean_for_sentiment(text: str) -> str:
    text = STOP_CHARS_PATTERN.sub(" ", text)
    text = text.replace("[UNK]", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

# ── 기업명 추출기 로딩 ────────────────────────────────────
with open(os.path.join(os.path.dirname(__file__), 'data_files', 'keyword_processor.pkl'), 'rb') as f:
    keyword_processor = pickle.load(f)
corp_df = pd.read_csv(
    os.path.join(os.path.dirname(__file__), 'data_files', 'corp_names.csv'),
    encoding='utf-8-sig'
)
COMPANY_SET = set(corp_df['corp_name'].astype(str))
STOPWORDS = {"ETF", "ETN", "신탁", "SPAC", "펀드", "리츠"}

BOUNDARY = r"[가-힣A-Za-z0-9]"   # 단어로 취급할 문자들

def is_standalone(word: str, text: str) -> bool:
    """
    text 안에서 word가 양옆이 문자/숫자에 붙어있지 않은 '독립된' 형태로 존재하는지 검사
    """
    pattern = rf"(?<!{BOUNDARY}){re.escape(word)}(?!{BOUNDARY})"
    return re.search(pattern, text) is not None

def extract_companies(text: str) -> list[str]:
    cleaned = re.sub(r'ⓒ.*', '', text)
    candidates = keyword_processor.extract_keywords(cleaned)

    uniq = dict.fromkeys(candidates)  # 순서 유지한 중복 제거
    filtered = []
    for w in uniq:
        # 1) 기본 필터
        if w in STOPWORDS: 
            continue
        if w not in COMPANY_SET:
            continue
        # 2) 단독 단어인지 확인 (부분 문자열 방지)
        if not is_standalone(w, cleaned):
            continue
        filtered.append(w)
    return filtered

# ── 본문(fetch) 헬퍼 ─────────────────────────────────────────
def fetch_body(url: str) -> str:
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        resp.raise_for_status()
        html = resp.text
        doc = Document(html) #
        content_html = doc.summary() #
        soup = BeautifulSoup(content_html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        paragraphs = [
            p.get_text(strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 20
        ]
        return "\n".join(paragraphs)
    except Exception:
        return ""

# ── Jinja2 필터 정의 ────────────────────────────────────────

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
CACHE_DIR = 'cache'

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
    # ticker가 '.KS' 또는 '.KQ'로 끝나는 한국 주식 코드일 경우, yfinance 티커 맵에서 제거하고 직접 사용
    # 왜냐하면 'KS11'이나 'KQ11' 같은 지수 티커와 '005930.KS' 같은 개별 주식 티커를 구분해야 하기 때문
    yf_ticker_map = {'USD/KRW': 'KRW=X', 'KS11': '^KS11', 'KQ11': '^KQ11', 'CL=F': 'CL=F'}
    
    # 한국 개별 주식은 yf_ticker_map에 없으므로 직접 ticker 사용
    if ticker.endswith(('.KS', '.KQ')):
        actual_yf_ticker = ticker
    else:
        actual_yf_ticker = yf_ticker_map.get(ticker, ticker) # 매핑된 이름 사용

    if interval != '1d':
        try:
            print(f"Attempting to fetch '{actual_yf_ticker}' with yfinance (interval: {interval})...")
            yf_df = yf.download(actual_yf_ticker, start=start, end=end, interval=interval, auto_adjust=True, show_errors=True) # show_errors=True 추가
            if not yf_df.empty:
                yf_df = yf_df.reset_index()
                if 'index' in yf_df.columns:
                    yf_df.rename(columns={'index': 'Date'}, inplace=True)
                elif 'Datetime' in yf_df.columns:
                    yf_df.rename(columns={'Datetime': 'Date'}, inplace=True)
                print("yfinance fetch successful.")
                return yf_df[['Date', 'Close']].copy()
            raise ValueError("yfinance returned empty dataframe or ticker not in map")
        except Exception as yf_e:
            print(f"yfinance (interval) failed for '{actual_yf_ticker}': {yf_e}. Falling back to fdr.")

    try:
        print(f"Attempting to fetch '{ticker}' with fdr...")
        df = fdr.DataReader(ticker, start, end)
        if df.empty: raise ValueError("FDR returned empty dataframe")
        print("FDR fetch successful.")
        df = df.reset_index()
        if 'Date' not in df.columns:
            if 'index' in df.columns:
                df.rename(columns={'index': 'Date'}, inplace=True)
        return df[['Date', 'Close']].copy()
    except Exception as e:
        print(f"FDR failed for '{ticker}': {e}. Falling back to yfinance (daily).")
        try:
            # yfinance 일별 데이터 폴백
            yf_df = yf.download(actual_yf_ticker, start=start, end=end, interval='1d', auto_adjust=True, show_errors=True) # show_errors=True 추가
            if yf_df.empty: return pd.DataFrame()
            print("yfinance (daily) fetch successful.")
            yf_df = yf_df.reset_index()
            if 'index' in yf_df.columns:
                yf_df.rename(columns={'index': 'Date'}, inplace=True)
            elif 'Datetime' in yf_df.columns:
                yf_df.rename(columns={'Datetime': 'Date'}, inplace=True)
            return yf_df[['Date', 'Close']].copy()
        except Exception as yf_e:
            print(f"yfinance (daily) also failed for '{actual_yf_ticker}': {yf_e}")
            return pd.DataFrame()

@app.route('/news/<string:code>')
def get_news(code):
    """
    특정 종목 코드에 대한 뉴스를 가져옵니다.
    code는 '005930.KS'와 같은 yfinance 형식일 수 있습니다.
    """
    formatted_news = []
    # yfinance 코드에서 6자리 순수 종목 코드 추출 (네이버 API용)
    stock_code_6_digit = code.split('.')[0]
    print(f"DEBUG: '{code}' (6자리: {stock_code_6_digit}) 종목 코드에 대해 네이버 모바일 API에서 뉴스 가져오기 시도.")
    try:
        url = f"https://m.stock.naver.com/api/news/stock/{stock_code_6_digit}?pageSize=10&page=1" # 6자리 코드 사용
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        raw_data = response.json()

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
                                    'title': item.get('title'),
                                    'press': item.get('officeName'),
                                    'date': f_date,
                                    'url': f"https://n.news.naver.com/mnews/article/{office_id}/{article_id}"
                                })

        if not formatted_news:
            print(f"DEBUG: '{code}'에 대한 뉴스를 API에서 가져왔으나 항목이 0개입니다.")
            return jsonify({"error": "관련 뉴스가 없습니다. (API 결과 없음)"}), 200 # 500 대신 200으로 변경
        
        # 뉴스 감성 분석 및 기업명 추출 (뉴스 컨텐츠 크롤링이 필요하므로 시간이 걸릴 수 있음)
        processed_news = []
        for news_item in formatted_news[:5]: # 너무 많은 뉴스 본문 분석은 비효율적이므로 상위 5개만
            body = fetch_body(news_item["url"])
            sentiment = "없음"
            companies = []

            # 본문이 비어있지 않고 길이가 충분할 때만 감성 분석 및 기업명 추출 시도
            if body and len(body.strip()) > 50: # 최소 길이 설정
                title_clean = clean_for_sentiment(news_item["title"])
                target_text = title_clean if title_clean.strip() else clean_for_sentiment(body)[:256]

                if target_text.strip():
                    try:
                        sentiment_result = sentiment_pipeline(target_text)[0]["label"]
                        sentiment = sentiment_result if sentiment_result else "없음"
                    except Exception as sentiment_e:
                        print(f"DEBUG: 감성 분석 오류: {sentiment_e}")
                        sentiment = "오류" # 감성 분석 실패 시

                try:
                    companies = extract_companies(body)
                except Exception as company_e:
                    print(f"DEBUG: 기업명 추출 오류: {company_e}")
                    companies = [] # 기업명 추출 실패 시
            
            processed_news.append({
                'title': news_item['title'],
                'press': news_item['press'],
                'date': news_item['date'],
                'url': news_item['url'],
                'sentiment': sentiment,
                'companies': companies
            })

        print(f"DEBUG: '{code}'에 대한 뉴스 {len(processed_news)}개 성공적으로 가져옴 (감성 분석 포함).")
        return jsonify(processed_news)

    except requests.exceptions.Timeout:
        print(f"DEBUG: 뉴스 API 요청 타임아웃 발생 (종목코드: {code})")
        return jsonify({"error": "뉴스 요청 시간 초과 (API 호출 실패)"}), 500
    except requests.exceptions.RequestException as e:
        print(f"DEBUG: 뉴스 API 요청 오류 (종목코드: {code}): {e}")
        return jsonify({"error": f"뉴스 API 호출 실패: {str(e)}"}), 500
    except json.JSONDecodeError:
        print(f"DEBUG: 뉴스 API 응답이 유효한 JSON이 아님 (종목코드: {code})")
        return jsonify({"error": "뉴스 API 응답 파싱 실패"}), 500
    except Exception as e:
        print(f"DEBUG: 뉴스 API 처리 중 알 수 없는 오류 발생 (종목코드: {code}): {e}")
        traceback.print_exc()
        return jsonify({"error": f"뉴스 API 처리 중 알 수 없는 오류: {str(e)}"}), 500


def _get_news_from_naver_scraping():
    news_list = []
    print("DEBUG: Attempting to scrape general market news from Naver Finance.")
    try:
        url = "https://finance.naver.com/news/mainnews.naver"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        news_items = soup.select('.main_news .newsList .articleSubject a')
        press_items = soup.select('.main_news .newsList .articleSummary .press')
        date_items = soup.select('.main_news .newsList .articleSummary .wdate')

        for i in range(min(len(news_items), 10)):
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

# ECOS 주요 통계 지표 현황 데이터를 가져오는 함수
def get_key_statistic_current_data():
    """
    한국은행 ECOS '주요 통계 지표 현황' 데이터를 가져옵니다.
    (KeyStatisticList API를 사용하며, CLASS_NAME, KEYSTAT_NAME, DATA_VALUE 등을 포함)
    """
    ecos_api_key = os.getenv("ECOS_API_KEY")
    if not ecos_api_key:
        print("ECOS API 키가 설정되지 않아 주요 통계 현황 데이터를 가져올 수 없습니다.")
        return []

    api_url = f"https://ecos.bok.or.kr/api/KeyStatisticList/{ecos_api_key}/xml/kr/1/100/"

    key_statistics_current_data = []
    print(f"DEBUG: ECOS 주요 통계 현황 API 호출 시도: {api_url}")
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'xml')
        items = soup.find_all('row')

        if not items:
            print("ECOS 주요 통계 현황 API에서 데이터를 찾을 수 없습니다.")
            return []

        for item in items:
            class_name = item.find('CLASS_NAME').get_text() if item.find('CLASS_NAME') else 'N/A'
            keystat_name = item.find('KEYSTAT_NAME').get_text() if item.find('KEYSTAT_NAME') else 'N/A'
            data_value = item.find('DATA_VALUE').get_text() if item.find('DATA_VALUE') else 'N/A'
            cycle = item.find('CYCLE').get_text() if item.find('CYCLE') else 'N/A'
            unit_name = item.find('UNIT_NAME').get_text() if item.find('UNIT_NAME') else 'N/A'

            key_statistics_current_data.append({
                "CLASS_NAME": class_name,
                "KEYSTAT_NAME": keystat_name,
                "DATA_VALUE": data_value,
                "CYCLE": cycle,
                "UNIT_NAME": unit_name
            })
        print(f"DEBUG: ECOS 주요 통계 현황 데이터 {len(key_statistics_current_data)}개 항목 성공적으로 가져옴.")
        return key_statistics_current_data

    except requests.exceptions.Timeout:
        print("ECOS 주요 통계 현황 API 요청 시간 초과.")
    except requests.exceptions.RequestException as e:
        print(f"ECOS 주요 통계 현황 API 요청 오류: {e}")
        traceback.print_exc()
    except Exception as e:
        print(f"ECOS 주요 통계 현황 데이터 처리 중 오류: {e}")
        traceback.print_exc()
    return []


def get_general_market_news():
    """
    한국 시장 주요 뉴스를 가져와
    본문(fetch) → 금융 키워드 필터 → 감성분석(제목만) & 기업명추출 후 반환합니다.
    """
    news_api_key = os.getenv("NEWS_API_KEY")
    raw_list = []

    # 1) NewsAPI로 한국 시장 뉴스 시도
    if news_api_key:
        try:
            query = (
                "한국 경제 OR 국내 증시 OR 한국은행 OR 코스피 OR 코스닥 "
                "OR 국내 기업 OR 거시경제 OR 금리 OR 환율 OR CPI "
                "OR 인플레이션 OR GDP OR 통화정책 OR 재정정책"
            )
            api_url = (
                f"https://newsapi.org/v2/everything?"
                f"q={query}&language=ko&sortBy=publishedAt"
                f"&apiKey={news_api_key}&pageSize=10"
            )
            resp = requests.get(api_url, timeout=7)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "ok" and data.get("articles"):
                for art in data["articles"]:
                    raw_list.append({
                        "title": art.get("title", "제목 없음"),
                        "press": art.get("source", {}).get("name", "N/A"),
                        "date": art.get("publishedAt", "")[:10],
                        "url": art.get("url", "#"),
                    })
            else:
                raw_list = _get_news_from_naver_scraping()

        except Exception:
            traceback.print_exc()
            raw_list = _get_news_from_naver_scraping()
    else:
        # API 키 없으면 네이버 스크래핑으로
        raw_list = _get_news_from_naver_scraping()

    # 2) 본문(fetch) → 금융 키워드 필터 → 감성분석(제목만) & 기업명추출
    processed = []
    for item in raw_list:
        body = fetch_body(item["url"])

        # ← 이 부분 바로 아래에 금융 키워드 필터 삽입
        combined = (item["title"] + " " + item["press"] + " " + body).lower()
        if not any(kw in combined for kw in FINANCE_KEYWORDS):
            continue
        # → 여기까지 필터링 구간

        # —— 감성분석(제목만) & 기업명추출 —— 
        title_clean = clean_for_sentiment(item["title"])
        # 제목이 너무 짧거나 비어있을 경우 대비: 본문 일부로 백업
        if not title_clean.strip():
            backup_text = clean_for_sentiment(body)[:256]
            target_text = backup_text if backup_text else "내용없음"
        else:
            target_text = title_clean

        # sentiment_pipeline 로딩 확인 추가
        sentiment = "없음"
        if 'sentiment_pipeline' in globals() and sentiment_pipeline is not None:
            try:
                sentiment = sentiment_pipeline(target_text[:256])[0]["label"]
            except Exception as sentiment_e:
                print(f"DEBUG: 감성 분석 파이프라인 호출 오류: {sentiment_e}")
                sentiment = "오류"
        else:
            print("DEBUG: sentiment_pipeline이 로드되지 않았습니다.")


        companies = extract_companies(body)

        processed.append({
            "title":     item["title"],
            "press":     item["press"],
            "date":      item["date"],
            "url":       item["url"],
            "sentiment": sentiment,
            "companies": companies
        })

    return processed

def get_international_market_news():
    """해외 시장 뉴스를 가져옵니다 (NewsAPI.org - 미국 비즈니스 헤드라인)."""
    news_api_key = os.getenv("NEWS_API_KEY")
    if not news_api_key:
        print("DEBUG: NEWS_API_KEY 없음. 해외 뉴스 가져오기 건너뜜니다.")
        return [] # API 키 없으면 해외 뉴스는 가져오지 않음
    try:
        api_url = f"https://newsapi.org/v2/top-headlines?country=us&category=business&apiKey={news_api_key}&pageSize=10"
        print("DEBUG: NewsAPI.org로 해외 시장 뉴스 (미국 비즈니스) 검색 시도...")
        response = requests.get(api_url, timeout=7)
        response.raise_for_status()

        data = response.json()
        if data.get('status') == 'ok' and data.get('articles'):
            print(f"DEBUG: NewsAPI.org에서 해외 시장 뉴스 {len(data['articles'])}개 성공적으로 가져옴.")
            return [{'title': a.get('title', '제목 없음'), 'press': a.get('source', {}).get('name', 'N/A'), 'date': a.get('publishedAt', '')[:10], 'url': a.get('url', '#')} for a in data['articles']]
        else:
            print("DEBUG: NewsAPI.org 응답 상태 'ok' 아님 또는 기사 없음. 해외 뉴스 가져오기 실패.")
            return []
    except Exception as e:
        print(f"DEBUG: NewsAPI.org 오류 (해외 뉴스): {e}. 해외 뉴스 가져오기 실패.")
        traceback.print_exc()
        return []

def run_and_cache_quant_report():
    """서버 시작 시 퀀트 분석을 실행하고 결과를 반환하는 함수"""
    print("🚀 최초 퀀트 리포트 생성 및 캐싱 시작...")
    try:
        predictor = EnhancedStockPredictor(start_date='2015-01-01')
        
        predictor.collect_all_data()
        patterns = predictor.analyze_patterns()
        predictor.detect_anomalies()
        
        # calculate_economic_risks_detailed()가 내부적으로 risk_history를 계산함
        current_risks = predictor.calculate_economic_risks_detailed() 
        predictions = predictor.predict_weekly_enhanced()
        
        # --- 수정된 부분 시작 ---
        # DataFrame을 JSON 친화적인 형태로 변환
        if 'monthly' in patterns and isinstance(patterns.get('monthly'), pd.DataFrame):
            patterns['monthly'] = patterns['monthly'].reset_index().to_dict('records')
        if 'daily' in patterns and isinstance(patterns.get('daily'), pd.DataFrame):
            patterns['daily'] = patterns['daily'].reset_index().to_dict('records')
        
        # risk_history도 JSON으로 변환
        risk_history_data = None
        if hasattr(predictor, 'risk_history') and not predictor.risk_history.empty:
            df = predictor.risk_history.reset_index()
            df['index'] = df['index'].dt.strftime('%Y-%m-%d')
            risk_history_data = df.to_dict('records')

        # 주요 모니터링 지표 생성 로직 추가
        monitoring_indicators = []
        if current_risks['inflation']['risk'] > 40:
            monitoring_indicators.extend(["원자재 가격", "달러 인덱스", "장기 금리"])
        if current_risks['deflation']['risk'] > 40:
            monitoring_indicators.extend(["VIX 지수", "글로벌 주가", "경기선행지수"])
        if current_risks['stagflation']['risk'] > 30:
            monitoring_indicators.extend(["환율", "공급망 지표", "임금 상승률"])
        if not monitoring_indicators:
            monitoring_indicators.extend(["전반적 시장 동향", "기술적 지표", "거래량"])
        # 중복 제거 및 정렬
        monitoring_indicators = sorted(list(set(monitoring_indicators)))
        # --- 수정된 부분 끝 ---

        report_data = {
            "current_risks": current_risks,
            "future_risks": predictor.future_risks,
            "predictions": predictions,
            "patterns": patterns,
            "anomalies": predictor.anomalies,
            "overall_risk": current_risks.get('overall', 0),
            "risk_history": risk_history_data,
            "monitoring_indicators": monitoring_indicators # <-- 모니터링 지표 추가
        }
        print(" 최초 퀀트 리포트 캐싱 완료.")
        return report_data
    except Exception as e:
        print(f" 최초 퀀트 리포트 생성 실패: {e}")
        traceback.print_exc()
        return None


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
            df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
            df.set_index('Date', inplace=True)
            data[name] = calculate_change_info(df.copy(), name.upper())

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
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}

    latest_bday = get_latest_business_day()
    formatted_today_date = datetime.strptime(latest_bday, '%Y%m%d').strftime('%m월 %d일')

    if cache.get('date') != latest_bday:
        print(f"DEBUG: 캐시 업데이트 필요. 현재 영업일: {latest_bday}, 캐시된 날짜: {cache.get('date')}")
        new_cache = {'date': latest_bday}
        
        kospi_all_data = []
        kosdaq_all_data = []

        try:
            kospi_all_data, kosdaq_all_data = get_market_rank_data(latest_bday)
            
            current_key_stats_full = get_key_statistic_current_data()
            filtered_key_stats = [item for item in current_key_stats_full if item.get('DATA_VALUE') not in ['N/A', '', None]]
            important_key_stats = filtered_key_stats[:20]

            korean_news = get_general_market_news() # 한국 뉴스 담당 함수
            international_news = get_international_market_news()

            new_cache.update({
                'kospi_all_data': kospi_all_data,
                'kosdaq_all_data': kosdaq_all_data,
                'korean_market_news': korean_news,
                'international_market_news': international_news,
                'key_statistic_current_data': important_key_stats
            })
        except Exception as e:
            print(f"Error creating cache: {e}")
            traceback.print_exc()
            new_cache.update({
                'kospi_all_data': [],
                'kosdaq_all_data': [],
                'korean_market_news': [],
                'international_market_news': [],
                'key_statistic_current_data': []
            })
        cache = new_cache
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    else:
        print(f"DEBUG: 캐시 최신 상태 유지. 날짜: {latest_bday}")


    context = {
        'today': datetime.strptime(latest_bday, '%Y%m%d').strftime('%Y-%m-%d'),
        'formatted_today_date': formatted_today_date,
        **cache,
        'key_statistic_current_data': cache.get('key_statistic_current_data', []),
        'korean_market_news': cache.get('korean_market_news', []),
        'international_market_news': cache.get('international_market_news', [])
    }
    days_to_fetch = 60
    start_date, end_date = datetime.now() - timedelta(days=days_to_fetch), datetime.now()

    for ticker, name in [('KS11', 'kospi'), ('KQ11', 'kosdaq'), ('USD/KRW', 'usdkrw')]:
        try:
            df = get_fdr_or_yf_data(ticker, start_date, end_date)
            if not df.empty and 'Date' in df.columns:
                df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
                df.set_index('Date', inplace=True)
                context[f'{name}_info'] = calculate_change_info(df.copy(), name.upper())
                df_for_chart = df.reset_index().copy()
                df_for_chart['Date'] = pd.to_datetime(df_for_chart['Date']).dt.strftime('%Y-%m-%d')
                context[f'{name}_data'] = df_for_chart.tail(30).to_dict('records')
            else:
                context[f'{name}_data'], context[f'{name}_info'] = [], {'value': 'N/A'}
        except Exception as e:
            print(f"Error processing {name} data: {e}")
            context[f'{name}_data'], context[f'{name}_info'] = [], {'value': 'N/A'}


    try:
        wti_df = get_wti_data(days_to_fetch)
        if not wti_df.empty and 'Date' in wti_df.columns:
            wti_df['Close'] = pd.to_numeric(wti_df['Close'], errors='coerce')
            wti_df.set_index('Date', inplace=True)
            context['wti_info'] = calculate_change_info(wti_df.copy(), 'WTI')
            wti_df_for_chart = wti_df.reset_index().copy()
            wti_df_for_chart['Date'] = pd.to_datetime(wti_df_for_chart['Date']).dt.strftime('%Y-%m-%d')
            context['wti_data'] = wti_df_for_chart.tail(30).to_dict('records')
        else:
            context['wti_data'], context['wti_info'] = [], {'value': 'N/A'}
    except Exception as e:
        print(f"Error processing WTI data: {e}")
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
    app.config['QUANT_REPORT_CACHE'] = run_and_cache_quant_report()
    print("--- 모든 초기 데이터 로딩 완료 ---")

@app.context_processor
def inject_current_year():
    return {'current_year': datetime.utcnow().year}

@app.route('/api/chart_data/<string:ticker>/<string:interval>')
def get_chart_data(ticker, interval):
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

        raw_df = get_fdr_or_yf_data(ticker, start=start_date, end=end_date, interval=yf_interval) # start, end 인자 이름 명시
        if raw_df.empty:
            return jsonify({"error": "No data found for ticker"}), 404

        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)

        raw_df = raw_df.loc[:, ~raw_df.columns.duplicated()]

        date_col_name = next((col for col in ['Date', 'Datetime', 'index'] if col in raw_df.columns), None)
        close_col_name = next((col for col in ['Close', 'Adj Close', ticker] if col in raw_df.columns), None)

        if not date_col_name or not close_col_name:
            print(f"DEBUG: Could not find Date or Close column in {raw_df.columns}")
            return jsonify({"error": "Could not identify Date or Close column"}), 500

        clean_df = pd.DataFrame({
            'Date': raw_df[date_col_name],
            'Close': pd.to_numeric(raw_df[close_col_name], errors='coerce')
        })

        clean_df.dropna(inplace=True)
        if clean_df.empty:
            return jsonify({"error": "Data became empty after cleaning"}), 404

        clean_df['Date'] = pd.to_datetime(clean_df['Date']).dt.strftime('%Y-%m-%d')

        return jsonify(clean_df[['Date', 'Close']].to_dict('records'))

    except Exception as e:
        import traceback
        print(f"Error in get_chart_data for {ticker}/{interval}: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/stock-model')
def stock_model():
    return render_template('stock_model.html')


if __name__ == '__main__':
    app.run(debug=True, port=5000)

