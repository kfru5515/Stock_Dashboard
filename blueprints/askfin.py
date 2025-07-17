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
GLOBAL_TICKER_NAME_MAP = None 
GLOBAL_NAME_TICKER_MAP = None
ANALYSIS_CACHE = {} 
GLOBAL_SECTOR_MASTER_DF = None 
GLOBAL_STOCK_SECTOR_MAP = None 

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

- "stock_analysis": For questions about stock performance under certain conditions (e.g., "most risen", "most fallen", "volatility").
- "indicator_lookup": For questions asking for a specific economic indicator's value (e.g., "CPI", "interest rate", "oil price").
- "general_inquiry": For questions asking for general financial advice, trends, recommendations, or information not directly about specific stock analysis or indicator lookup. This includes questions about market sentiment, international affairs, or general advice.

- You MUST only respond with a JSON object. No other text.
- For a "condition" involving an indicator, use a condition object.


## JSON Schema:
{{"query_type": "stock_analysis|indicator_lookup", "period": "string|null", "condition": "string|object|null", "target": "string|null", "action": "string|null"}}

## Examples:
1. User Query: "지난 3년 동안 겨울에 오른 콘텐츠 관련 주식"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "지난 3년", "condition": "겨울", "target": "콘텐츠 관련주", "action": "오른 주식"}}
    ```
2. User Query: "최근 CPI 지수 알려줘"
    JSON Output:
    ```json
    {{"query_type": "indicator_lookup", "period": "최근", "condition": null, "target": "CPI 지수", "action": "조회"}}
    ```
3. User Query: "지난 3년 동안 겨울에 오른 콘텐츠 관련 주식을 보여줘"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "지난 3년", "condition": "겨울", "target": "콘텐츠 관련주", "action": "오른 주식"}}
    ```
4. User Query: "최근 CPI 지수가 3.5%보다 높았을 때 가장 많이 오른 주식은?"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "최근", "condition": {{"type": "indicator", "name": "CPI", "operator": ">", "value": 3.5}}, "target": "주식", "action": "가장 많이 오른 주식"}}
    ```
5. User Query: "지난 1년간 2차전지주 중 가장 많이 내린 주식은?"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "지난 1년간", "condition": null, "target": "2차전지주", "action": "가장 많이 내린 주식"}}
    ```
6. User Query: "최근 유행하는 테마주 추천해줄래?"
    JSON Output:
    ```json
    {{"query_type": "general_inquiry", "period": "최근", "target": "테마주", "action": "추천", "recommendation_type": "유행"}}
    ```
7. User Query: "요즘 국제 정세를 알려줄래?"
    JSON Output:
    ```json
    {{"query_type": "general_inquiry", "period": "최근", "target": "국제 정세", "action": "정보 제공"}}
    ```
8. User Query: "주식 초보인데 어떤 종목이 좋아?"
    JSON Output:
    ```json
    {{"query_type": "general_inquiry", "target": "주식", "action": "추천", "recommendation_type": "초보"}}
    ```
9. User Query: "CPI지수는"
    JSON Output:
    ```json
    {{"query_type": "indicator_lookup", "period": "최근", "condition": null, "target": "CPI 지수", "action": "조회"}}
    ```
10. User Query: "환율"
    JSON Output:
    ```json
    {{"query_type": "indicator_lookup", "period": "최근", "condition": null, "target": "환율", "action": "조회"}}
    ```
11. User Query: "기준금리 얼마"
    JSON Output:
    ```json
    {{"query_type": "indicator_lookup", "period": "최근", "condition": null, "target": "기준금리", "action": "조회"}}
    ```

## Task:
User Query: "{user_query}"
JSON Output:
"""
except Exception as e:
    print(f"AskFin Blueprint: 모델 초기화 실패 - {e}")
    model = None

# --- Initial Data Loading Function ---
def initialize_global_data():
    """
    서버 시작 시 한 번만 호출되어 전역으로 사용될 주식 기본 데이터를 로드하고 캐시합니다.
    """
    global GLOBAL_KRX_LISTING, GLOBAL_TICKER_NAME_MAP, GLOBAL_NAME_TICKER_MAP

    print("[애플리케이션 초기화] 필수 주식 데이터 로딩 시작...")
    try:
        print("  - KOSPI 및 KOSDAQ 종목 목록 (FDR) 로딩 중...")
        GLOBAL_KRX_LISTING = fdr.StockListing('KRX')
        print(f"  - 종목 목록 로딩 완료. 총 {len(GLOBAL_KRX_LISTING)}개 종목.")
        print(f"  - GLOBAL_KRX_LISTING 컬럼: {GLOBAL_KRX_LISTING.columns.tolist()}")

        if 'Industry' in GLOBAL_KRX_LISTING.columns:
            print("\n  - FinanceDataReader 'Industry' 컬럼 고유값 (상위 20개):")
            for industry in GLOBAL_KRX_LISTING['Industry'].dropna().unique().tolist()[:20]:
                print(f"    - {industry}")
            if len(GLOBAL_KRX_LISTING['Industry'].dropna().unique()) > 20:
                print("    ... (더 많은 업종이 있습니다)")
        else:
            print("  - GLOBAL_KRX_LISTING에 'Industry' 컬럼이 없습니다.")

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


        period_str = intent_json.get("period")
        req_start_date, req_end_date = parse_period(period_str)
        query_start_date = req_end_date - timedelta(days=90) 
        query_end_date = req_end_date 
        
        data = fdr.DataReader(code, query_start_date, query_end_date) 
        
        if data.empty:
            return {"error": f"{name} 데이터 조회에 실패했습니다."}

        target_data_in_period = data.loc[data.index <= req_end_date].sort_index(ascending=True)

        if target_data_in_period.empty:
             return {"error": f"{name} 지표에 대해 요청하신 기간({req_end_date.strftime('%Y년 %m월 %d일')})의 데이터를 찾을 수 없습니다."}

        latest_for_period = target_data_in_period['Close'].iloc[-1]
        latest_date_for_period = target_data_in_period.index[-1]

        previous_data_in_period = data.loc[data.index < latest_date_for_period].sort_index(ascending=True)
        previous_for_period = None
        if not previous_data_in_period.empty:
            previous_for_period = previous_data_in_period['Close'].iloc[-1]

        if previous_for_period is not None:
            change = latest_for_period - previous_for_period
            change_str = f"{abs(change):.2f} 상승" if change > 0 else f"{abs(change):.2ff} 하락" if change < 0 else "변동 없음"
            result_sentence = f"요청하신 기간의 마지막({latest_date_for_period.strftime('%Y년 %m월 %d일')}) {name}는(은) {latest_for_period:,.2f}이며, 직전 영업일 대비 {change_str}했습니다."
        else:
            result_sentence = f"요청하신 기간의 마지막({latest_date_for_period.strftime('%Y년 %m월 %d일')}) {name}는(은) {latest_for_period:,.2f}입니다. 직전 영업일 데이터가 없어 변동 정보를 제공할 수 없습니다."

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
            
    BOK_INDICATOR_MAP = {
        "CPI": {"stats_code": "901Y001", "item_code": "0", "name": "소비자물가지수"},
        "기준금리": {"stats_code": "722Y001", "item_code": "0001000", "name": "한국 기준금리"},
    }
    
    for key, value in BOK_INDICATOR_MAP.items():
        if key in target or value['name'] in target:
            return _get_bok_indicator(value, intent_json)

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


    if company_name:
        try:
            corp_list = dart.get_corp_list()
            
            # --- START OF MODIFICATION ---
            # Search for the corporation by name
            found_corps = corp_list.find_by_corp_name(company_name, exactly=True)
            
            corp = None
            if found_corps: # Check if the list is not empty
                corp = found_corps[0] # Assign the first found corporation
            
            if not corp: # Explicitly check if corp is None after assignment
                raise ValueError(f"DART에서 '{company_name}'을(를) 찾을 수 없습니다.")
            # --- END OF MODIFICATION ---

            reports = corp.search_filings(bgn_de=(datetime.now() - timedelta(days=365)).strftime('%Y%m%d'), last_reprt_at='Y')
            response_data["report_list"] = [{
                'report_nm': r.report_nm, 'flr_nm': r.flr_nm, 'rcept_dt': r.rcept_dt,
                'url': f"http://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.rcept_no}"
            } for r in reports[:15]] if reports else []

            api_url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
            fs_data_list = [] 
            
            current_year = datetime.now().year
            years_to_fetch = [str(year) for year in range(current_year, current_year - 4, -1)]

            for year in years_to_fetch:
                for reprt_code in ['11011', '11013', '11012']:
                    params = {
                        'crtfc_key': DART_API_KEY,
                        'corp_code': corp.corp_code, # 'corp' is now guaranteed to be defined here
                        'bsns_year': str(year),
                        'reprt_code': reprt_code,
                        'fs_div': 'CFS'
                    }
                    res = requests.get(api_url, params=params)
                    data = res.json()

                    if data.get('status') == '000' and data.get('list'):
                        for item in data['list']:
                            if 'thstrm_end_dt' in item and item['thstrm_end_dt']:
                                item['report_date_key'] = item['thstrm_end_dt']
                            elif 'thstrm_dt' in item and item['thstrm_dt']:
                                date_range_str = item['thstrm_dt']
                                match = re.search(r'(\d{4}\.\d{2}\.\d{2})$', date_range_str.strip())
                                if match:
                                    item['report_date_key'] = match.group(1)
                                else:
                                    item['report_date_key'] = date_range_str.strip()
                            else:
                                item['report_date_key'] = None
                        fs_data_list.extend(data['list'])
                        break
                    elif params['fs_div'] == 'CFS':
                        params['fs_div'] = 'OFS'
                        res = requests.get(api_url, params=params)
                        data = res.json()
                        if data.get('status') == '000' and data.get('list'):
                             for item in data['list']:
                                if 'thstrm_end_dt' in item and item['thstrm_end_dt']:
                                    item['report_date_key'] = item['thstrm_end_dt']
                                elif 'thstrm_dt' in item and item['thstrm_dt']:
                                    date_range_str = item['thstrm_dt']
                                    match = re.search(r'(\d{4}\.\d{2}\.\d{2})$', date_range_str.strip())
                                    if match:
                                        item['report_date_key'] = match.group(1)
                                    else:
                                        item['report_date_key'] = date_range_str.strip()
                                else:
                                    item['report_date_key'] = None
                             fs_data_list.extend(data['list'])
                             break

            if fs_data_list:
                df = pd.DataFrame(fs_data_list)
                
                df = df.drop_duplicates(subset=['rcept_no', 'account_nm'], keep='last')
                
                df['thstrm_amount'] = df['thstrm_amount'].astype(str).str.replace(',', '', regex=False).str.strip()
                df['thstrm_amount'] = pd.to_numeric(df['thstrm_amount'], errors='coerce')
                df.dropna(subset=['thstrm_amount'], inplace=True)
                
                def extract_and_clean_date(date_string): # This helper function is now defined here again or needs to be outside if used multiple places
                    if not isinstance(date_string, str): # Add this check for safety
                        return None
                    match = re.search(r'(\d{4}\.\d{2}\.\d{2})$', date_string.strip())
                    if match:
                        return match.group(1)
                    parts = date_string.split('~')
                    if len(parts) > 1:
                        match = re.search(r'(\d{4}\.\d{2}\.\d{2})$', parts[-1].strip())
                        if match:
                            return match.group(1)
                    return None
                
                # Use the report_date_key if it was set, otherwise apply the cleaning to thstrm_dt
                if 'report_date_key' not in df.columns: # Fallback if direct assignment didn't happen for all
                    df['report_date_key'] = df['thstrm_dt'].astype(str).apply(extract_and_clean_date)


                df['report_date_key'] = pd.to_datetime(df['report_date_key'], format='%Y.%m.%d', errors='coerce')
                df.dropna(subset=['report_date_key'], inplace=True)
                
                df_pivot = df.pivot_table(index='account_nm', columns='report_date_key', values='thstrm_amount', aggfunc='first')
                df_pivot = df_pivot.fillna(0)

                df_pivot = df_pivot.sort_index(axis=1) 
                
                display_columns = df_pivot.columns[-4:] if len(df_pivot.columns) >= 4 else df_pivot.columns
                
                response_data["key_financial_info"] = json.dumps(
                    {
                        "columns": [col.strftime('%Y.%m') for col in display_columns],
                        "index": list(df_pivot.index),
                        "data": df_pivot[display_columns].values.tolist()
                    }, ensure_ascii=False
                )
                print("2024년도 주요 재무정보 API 호출 성공 및 처리 완료")
            else:
                response_data["financials_error"] = "주요 재무정보를 가져올 수 없습니다. DART API에서 데이터를 찾을 수 없거나 파싱 오류."

        except Exception as e:
            print(f"재무제표 데이터 처리 중 치명적인 오류 발생: {e}")
            traceback.print_exc()
            response_data["financials_error"] = f"재무제표를 불러오는 데 실패했습니다: {e}"
    
    return jsonify(response_data)

def get_target_stocks(target_str):
    """
    [수정됨] 타겟 문자열에 해당하는 종목 리스트(DataFrame)를 반환하는 함수 (캐시된 데이터 사용)
    """
    global GLOBAL_KRX_LISTING, GLOBAL_NAME_TICKER_MAP

    if GLOBAL_KRX_LISTING is None:
        print("경고: get_target_stocks() 호출 시 GLOBAL_KRX_LISTING이 초기화되지 않았습니다. 강제로 초기화 시도.")
        initialize_global_data()
        if GLOBAL_KRX_LISTING is None:
            return pd.DataFrame(columns=['Name', 'Code']), "초기화 실패"

    krx = GLOBAL_KRX_LISTING 

    GENERIC_TARGETS = {"주식", "종목", "급등주", "우량주", "인기주", "전체"}
    
    analysis_subject = "시장 전체"
    target_stocks = krx 

    import os 

    if target_str and target_str.strip() and target_str not in GENERIC_TARGETS:
        analysis_subject = f"'{target_str}'"
        
        keyword = target_str.replace(" 관련주", "").replace(" 테마주", "").replace(" 테마", "").replace("주", "").strip()
        lower_keyword = keyword.lower()

        print(f"--- 디버그 시작 (get_target_stocks) ---")
        print(f"디버그: 사용자 입력 키워드: '{keyword}' (소문자: '{lower_keyword}')")

        themes_from_file = {}
        try:
            themes_file_path = os.path.join(os.path.dirname(__file__), '..', 'cache', 'themes.json')

            print(f"디버그: themes.json을 찾을 경로: {themes_file_path}") # 디버그 출력 추가

            with open(themes_file_path, 'r', encoding='utf-8') as f:
                themes_from_file = json.load(f)
            print(f"디버그: 'themes.json' 파일 로드 성공. 총 {len(themes_from_file)}개 테마.")
            print(f"디버그: themes.json 키 목록 (상위 5개): {list(themes_from_file.keys())[:5]}...")
        except FileNotFoundError:
            print("경고: 'themes.json' 파일을 찾을 수 없습니다. 경로를 확인해주세요.")
        except Exception as e:
            print(f"경고: 'themes.json' 파일 로드 중 오류 발생: {e}")

        found_by_theme_file = False
        target_codes_from_theme = []

        for theme_name_in_file, stock_list_in_file in themes_from_file.items():
            print(f"디버그: '{lower_keyword}' vs '{theme_name_in_file.lower()}' 매칭 시도...")
            
            if (lower_keyword == theme_name_in_file.lower() or 
                lower_keyword in theme_name_in_file.lower() or 
                theme_name_in_file.lower() in lower_keyword): 
                
                print(f"디버그: themes.json에서 테마 '{theme_name_in_file}'를 찾았습니다.")
                for stock_info in stock_list_in_file:
                    if isinstance(stock_info, dict) and 'code' in stock_info:
                        target_codes_from_theme.append(stock_info['code'])
                    elif isinstance(stock_info, str) and len(stock_info) == 6 and stock_info.isdigit(): # 코드가 문자열로 직접 저장된 경우
                        target_codes_from_theme.append(stock_info)
                analysis_subject = f"'{theme_name_in_file}' 테마"
                found_by_theme_file = True
                break
        
        print(f"디버그: themes.json에서 추출된 종목 코드 수: {len(target_codes_from_theme)}")
        if len(target_codes_from_theme) > 0:
            print(f"디버그: 추출된 첫 5개 종목 코드: {target_codes_from_theme[:5]}")

        if found_by_theme_file:

            print(f"디버그: GLOBAL_KRX_LISTING의 첫 5개 행:\n{krx.head()}")

            codes_in_krx_check = krx[krx['Code'].isin(target_codes_from_theme)]
            print(f"디버그: GLOBAL_KRX_LISTING에 존재하는 테마 종목 코드 수: {len(codes_in_krx_check)}")
            if len(codes_in_krx_check) == 0 and len(target_codes_from_theme) > 0:
                print("디버그: 경고! themes.json의 종목 코드 중 GLOBAL_KRX_LISTING에 매칭되는 것이 없습니다. 코드 형식 불일치 가능성.")
                if target_codes_from_theme:
                    print(f"디버그: themes.json 첫 종목 코드: '{target_codes_from_theme[0]}'")
                if not krx.empty:
                    print(f"디버그: GLOBAL_KRX_LISTING 첫 종목 코드: '{krx.iloc[0]['Code']}'")


            target_stocks = krx[krx['Code'].isin(target_codes_from_theme)]
            print(f"디버그: 최종 필터링된 target_stocks 개수: {len(target_stocks)}")
        
        else:
            # 2. 기존 FinanceDataReader 'Industry' 컬럼 (혹시 존재한다면)을 통한 검색 (테마 파일 없을 때의 폴백)
            # 현재 로그에 'Industry' 컬럼이 없다고 나왔지만, 미래에 추가될 가능성을 고려하여 로직은 유지하되,
            # 실제 컬럼이 없으면 건너뛰도록 조건문 추가
            found_by_industry = False
            if 'Industry' in krx.columns:
                INDUSTRY_KEYWORD_MAP = {
                    "제약": ["의약품 제조업", "의료용 물질 및 의약품 제조업", "생물학적 제제 제조업"], 
                    "반도체": ["반도체 제조업", "전자부품 제조업", "반도체 및 평판디스플레이 제조업"],
                    "자동차": ["자동차용 엔진 및 자동차 제조업", "자동차 부품 제조업"],
                    "IT": ["소프트웨어 개발 및 공급업", "컴퓨터 프로그래밍, 시스템 통합 및 관리업", "정보서비스업"],
                    "은행": ["은행"],
                    "증권": ["증권 및 선물 중개업"],
                    "보험": ["보험 및 연금업"],
                    "건설": ["종합 건설업", "건물 건설업", "토목 건설업"],
                    "화학": ["화학물질 및 화학제품 제조업", "고무 및 플라스틱제품 제조업"],
                    "콘텐츠": ["영화, 비디오물, 방송프로그램 제작 및 배급업", "음악 및 기타 엔터테인먼트업", "출판업"], 
                    "게임": ["게임 소프트웨어 개발 및 공급업", "데이터베이스 및 온라인 정보 제공업"],
                    "철강": ["1차 철강 제조업", "금속 가공제품 제조업"],
                    "조선": ["선박 및 보트 건조업"],
                    "해운": ["해상 운송업"],
                    "항공": ["항공 운송업"],
                    "방산": ["항공기, 우주선 및 보조장비 제조업"],
                    "음식료": ["식료품 제조업", "음료 제조업", "담배 제조업"],
                    "유통": ["종합 소매업", "전문 소매업", "무점포 소매업"],
                    # ... FinanceDataReader의 'Industry' 고유값을 참고하여 추가/수정
                }
                for industry_key, industry_names in INDUSTRY_KEYWORD_MAP.items():
                    if lower_keyword == industry_key.lower() or any(name.lower() in lower_keyword for name in industry_names):
                        print(f"디버그: 업종 '{industry_key}'에 해당하는 종목을 검색합니다.")
                        target_stocks = krx[krx['Industry'].isin(industry_names)]
                        analysis_subject = f"'{industry_key}' 업종"
                        found_by_industry = True
                        break
            
            if not found_by_industry:
                # 3. Fallback to name-based search (가장 마지막 순위)
                print(f"디버그: 종목명에 '{keyword}' 키워드가 포함된 종목을 검색합니다. (최종 폴백)")
                target_stocks = krx[krx['Name'].str.contains(keyword, na=False)]
    
    elif target_str in GENERIC_TARGETS:
        analysis_subject = "시장 전체"
    
    print(f"--- 디버그 종료 (get_target_stocks) ---")
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
    
    final_result = None # final_result 초기화
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
                start = raw_text.find('{')
                end = raw_text.rfind('}') + 1
                cleaned_response = raw_text[start:end]
                intent_json = json.loads(cleaned_response)
                
                new_cache_key = str(hash(json.dumps(intent_json, sort_keys=True)))
                ANALYSIS_CACHE[new_cache_key] = { 'intent_json': intent_json }
                cache_key = new_cache_key 
            
            except (json.JSONDecodeError, IndexError) as e:
                # JSON 파싱 실패 시, 일반 대화형 답변으로 폴백
                print(f" JSON 분석 실패({e}). 일반 대화형 모델로 폴백합니다.")
                try:
                    general_prompt = f"다음 질문에 대해 친절하고 상세하게 답변해줘: {user_query}"
                    fallback_response = model.generate_content(general_prompt)
                    
                    final_result = {
                        "analysis_subject": "일반 답변",
                        "result": [fallback_response.text.replace('\r\n', '<br>').replace('\n', '<br>').strip()] # 줄바꿈 처리 강화
                    }
                    return jsonify(final_result)
                
                except Exception as fallback_e:
                    print(f" 폴백 모델 호출 실패: {fallback_e}")
                    traceback.print_exc()
                    return jsonify({"error": "질문을 분석하는데 실패했고, 일반 답변도 가져올 수 없었습니다."}), 500
        
        if not intent_json or not intent_json.get("query_type"): 
            print(f"디버그: Gemini가 반환한 JSON이 유효하지 않거나 query_type이 없습니다: {intent_json}")
            print(f" 알 수 없는 질문 유형 또는 유효하지 않은 JSON. 일반 대화형 모델로 폴백합니다.")
            general_prompt = f"다음 질문에 대해 친절하게 답변해줘: {user_query}"
            fallback_response = model.generate_content(general_prompt)
            final_result = {
                "analysis_subject": "일반 답변",
                "result": [fallback_response.text.replace('\r\n', '<br>').replace('\n', '<br>').strip()]
            }
            return jsonify(final_result)

        query_type = intent_json.get("query_type")
        
        if query_type == "stock_analysis":
            final_result = execute_stock_analysis(intent_json, page, user_query, cache_key)
            if not final_result.get('result') or (isinstance(final_result.get('result'), list) and len(final_result['result']) == 0):
                print(f"디버그: 주식 분석 결과가 0개입니다. 일반 대화형 모델로 폴백합니다.")
                general_prompt = f"요청하신 '{user_query}'에 대한 주식 데이터를 충분히 찾거나 분석할 수 없었습니다. 다른 질문을 해주시겠어요?"
                fallback_response = model.generate_content(general_prompt)
                final_result = {
                    "analysis_subject": "일반 답변",
                    "result": [fallback_response.text.replace('\r\n', '<br>').replace('\n', '<br>').strip()]
                }

        elif query_type == "indicator_lookup":
            lookup_result = execute_indicator_lookup(intent_json)
            
            if lookup_result.get("analysis_subject") == "알 수 없는 지표" or \
               not lookup_result.get('result') or \
               (isinstance(lookup_result.get('result'), list) and len(lookup_result['result']) == 0):
                
                print(f"디버그: 지표 조회 실패 또는 결과가 0개입니다. 일반 대화형 모델로 폴백합니다.")
                general_prompt = f"요청하신 '{user_query}'에 대한 지표 데이터를 찾을 수 없거나 분석에 문제가 있었습니다. 다른 질문을 해주시겠어요?"
                fallback_response = model.generate_content(general_prompt)
                final_result = {
                    "analysis_subject": "일반 답변",
                    "result": [fallback_response.text.replace('\r\n', '<br>').replace('\n', '<br>').strip()]
                }
            else:
                final_result = lookup_result 

        else: 
            print(f"디버그: 알 수 없는 query_type '{query_type}'. 일반 대화형 모델로 폴백합니다.")
            general_prompt = f"다음 질문에 대해 친절하게 답변해줘: {user_query}"
            fallback_response = model.generate_content(general_prompt)
            final_result = {
                "analysis_subject": "일반 답변",
                "result": [fallback_response.text.replace('\r\n', '<br>').replace('\n', '<br>').strip()]
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