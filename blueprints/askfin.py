import dart_fss as dart
import time

import os
import json
import traceback
import requests
import google.generativeai as genai
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import statistics
from flask import Blueprint, render_template, request, jsonify, session
from dotenv import load_dotenv
from datetime import datetime, timedelta
import concurrent.futures

from pykrx import stock
import re
from bs4 import BeautifulSoup

# --- Global Caches for Initial Loading ---
GLOBAL_KRX_LISTING = None
GLOBAL_TICKER_NAME_MAP = None # 종목 코드로 이름을 찾기 위한 맵
GLOBAL_NAME_TICKER_MAP = None # 종목 이름으로 코드를 찾기 위한 맵
ANALYSIS_CACHE = {} # 기존 분석 결과 캐시

# --- Environment Variable Loading and API Key Setup ---
load_dotenv()

askfin_bp = Blueprint('askfin', __name__, url_prefix='/askfin')

try:
    DART_API_KEY = os.getenv("DART_API_KEY")
    if not DART_API_KEY:
        raise ValueError("DART API 키가 .env 파일에 없습니다.")
    dart.set_api_key(api_key=DART_API_KEY)
    print("DART API 키가 성공적으로 설정되었습니다.")
except Exception as e:
    print(f"[경고] DART API 키 설정 실패: {e}")

try:
    API_KEY = os.getenv("GOOGLE_AI_API_KEY")
    if not API_KEY: raise ValueError("API 키가 없습니다.")
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

    PROMPT_TEMPLATE = """
You are a financial analyst. Your task is to analyze a user's query and convert it into a structured JSON object.
First, classify the query_type as "stock_analysis" or "indicator_lookup".

- "stock_analysis": For questions about stock performance under certain conditions.
- "indicator_lookup": For questions asking for a specific economic indicator's value.

- You MUST only respond with a JSON object. No other text.
- For a "condition" involving an indicator, use a condition object.

## JSON Schema:
{{"query_type": "stock_analysis|indicator_lookup", "period": "string|null", "condition": "string|object|null", "target": "string|null", "action": "string|null"}}

## Examples:
1. User Query: "지난 3년 동안 겨울에 오른 콘텐츠 관련 주식"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "지난 3년", "condition": "겨울", "target": "콘텐츠 관련주", "action": "오른 주식"}}
    
2. User Query: "최근 CPI 지수 알려줘"
    JSON Output:
    ```json
    {{"query_type": "indicator_lookup", "period": "최근", "condition": null, "target": "CPI 지수", "action": "조회"}}
    ```

3. 	User Query: "지난 3년 동안 겨울에 오른 콘텐츠 관련 주식을 보여줘"
    JSON Output:
    ```json
    {{"period": "지난 3년","condition": "겨울","target": "콘텐츠 관련주","action": "오른 주식"}}

    ```
    
4. User Query: "최근 CPI 지수가 3.5%보다 높았을 때 가장 많이 오른 주식은?"
    JSON Output:
    ```json
    {{"period": "최근", "condition": {{"type": "indicator", "name": "CPI", "operator": ">", "value": 3.5}}, "target": "주식", "action": "가장 많이 오른 주식"}}

    ```

5. User Query: "지난 1년간 2차전지주 중 가장 많이 내린 주식은?"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "지난 1년간", "condition": null, "target": "2차전지주", "action": "가장 많이 내린 주식"}}

    
## Task:
User Query: "{user_query}"
JSON Output:
"""
except Exception as e:
    print(f"AskFin Blueprint: 모델 초기화 실패 - {e}")
    model = None

# --- Initial Data Loading Function (Call this once at application startup) ---
def initialize_global_data():
    """
    서버 시작 시 한 번만 호출되어 전역으로 사용될 주식 기본 데이터를 로드하고 캐시합니다.
    """
    global GLOBAL_KRX_LISTING, GLOBAL_TICKER_NAME_MAP, GLOBAL_NAME_TICKER_MAP

    print("[애플리케이션 초기화] 필수 주식 데이터 로딩 시작...")
    try:
        # 1. KRX 전체 종목 목록 로딩 (FinanceDataReader)
        print("  - KOSPI 및 KOSDAQ 종목 목록 (FDR) 로딩 중...")
        GLOBAL_KRX_LISTING = fdr.StockListing('KRX')
        print(f"  - 종목 목록 로딩 완료. 총 {len(GLOBAL_KRX_LISTING)}개 종목.")

        # 2. 종목 코드 <-> 이름 매핑 로딩 (pykrx)
        print("  - 종목 코드/이름 매핑 (pykrx) 로딩 중...")
        all_tickers = stock.get_market_ticker_list(market="ALL")
        GLOBAL_TICKER_NAME_MAP = {ticker: stock.get_market_ticker_name(ticker) for ticker in all_tickers}
        GLOBAL_NAME_TICKER_MAP = {name: ticker for ticker, name in GLOBAL_TICKER_NAME_MAP.items()}
        print(f"  - 종목 코드/이름 매핑 생성 완료. 총 {len(GLOBAL_NAME_TICKER_MAP)}개 매핑.")

        print("[애플리케이션 초기화] 모든 필수 주식 데이터 로딩 완료.")

    except Exception as e:
        print(f"[초기화 오류] 필수 주식 데이터 로딩 실패: {e}")
        traceback.print_exc()

# --- Helper Functions (Updated to use global cached data) ---

def _load_ticker_maps():
    """
    종목 정보 맵을 전역 변수에서 가져오도록 변경.
    이 함수는 initialize_global_data()가 호출된 후에만 유효합니다.
    """
    global GLOBAL_TICKER_NAME_MAP, GLOBAL_NAME_TICKER_MAP
    if GLOBAL_NAME_TICKER_MAP is None:
        print("경고: _load_ticker_maps() 호출 시 글로벌 종목 맵이 초기화되지 않았습니다. 강제로 초기화 시도.")
        initialize_global_data()

def _get_fdr_indicator(indicator_info, intent_json):
    """FinanceDataReader를 통해 일별 지표를 조회하고 결과를 반환하는 헬퍼 함수"""
    try:
        name = indicator_info['name']
        code = indicator_info['code']
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=60)
        data = fdr.DataReader(code, start_date, end_date)
        
        if data.empty or len(data) < 2:
            return {"error": f"{name} 데이터 조회에 실패했습니다."}

        latest = data['Close'].iloc[-1]
        previous = data['Close'].iloc[-2]
        change = latest - previous
        change_str = f"{abs(change):.2f} 상승" if change > 0 else f"{abs(change):.2f} 하락" if change < 0 else "변동 없음"
        latest_date = data.index[-1].strftime('%Y년 %m월 %d일')
        
        result_sentence = f"가장 최근({latest_date}) {name}는(은) {latest:,.2f}이며, 전일 대비 {change_str}했습니다."
        
        return {
            "query_intent": intent_json,
            "analysis_subject": name,
            "result": [result_sentence]
        }
    except Exception as e:
        return {"error": f"{indicator_info.get('name', '알수없는')} 지표 조회 중 오류가 발생했습니다: {e}"}

def _get_bok_indicator(indicator_info, intent_json):
    """한국은행(BOK) API를 통해 월별 지표를 조회하고 결과를 반환하는 헬퍼 함수"""
    try:
        name = indicator_info['name']
        bok_api_key = os.getenv("ECOS_API_KEY")
        if not bok_api_key: return {"error": "한국은행 API 키가 설정되지 않았습니다."}

        end_date = datetime.now().strftime('%Y%m')
        start_date = (datetime.now() - timedelta(days=120)).strftime('%Y%m')
        url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{bok_api_key}/json/kr/1/10/"
               f"{indicator_info['stats_code']}/MM/{start_date}/{end_date}/{indicator_info['item_code']}")

        response = requests.get(url, timeout=10).json()
        rows = response.get("StatisticSearch", {}).get("row", [])
        
        if len(rows) < 2:
            return {"error": f"최근 {name} 데이터를 비교할 만큼 충분히 조회할 수 없습니다."}
            
        latest = rows[-1]
        previous = rows[-2]
        latest_date = f"{latest['TIME'][:4]}년 {latest['TIME'][4:]}월"
        change = float(latest['DATA_VALUE']) - float(previous['DATA_VALUE'])
        change_str = f"{abs(change):.2f} 상승" if change > 0 else f"{abs(change):.2f} 하락" if change < 0 else "변동 없음"

        result_sentence = (f"가장 최근({latest_date}) {name}는(은) {latest['DATA_VALUE']}이며, 전월 대비 {change_str}했습니다.")
        
        return {
            "query_intent": intent_json,
            "analysis_subject": name,
            "result": [result_sentence]
        }
    except Exception as e:
        return {"error": f"한국은행(BOK) 지표 조회 중 오류가 발생했습니다: {e}"}


def execute_indicator_lookup(intent_json):
    """
    [최종 수정] 여러 소스의 경제 지표를 조회하는 메인 함수
    """
    target = intent_json.get("target", "")

    # 데이터 소스 1: FinanceDataReader (일별 데이터)
    FDR_INDICATOR_MAP = {
        "환율": {"code": "USD/KRW", "name": "원/달러 환율"},
        "유가": {"code": "WTI", "name": "WTI 국제 유가"},
        "금값": {"code": "GC", "name": "금 선물"},
        "미국채10년": {"code": "US10YT", "name": "미 10년물 국채 금리"},
        "코스피": {"code": "KS11", "name": "코스피 지수"},
        "코스닥": {"code": "KQ11", "name": "코스닥 지수"},
    }
    
    for key, value in FDR_INDICATOR_MAP.items():
        if key in target or value['name'] in target:
            return _get_fdr_indicator(value, intent_json)
            
    # 데이터 소스 2: 한국은행 ECOS (월별 데이터)
    BOK_INDICATOR_MAP = {
        "CPI": {"stats_code": "901Y001", "item_code": "0", "name": "소비자물가지수"},
        "기준금리": {"stats_code": "722Y001", "item_code": "0001000", "name": "한국 기준금리"},
    }
    
    for key, value in BOK_INDICATOR_MAP.items():
        if key in target or value['name'] in target:
            return _get_bok_indicator(value, intent_json)

    # 어떤 맵에서도 찾지 못한 경우
    return {
        "query_intent": intent_json,
        "analysis_subject": "알 수 없는 지표",
        "result": [f"'{target}' 지표는 아직 지원하지 않습니다."]
    }

@askfin_bp.route('/stock/<code>/profile')
def get_stock_profile(code):
    """
    [수정] DART API를 사용하여 '주요 공시' 목록을 조회하도록 변경합니다.
    """
    response_data = {}
    company_name = None

    # --- 1. 기업 개요 정보 조회 (pykrx) ---
    try:
        profile_data = {}
        latest_business_day = stock.get_nearest_business_day_in_a_week()

        if GLOBAL_KRX_LISTING is None:
            initialize_global_data()
        
        krx_list = GLOBAL_KRX_LISTING
        target_info = krx_list[krx_list['Code'] == code]
        if target_info.empty:
            return jsonify({"error": f"종목코드 '{code}'를 찾을 수 없습니다."}), 404
        
        target_info = target_info.iloc[0]
        market = target_info.get('Market', 'KOSPI')
        sector = target_info.get('Sector')
        company_name = target_info.get('Name', 'N/A')
        
        profile_data['기업명'] = company_name
        profile_data['업종'] = sector
        profile_data['주요제품'] = target_info.get('Industry', 'N/A')

        df_ohlcv = stock.get_market_ohlcv(latest_business_day, market=market)
        df_cap = stock.get_market_cap(latest_business_day, market=market)
        df_funda = stock.get_market_fundamental(latest_business_day, market=market)

        current_price = df_ohlcv.loc[code, '종가']
        market_cap = df_cap.loc[code, '시가총액']
        funda = df_funda.loc[code]

        profile_data['현재가'] = f"{current_price:,} 원"
        profile_data['시가총액'] = f"{market_cap / 1_0000_0000_0000:.2f} 조원" if market_cap > 1_0000_0000_0000 else f"{market_cap / 1_0000_0000:.2f} 억원"
        
        eps = funda.get('EPS', 0)
        profile_data['PER'] = f"{funda.get('PER', 0):.2f} 배"
        profile_data['PBR'] = f"{funda.get('PBR', 0):.2f} 배"
        profile_data['배당수익률'] = f"{funda.get('DIV', 0):.2f} %"
        
        response_data["company_profile"] = profile_data

    except Exception as e:
        traceback.print_exc()
        response_data["profile_error"] = f"기업 개요 정보 처리 중 오류 발생: {str(e)}"

    # --- 2. DART 주요 공시 목록 조회 ---
    if company_name:
        try:
            corp_list = dart.get_corp_list()
            corp = corp_list.find_by_corp_name(company_name, exactly=True)[0] if corp_list.find_by_corp_name(company_name, exactly=True) else None
            
            if not corp:
                raise ValueError(f"DART에서 '{company_name}'을(를) 찾을 수 없습니다.")
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=365)
            reports = corp.search_filings(bgn_de=start_date.strftime('%Y%m%d'), end_de=end_date.strftime('%Y%m%d'), last_reprt_at='Y')
            
            if reports:
                report_list = []
                for r in reports[:15]: # 최근 15개 공시만 표시
                    report_list.append({
                        'report_nm': r.report_nm,
                        'flr_nm': r.flr_nm,
                        'rcept_dt': r.rcept_dt,
                        # [FIX] 'url' 속성 대신 rcept_no를 사용해 URL을 직접 구성합니다.
                        'url': f"http://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.rcept_no}"
                    })
                response_data["report_list"] = report_list
            else:
                response_data["report_list"] = []

        except Exception as e:
            traceback.print_exc()
            response_data["reports_error"] = f"공시 목록 조회 중 오류 발생: {str(e)}"
    
    return jsonify(response_data)
def get_target_stocks(target_str):
    """
    [수정됨] 타겟 문자열에 해당하는 종목 리스트(DataFrame)를 반환하는 함수 (캐시된 데이터 사용)
    """
    # 전역으로 캐시된 KRX 종목 리스트 사용
    if GLOBAL_KRX_LISTING is None:
        # 이 경우는 initialize_global_data가 호출되지 않았거나 실패한 경우.
        # 실제 운영 환경에서는 initialize_global_data가 먼저 호출되도록 보장해야 함.
        print("경고: get_target_stocks() 호출 시 GLOBAL_KRX_LISTING이 초기화되지 않았습니다. 강제로 초기화 시도.")
        initialize_global_data()
        if GLOBAL_KRX_LISTING is None:
            # 초기화 실패 시 빈 DataFrame 반환
            return pd.DataFrame(columns=['Name', 'Code']), "초기화 실패"

    krx = GLOBAL_KRX_LISTING
    
    GENERIC_TARGETS = {"주식", "종목", "급등주", "우량주", "인기주", "전체"}
    
    analysis_subject = "시장 전체"
    target_stocks = krx # 기본적으로 전체 KRX 리스트를 대상으로 시작

    if target_str and target_str.strip() and target_str not in GENERIC_TARGETS:
        analysis_subject = f"'{target_str}'"
        
        keyword = target_str.replace(" 관련주", "").replace(" 테마주", "").replace(" 테마", "").replace("주", "").strip()

        # Assuming THEME_MAP is defined elsewhere or loaded from a file
        # For demonstration, let's add a placeholder THEME_MAP if it's not present
        try:
            with open('themes.json', 'r', encoding='utf-8') as f:
                THEME_MAP = json.load(f)
        except FileNotFoundError:
            THEME_MAP = {
                "제약": ["삼성바이오로직스", "셀트리온", "한미약품", "유한양행", "녹십자", "종근당", "대웅제약", "GC녹십자", "SK바이오팜", "일양약품"],
                "콘텐츠": ["CJ ENM", "스튜디오드래곤", "에스엠", "JYP Ent.", "하이브", "YG엔터테인먼트", "콘텐트리중앙", "쇼박스", "NEW", "덱스터스튜디오"]
            }
            print("themes.json 파일을 찾을 수 없습니다. 기본 테마 맵을 사용합니다. (배포 시 themes.json 파일 사용을 권장합니다.)")
        
        if keyword in THEME_MAP:
            print(f"테마 '{keyword}'에 대한 종목을 검색합니다.")
            theme_stock_names = THEME_MAP[keyword]
            
            # 여기서 _load_ticker_maps() 대신 GLOBAL_NAME_TICKER_MAP 사용
            if GLOBAL_NAME_TICKER_MAP is None:
                _load_ticker_maps() # 비상시 로드 (원칙적으로는 initialize_global_data에서 로드되어야 함)
            
            target_codes = [GLOBAL_NAME_TICKER_MAP.get(name) for name in theme_stock_names if GLOBAL_NAME_TICKER_MAP.get(name)]
            target_stocks = krx[krx['Code'].isin(target_codes)]
        
        else:
            print(f"종목명에 '{keyword}' 키워드가 포함된 종목을 검색합니다.")
            target_stocks = krx[krx['Name'].str.contains(keyword, na=False)]
    
    elif target_str in GENERIC_TARGETS:
        analysis_subject = "시장 전체"
            
    return target_stocks, analysis_subject


def parse_period(period_str):
    """'지난 3년간' 같은 문자열을 시작일과 종료일로 변환하는 함수"""
    today = datetime.now()
    if not period_str:
        return today - timedelta(days=365), today
    try:
        if "년간" in period_str:
            years = int(period_str.replace("지난", "").replace("년간", "").strip())
            return today - timedelta(days=365 * years), today
        elif "개월" in period_str:
            months = int(period_str.replace("지난", "").replace("개월", "").strip())
            return today - timedelta(days=30 * months), today
        elif "작년" in period_str:
            last_year = today.year - 1
            return datetime(last_year, 1, 1), datetime(last_year, 12, 31)
        elif "올해" in period_str:
            return datetime(today.year, 1, 1), today
    except (ValueError, TypeError):
        pass

    return today - timedelta(days=365), today 


def get_interest_rate_hike_dates(api_key):
    """한국은행 API로 기준금리 인상일을 가져오는 함수."""
    stats_code, item_code = "722Y001", "0001000"
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=5*365)).strftime('%Y%m%d')
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/{stats_code}/DD/{start_date}/{end_date}/{item_code}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if "StatisticSearch" not in data or "row" not in data.get("StatisticSearch", {}):
            return []

        df = pd.DataFrame(data["StatisticSearch"]["row"])
        df['TIME'] = pd.to_datetime(df['TIME'], format='%Y%m%d')
        df['DATA_VALUE'] = pd.to_numeric(df['DATA_VALUE'])
        df = df.sort_values(by='TIME').reset_index(drop=True)
        df['PREV_VALUE'] = df['DATA_VALUE'].shift(1)
        
        hike_dates = df[df['DATA_VALUE'] > df['PREV_VALUE']]['TIME'].tolist()
        return hike_dates
    except Exception as e:
        print(f"한국은행 API 처리 오류: {e}")
        return []
    

def analyze_target_price_upside(target_stocks):
    """
    [최적화] 네이버 증권 컨센서스 페이지를 일괄 스크레이핑하여 목표주가 괴리율을 분석합니다.
    """
    print("목표주가 컨센서스 데이터 일괄 조회 시작...")
    try:
        url = "https://finance.naver.com/sise/consensus.naver?&target=up"
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        df_list = pd.read_html(requests.get(url, headers=headers, timeout=10).text)
        df = df_list[1]
        
        df = df.dropna(axis='index', how='all')
        df.columns = ['종목명', '목표주가', '투자의견', '현재가', '괴리율', '증권사', '작성일']
        df = df[df['종목명'].notna()]

        df['목표주가'] = pd.to_numeric(df['목표주가'], errors='coerce')
        df['현재가'] = pd.to_numeric(df['현재가'], errors='coerce')
        df['괴리율'] = df['괴리율'].str.strip('%').astype(float)
        df = df.dropna(subset=['목표주가', '현재가', '괴리율'])

        # 여기서도 GLOBAL_KRX_LISTING을 사용하여 중복 호출 방지
        if GLOBAL_KRX_LISTING is None:
            initialize_global_data()
        krx_list = GLOBAL_KRX_LISTING[['Name', 'Code']]
        df = pd.merge(df, krx_list, left_on='종목명', right_on='Name', how='inner')
        
        print("데이터 조회 및 가공 완료.")
        
        analysis_results = []
        for index, row in df.iterrows():
            analysis_results.append({
                "code": row['Code'],
                "name": row['종목명'],
                "value": row['괴리율'],
                "label": "목표주가 괴리율(%)",
                "start_price": int(row['현재가']),
                "end_price": int(row['목표주가'])
            })
            
        return analysis_results

    except Exception as e:
        print(f"목표주가 컨센서스 조회 중 오류 발생: {e}")
        return []
    
def execute_stock_analysis(intent_json, page, user_query, cache_key=None):
    """
    [최종 완성] 캐싱, 페이지네이션, 다중 분석, 폴백, 설명 기능을 모두 포함한 주식 분석 실행 함수.
    """
    try:
        action_str = intent_json.get("action", "")

        supported_actions = ["오른", "내린", "변동성", "변동", "목표주가"]
        # Removed the fallback block as per previous request

        if cache_key and cache_key in ANALYSIS_CACHE and 'full_result' in ANALYSIS_CACHE[cache_key]:
            sorted_result = ANALYSIS_CACHE[cache_key]['full_result']
            analysis_subject = ANALYSIS_CACHE[cache_key]['analysis_subject']
            print(f"✅ CACHE HIT: 캐시된 전체 결과 {len(sorted_result)}개를 사용합니다.")
        else:
            print(f"🔥 CACHE MISS: 새로운 분석을 시작합니다.")
            target_str = intent_json.get("target")
            condition_str = intent_json.get("condition")
            target_stocks, analysis_subject = get_target_stocks(target_str) # get_target_stocks는 이제 캐시된 GLOBAL_KRX_LISTING 사용
            if target_stocks.empty: return {"result": [f"{analysis_subject}에 해당하는 종목을 찾을 수 없습니다."]}

            start_date, end_date = parse_period(intent_json.get("period"))
            
            event_periods = []
            if isinstance(condition_str, str) and any(s in condition_str for s in ["여름", "겨울"]):
                season = "여름" if "여름" in condition_str else "겨울"
                event_periods = handle_season_condition((start_date, end_date), season)
            elif isinstance(condition_str, dict) and condition_str.get("type") == "indicator":
                # Handle indicator-based conditions, e.g., "CPI 지수가 3.5%보다 높았을 때"
                event_periods = handle_indicator_condition(condition_str, (start_date, end_date))
            else:
                event_periods = [(start_date, end_date)]

            result_data = []
            if "오른" in action_str or "내린" in action_str:
                result_data = analyze_top_performers(target_stocks, event_periods, (start_date, end_date))
            elif "변동성" in action_str or "변동" in action_str:
                result_data = analyze_volatility(target_stocks, (start_date, end_date))
            elif "목표주가" in action_str:
                result_data = analyze_target_price_upside(target_stocks)

            reverse_sort = False if "내린" in action_str else True
            sorted_result = sorted(result_data, key=lambda x: x.get('value', -999), reverse=reverse_sort)
            
            if not cache_key: cache_key = str(hash(json.dumps(intent_json, sort_keys=True)))
            ANALYSIS_CACHE[cache_key] = {
                'intent_json': intent_json, 'analysis_subject': analysis_subject, 'full_result': sorted_result
            }
            print(f"새로운 분석 결과 {len(sorted_result)}개를 캐시에 저장했습니다. (키: {cache_key})")

        items_per_page = 20
        total_items = len(sorted_result)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        paginated_result = sorted_result[start_index:end_index]
        
        condition_str = intent_json.get("condition")
        description = ""
        if isinstance(condition_str, str):
            if "여름" in condition_str:
                description = "여름(6월1일~8월 31일) 기간의 평균 수익률을 분석한 결과입니다. \n 현재 나오는 과거가격과 현재가격의 수익률이 아닙니다."
            elif "겨울" in condition_str:
                description = "겨울(12월1일~3월1) 기간의 평균 수익률을 분석한 결과입니다. \n 현재 나오는 과거가격과 현재가격의 수익률이 아닙니다."
        elif isinstance(condition_str, dict) and condition_str.get("type") == "indicator":
            description = f"{condition_str.get('name')} 지표가 {condition_str.get('value')}{condition_str.get('operator')} 조건 기간의 평균 수익률을 분석한 결과입니다."

        return {
            "query_intent": intent_json,
            "analysis_subject": analysis_subject,
            "description": description,
            "result": paginated_result,
            "pagination": { "current_page": page, "total_pages": total_pages, "total_items": total_items },
            "cache_key": cache_key
        }
    except Exception as e:
        traceback.print_exc()
        return {"error": f"분석 실행 중 오류 발생: {e}"}
    

def handle_season_condition(period_tuple, season):
    """'여름' 또는 '겨울' 조건에 맞는 날짜 구간 리스트를 반환하는 함수 (최적화)"""
    start_date, end_date = period_tuple
    event_periods = []
    
    for year in range(start_date.year, end_date.year + 1):
        if season == "여름":
            season_start = datetime(year, 6, 1)
            season_end = datetime(year, 8, 31)
            overlap_start = max(start_date, season_start)
            overlap_end = min(end_date, season_end)
            if overlap_start < overlap_end:
                event_periods.append((overlap_start, overlap_end))

        elif season == "겨울":
            # Winter can span across two years (Dec of year Y-1 to Feb/Mar of year Y)
            # So we check for both the current year's winter and the previous year's winter that ends in the current year.
            for y in [year - 1, year]: 
                season_start = datetime(y, 12, 1)
                season_end = datetime(y + 1, 3, 1) - timedelta(days=1) # End of Feb or March 1st minus 1 day
                overlap_start = max(start_date, season_start)
                overlap_end = min(end_date, season_end)
                if overlap_start < overlap_end:
                    event_periods.append((overlap_start, overlap_end))

    # Remove duplicates and sort the periods
    return sorted(list(set(event_periods)))

def handle_interest_rate_condition(api_key, period_tuple):
    """금리 인상 조건에 맞는 날짜 구간 리스트를 반환하는 함수"""
    start_date, end_date = period_tuple
    hike_dates = get_interest_rate_hike_dates(api_key)
    
    event_periods = []
    for hike_date in hike_dates:
        event_start = hike_date
        event_end = event_start + timedelta(days=7) # Consider a 7-day window after hike
        
        # Ensure the event period is within the overall query period
        if event_start >= start_date and event_end <= end_date:
            event_periods.append((event_start, event_end))
            
    return event_periods

# Helper function to fetch data for a single stock for parallel processing
def _fetch_and_analyze_single_stock(stock_code, stock_name, overall_start, overall_end, event_periods):
    """
    단일 종목의 전체 기간 데이터를 조회하고, 그 안에서 이벤트 기간 수익률을 분석하는 헬퍼 함수 (병렬 처리를 위함)
    """
    try:
        print(f"      데이터 조회 시작: {stock_name}({stock_code}) - {overall_start.strftime('%Y-%m-%d')} ~ {overall_end.strftime('%Y-%m-%d')}")

        overall_prices = fdr.DataReader(stock_code, overall_start, overall_end)
        print(f"      데이터 조회 완료: {stock_name}({stock_code})")

        if overall_prices.empty or len(overall_prices) < 2:
            return None 

        start_price = int(overall_prices['Open'].iloc[0])
        end_price = int(overall_prices['Close'].iloc[-1])

        period_returns = []
        for start, end in event_periods:
            prices_in_period = overall_prices.loc[start:end]
            
            if len(prices_in_period) > 1:
                event_start_price = prices_in_period['Open'].iloc[0]
                event_end_price = prices_in_period['Close'].iloc[-1]
                if event_start_price > 0:
                    period_returns.append((event_end_price / event_start_price) - 1)
        
        if period_returns:
            average_return = statistics.mean(period_returns)
            if pd.notna(average_return):
                return {
                    "code": stock_code, "name": stock_name,
                    "value": round(average_return * 100, 2), "label": "평균 수익률(%)",
                    "start_price": start_price, # 전체 기간 시작 가격
                    "end_price": end_price, # 전체 기간 종료 가격
                }
    except Exception as e:
        print(f" - {stock_name}({stock_code}) 분석 중 오류 발생: {e}")
    return None # 오류 발생 시 또는 유효한 수익률이 없으면 None 반환

def analyze_top_performers(target_stocks, event_periods, overall_period):
    """
    [성능 최적화] 전체 기간 데이터를 한 번에 조회 후, 메모리에서 조건 기간을 슬라이싱하여 분석 속도를 개선합니다.
    또한, 여러 종목의 데이터 조회를 병렬로 처리합니다.
    """
    analysis_results = []
    top_stocks = target_stocks.nlargest(min(len(target_stocks), 50), 'Marcap').reset_index(drop=True)
    print(f"시가총액 상위 {len(top_stocks)}개 종목에 대한 수익률 분석을 시작합니다...")
    overall_start, overall_end = overall_period

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor: # 20에서 30으로 증가
        future_to_stock = {
            executor.submit(_fetch_and_analyze_single_stock, stock['Code'], stock['Name'], overall_start, overall_end, event_periods): stock
            for index, stock in top_stocks.iterrows()
        }

        for i, future in enumerate(concurrent.futures.as_completed(future_to_stock)):
            stock_info = future_to_stock[future]
            stock_code, stock_name = stock_info['Code'], stock_info['Name']
            try:
                result = future.result()
                if result:
                    analysis_results.append(result)
                print(f"   ({i + 1}/{len(top_stocks)}) {stock_name}({stock_code}) 분석 완료.")
            except Exception as exc:
                print(f"   - {stock_name}({stock_code}) 분석 중 예외 발생: {exc}")
    
    return analysis_results

def analyze_volatility(target_stocks, period_tuple):
    """변동성 분석 함수 (딜레이 제거)"""
    analysis_results = []
    start_date, end_date = period_tuple
    top_stocks = target_stocks.nlargest(min(len(target_stocks), 50), 'Marcap').reset_index(drop=True)
    print(f"시가총액 상위 {len(top_stocks)}개 종목에 대한 변동성 분석을 시작합니다...")

    # ThreadPoolExecutor를 사용하여 변동성 분석을 위한 데이터 조회를 병렬로 처리
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor: # 20에서 30으로 증가
        future_to_stock = {
            executor.submit(
                lambda code, name, s_date, e_date: _fetch_and_calculate_volatility(code, name, s_date, e_date),
                stock_info['Code'], stock_info['Name'], start_date, end_date
            ): stock_info
            for index, stock_info in top_stocks.iterrows()
        }

        for i, future in enumerate(concurrent.futures.as_completed(future_to_stock)):
            stock_info = future_to_stock[future]
            code, name = stock_info['Code'], stock_info['Name']
            try:
                result = future.result()
                if result:
                    analysis_results.append(result)
                print(f"   ({i + 1}/{len(top_stocks)}) {name}({code}) 변동성 분석 완료.")
            except Exception as exc:
                print(f"   - {name}({code}) 변동성 분석 중 예외 발생: {exc}")
    
    return analysis_results

def _fetch_and_calculate_volatility(code, name, start_date, end_date):
    """
    단일 종목의 데이터를 조회하고 변동성을 계산하는 헬퍼 함수 (병렬 처리를 위함)
    """
    try:
        print(f"      변동성 데이터 조회 시작: {name}({code}) - {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
        overall_prices = fdr.DataReader(code, start_date, end_date)
        print(f"      변동성 데이터 조회 완료: {name}({code})")

        if overall_prices.empty:
            return None
        
        daily_returns = overall_prices['Close'].pct_change().dropna()
        volatility = daily_returns.std()
        if pd.notna(volatility):
            return {
                "code": code, "name": name,
                "value": round(volatility * 100, 2), "label": "변동성(%)",
                "start_price": int(overall_prices['Open'].iloc[0]),
                "end_price": int(overall_prices['Close'].iloc[-1])
            }
    except Exception as e:
        print(f"   - {name}({code}) 변동성 분석 중 오류 발생: {e}")
    return None

def handle_indicator_condition(condition_obj, period_tuple):
    """CPI, 금리 등 지표 조건을 만족하는 날짜 구간을 반환"""
    bok_api_key = os.getenv("ECOS_API_KEY")
    if not bok_api_key: return []
    INDICATOR_MAP = {
        "CPI": {"stats_code": "901Y001", "item_code": "0"},
        "기준금리": {"stats_code": "722Y001", "item_code": "0001000"},
    }

    indicator_name = condition_obj.get("name")
    if indicator_name not in INDICATOR_MAP: return []

    indicator_info = INDICATOR_MAP[indicator_name]
    data_series = get_bok_data(bok_api_key, indicator_info['stats_code'], indicator_info['item_code'], period_tuple[0], period_tuple[1])

    if data_series is None: return []

    op_str = condition_obj.get("operator")
    value = condition_obj.get("value")

    if op_str == '>': matching_series = data_series[data_series > value]
    elif op_str == '>=': matching_series = data_series[data_series >= value]
    else: return []

    return [(d.replace(day=1), (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)) for d in matching_series.index]

def get_bok_data(bok_api_key, stats_code, item_code, start_date, end_date):
    """
    한국은행 ECOS API를 통해 특정 지표의 시계열 데이터를 가져와 Pandas Series로 반환.
    """
    start_str = start_date.strftime('%Y%m')
    end_str = end_date.strftime('%Y%m')
    
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{bok_api_key}/json/kr/1/1000/{stats_code}/MM/{start_str}/{end_str}/{item_code}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # HTTP 오류 발생 시 예외 발생
        data = response.json()

        if "StatisticSearch" not in data or "row" not in data.get("StatisticSearch", {}):
            print("BOK API 응답에 데이터가 없습니다.")
            return None

        rows = data["StatisticSearch"]["row"]
        if not rows:
            return None

        df = pd.DataFrame(rows)
        df['TIME'] = pd.to_datetime(df['TIME'], format='%Y%m') 
        df['DATA_VALUE'] = pd.to_numeric(df['DATA_VALUE']) 
        df = df.set_index('TIME')                             
        
        return df['DATA_VALUE'].sort_index()

    except requests.exceptions.RequestException as e:
        print(f"한국은행 API 요청 오류: {e}")
        return None
    except Exception as e:
        print(f"데이터 처리 중 오류: {e}")
        return None
    
@askfin_bp.route('/')
def askfin_page():
    return render_template('askfin.html')

@askfin_bp.route('/analyze', methods=['POST'])
def analyze_query():
    """
    [수정됨] JSON 분석 실패 시, 일반 대화형 AI 답변으로 폴백하는 기능이 추가된 API.
    """
    if not model:
        return jsonify({"error": "모델이 초기화되지 않았습니다. API 키를 확인하세요."}), 500
    
    data = request.get_json()
    user_query = data.get('query')
    page = data.get('page', 1)
    cache_key = data.get('cache_key')

    if not user_query:
        return jsonify({"error": "잘못된 요청입니다."}), 400

    try:
        intent_json = None
        if cache_key and cache_key in ANALYSIS_CACHE:
            print(f"CACHE HIT: 캐시 키 '{cache_key}'를 사용합니다.")
            intent_json = ANALYSIS_CACHE[cache_key]['intent_json']
        else:
            print(f"1차 시도: '{user_query}'에 대해 JSON 분석을 요청합니다.")
            prompt = PROMPT_TEMPLATE.format(user_query=user_query)
            response = model.generate_content(prompt)
            raw_text = response.text

            try:
                # AI 응답에서 JSON 부분만 정확히 추출
                start = raw_text.find('{')
                end = raw_text.rfind('}') + 1
                cleaned_response = raw_text[start:end]
                intent_json = json.loads(cleaned_response)
                
                # 새로운 캐시 키 생성 및 결과 저장 준비
                new_cache_key = str(hash(json.dumps(intent_json, sort_keys=True)))
                ANALYSIS_CACHE[new_cache_key] = { 'intent_json': intent_json }
                cache_key = new_cache_key 
            
            except (json.JSONDecodeError, IndexError) as e:
                # --- [수정된 부분 시작] ---
                # JSON 분석 실패 시, 일반 대화형 답변으로 폴백
                print(f" JSON 분석 실패({e}). 일반 대화형 모델로 폴백합니다.")
                try:
                    # 금융 분석 프롬프트가 아닌 일반적인 프롬프트로 모델 재호출
                    general_prompt = f"다음 질문에 대해 친절하고 상세하게 답변해줘: {user_query}"
                    fallback_response = model.generate_content(general_prompt)
                    
                    # 프론트엔드가 처리할 수 있는 형태로 답변 포맷팅
                    final_result = {
                        "analysis_subject": "일반 답변",
                        "result": [fallback_response.text.replace('\n', '<br>')]
                    }
                    return jsonify(final_result)
                
                except Exception as fallback_e:
                    # 폴백 모델 호출조차 실패한 경우, 최종 오류 반환
                    print(f" 폴백 모델 호출 실패: {fallback_e}")
                    traceback.print_exc()
                    return jsonify({"error": "질문을 분석하는데 실패했고, 일반 답변도 가져올 수 없었습니다."}), 500
                # --- [수정된 부분 끝] ---

        if not intent_json:
            return jsonify({"error": "AI가 유효한 분석 결과를 반환하지 못했습니다."}), 500

        query_type = intent_json.get("query_type")
        
        if query_type == "stock_analysis":
            final_result = execute_stock_analysis(intent_json, page, user_query, cache_key)
        elif query_type == "indicator_lookup":
            final_result = execute_indicator_lookup(intent_json)
        elif query_type == "greeting":
            final_result = {
                "analysis_subject": "인사",
                "result": ["안녕하세요! 금융에 대해 무엇이든 물어보세요."]
            }
        else:
            print(f" 알 수 없는 질문 유형({query_type}). 일반 대화형 모델로 폴백합니다.")
            general_prompt = f"다음 질문에 대해 친절하게 답변해줘: {user_query}"
            fallback_response = model.generate_content(general_prompt)
            final_result = {
                "analysis_subject": "일반 답변",
                "result": [fallback_response.text.replace('\n', '<br>')]
            }
            
        return jsonify(final_result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"분석 중 오류 발생: {str(e)}"}), 500
    
@askfin_bp.route('/new_chat', methods=['POST'])
def new_chat():
    """대화 기록(세션)을 초기화합니다."""
    session.pop('chat_history', None)
    return jsonify({"status": "success", "message": "새 대화를 시작합니다."})