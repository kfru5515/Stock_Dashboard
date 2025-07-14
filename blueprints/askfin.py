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
from flask import Blueprint, render_template, request, jsonify
from dotenv import load_dotenv
from datetime import datetime, timedelta

from pykrx import stock
import re
from bs4 import BeautifulSoup

TICKER_NAME_MAP = None
NAME_TICKER_MAP = None
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

3.  User Query: "지난 3년 동안 겨울에 오른 콘텐츠 관련 주식을 보여줘"
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

# --- Helper Functions ---

def _load_ticker_maps():
    """종목 정보가 로드되지 않았을 경우에만 로드하는 함수"""
    global TICKER_NAME_MAP, NAME_TICKER_MAP
    # 맵이 비어있을 때만 (최초 호출 시) 실행
    if NAME_TICKER_MAP is None:
        print("지연 로딩: 전체 종목 코드 및 이름 로딩을 시작합니다...")
        all_tickers = stock.get_market_ticker_list(market="ALL")
        TICKER_NAME_MAP = {ticker: stock.get_market_ticker_name(ticker) for ticker in all_tickers}
        NAME_TICKER_MAP = {name: ticker for ticker, name in TICKER_NAME_MAP.items()}
        print("종목 정보 로딩 완료.")

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
    [최종 완성] 시장 정보를 명시적으로 지정하여 일괄 조회함으로써
    안정성과 속도를 모두 확보한 최종 API.
    """
    try:
        profile_data = {}
        latest_business_day = stock.get_nearest_business_day_in_a_week()

        # --- 1. fdr에서 종목의 기본 정보 및 소속 시장(Market) 확인 ---
        krx_list = fdr.StockListing('KRX')
        target_info = krx_list[krx_list['Code'] == code]
        if target_info.empty:
            return jsonify({"error": f"종목코드 '{code}'를 찾을 수 없습니다."}), 404
        
        target_info = target_info.iloc[0]
        market = target_info.get('Market', 'KOSPI') # 기본값 KOSPI
        sector = target_info.get('Sector')
        
        profile_data['기업명'] = target_info.get('Name', 'N/A')
        profile_data['업종'] = sector
        profile_data['주요제품'] = target_info.get('Industry', 'N/A')

        # --- 2. 확인된 시장(Market)의 데이터를 pykrx로 일괄 조회 ---
        print(f"'{market}' 시장의 전체 데이터를 일괄 조회합니다...")
        df_ohlcv = stock.get_market_ohlcv(latest_business_day, market=market)
        df_cap = stock.get_market_cap(latest_business_day, market=market)
        df_funda = stock.get_market_fundamental(latest_business_day, market=market)
        print("일괄 조회 완료.")

        # --- 3. 일괄 조회된 데이터에서 해당 종목 정보 추출 ---
        current_price = df_ohlcv.loc[code, '종가']
        market_cap = df_cap.loc[code, '시가총액']
        funda = df_funda.loc[code]

        profile_data['현재가'] = f"{current_price:,} 원"
        if market_cap > 1_0000_0000_0000:
            profile_data['시가총액'] = f"{market_cap / 1_0000_0000_0000:.2f} 조원"
        else:
            profile_data['시가총액'] = f"{market_cap / 1_0000_0000:.2f} 억원"
        
        eps = funda.get('EPS', 0)
        per = funda.get('PER', 0)
        pbr = funda.get('PBR', 0)
        div = funda.get('DIV', 0)
        profile_data['PER'] = f"{per:.2f} 배" if per > 0 else "N/A"
        profile_data['PBR'] = f"{pbr:.2f} 배" if pbr > 0 else "N/A"
        profile_data['배당수익률'] = f"{div:.2f} %" if div > 0 else "N/A"

        # --- 4. 적정주가 계산 ---
        if sector and eps > 0:
            # 업종 평균 PER 계산을 위해 krx_list와 df_funda를 병합
            merged_df = krx_list.set_index('Code').join(df_funda)
            sector_pers = merged_df[merged_df['Sector'] == sector]['PER']
            sector_pers = sector_pers[sector_pers > 0]
            
            if not sector_pers.empty:
                avg_per = sector_pers.mean()
                fair_price = eps * avg_per
                upside = ((fair_price / current_price) - 1) * 100 if current_price > 0 else 0
                profile_data['적정주가(업종PER기반)'] = f"{int(fair_price):,} 원"
                profile_data['상승여력'] = f"{upside:.2f} %"

        return jsonify({"company_profile": profile_data})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"기업 상세 정보 처리 중 오류 발생: {str(e)}"}), 500
    
def get_target_stocks(target_str):
    """타겟 문자열에 해당하는 종목 리스트(DataFrame)를 반환하는 함수 (themes.json 사용)"""
    
    GENERIC_TARGETS = {"주식", "종목", "급등주", "우량주", "인기주", "전체"}
    print("KOSPI 및 KOSDAQ 종목 목록 로딩 중...")
    krx = fdr.StockListing('KRX')
    print("종목 목록 로딩 완료.")
    
    analysis_subject = "시장 전체"
    target_stocks = krx

    if target_str and target_str.strip() and target_str not in GENERIC_TARGETS:
        analysis_subject = f"'{target_str}'"
        
        keyword = target_str.replace(" 관련주", "").replace(" 테마주", "").replace(" 테마", "").replace("주", "").strip()

        if keyword in THEME_MAP:
            print(f"테마 '{keyword}'에 대한 종목을 검색합니다.")
            theme_stock_names = THEME_MAP[keyword]
            _load_ticker_maps() 
            target_codes = [NAME_TICKER_MAP.get(name) for name in theme_stock_names if NAME_TICKER_MAP.get(name)]
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

    return today - timedelta(days=365), today # 기본값: 1년

def get_target_stocks(target_str):
    """타겟 문자열에 해당하는 종목 리스트(DataFrame)를 반환하는 함수"""
    
    THEME_MAP = {
        "방산주": ['012450', '047810', '079550', '064350', '272210'],
    }

    GENERIC_TARGETS = {"주식", "종목", "급등주", "우량주", "인기주", "전체"}
    print("KOSPI 및 KOSDAQ 종목 목록 로딩 중...")
    krx = fdr.StockListing('KRX')
    print("종목 목록 로딩 완료.")
    
    analysis_subject = "시장 전체"
    target_stocks = krx

    if target_str and target_str.strip():
        analysis_subject = f"'{target_str}'"

        if target_str in THEME_MAP:
            print(f"테마 '{target_str}'에 대한 종목을 검색합니다.")
            target_codes = THEME_MAP[target_str]
            target_stocks = krx[krx['Code'].isin(target_codes)]
        
        elif target_str not in GENERIC_TARGETS:
            print(f"종목명에 '{target_str}' 키워드가 포함된 종목을 검색합니다.")
            keyword = target_str.replace(" 관련주", "").replace("주", "")
            target_stocks = krx[krx['Name'].str.contains(keyword, na=False)]
        
        else:
            analysis_subject = "시장 전체"
            target_stocks = krx
            
    return target_stocks, analysis_subject

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
    
def handle_season_condition(date_range, season):
    start_date, end_date = date_range
    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    
    periods = []

    for year in range(start_year, end_year + 1):
        if season == "여름":
            periods.append((f"{year}-06-01", f"{year}-08-31"))
        elif season == "봄":
            periods.append((f"{year}-03-01", f"{year}-05-31"))
        elif season == "가을":
            periods.append((f"{year}-09-01", f"{year}-11-30"))
        elif season == "겨울":
            periods.append((f"{year}-12-01", f"{year+1}-02-28"))
        else:
            periods.append((start_date, end_date))  # fallback

    return periods


def execute_stock_analysis(intent_json, page):
    """
    주식 분석을 수행하고, 페이지네이션 정보를 포함하여 표준화된 결과를 반환하는 최종 함수.
    """
    try:
        # 1. 사용자 의도(JSON)에서 분석에 필요한 정보 추출
        target_str = intent_json.get("target")
        action_str = intent_json.get("action", "")
        condition_str = intent_json.get("condition")

        # 2. 분석 대상 종목 선정
        target_stocks, analysis_subject = get_target_stocks(target_str)
        if target_stocks.empty:
            return {"result": [f"{analysis_subject}에 해당하는 종목을 찾을 수 없습니다."]}

        # 3. 분석 기간 및 조건(계절 등)에 따른 세부 기간 설정
        start_date, end_date = parse_period(intent_json.get("period"))
        
        event_periods = []
        if isinstance(condition_str, str) and any(s in condition_str for s in ["여름", "겨울"]):
            season = "여름" if "여름" in condition_str else "겨울"
            event_periods = handle_season_condition((start_date, end_date), season)
        else:
            event_periods = [(start_date, end_date)]

        # 4. 사용자의 '액션'에 따라 적절한 분석 함수 호출
        result_data = []
        if "오른" in action_str or "내린" in action_str:
            result_data = analyze_top_performers(target_stocks, event_periods, (start_date, end_date))
        elif "변동성" in action_str or "변동" in action_str:
            result_data = analyze_volatility(target_stocks, (start_date, end_date))
        elif "목표주가" in action_str:
            result_data = analyze_target_price_upside(target_stocks)
        else:
            return {"error": f"'{action_str}' 액션은 아직 지원하지 않습니다."}

        # 5. 분석 결과 정렬
        reverse_sort = False if "내린" in action_str else True
        sorted_result = sorted(result_data, key=lambda x: x.get('value', -999), reverse=reverse_sort)
        
        # 6. 페이지네이션 처리
        items_per_page = 20
        total_items = len(sorted_result)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        paginated_result = sorted_result[start_index:end_index]
        
        # 7. 최종 결과 JSON 구성하여 반환
        return {
            "query_intent": intent_json,
            "analysis_subject": analysis_subject,
            "result": paginated_result,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_items": total_items
            }
        }
    except Exception as e:
        traceback.print_exc()
        return {"error": f"분석 중 오류 발생: {e}"}
    

def handle_season_condition(period_tuple, season):
    """'여름' 또는 '겨울' 조건에 맞는 날짜 구간 리스트를 반환하는 함수"""
    start_date, end_date = period_tuple
    event_periods = []
    
    # 분석할 기간 내의 모든 연도를 순회
    for year in range(start_date.year, end_date.year + 1):
        season_start, season_end = None, None
        
        if season == "겨울":

            dec_first_prev_year = datetime(year - 1, 12, 1)
            feb_last_this_year = datetime(year, 3, 1) - timedelta(days=1)
            if dec_first_prev_year <= end_date and feb_last_this_year >= start_date:
                event_periods.append((max(dec_first_prev_year, start_date), min(feb_last_this_year, end_date)))

        elif season == "여름":
            season_start = datetime(year, 6, 1)
            season_end = datetime(year, 8, 31)
            if season_start <= end_date and season_end >= start_date:
                event_periods.append((max(season_start, start_date), min(season_end, end_date)))

    return event_periods

def handle_interest_rate_condition(api_key, period_tuple):
    """금리 인상 조건에 맞는 날짜 구간 리스트를 반환하는 함수"""
    start_date, end_date = period_tuple
    hike_dates = get_interest_rate_hike_dates(api_key)
    
    event_periods = []
    for hike_date in hike_dates:
        event_start = hike_date
        event_end = event_start + timedelta(days=7)
        
        if event_start >= start_date and event_end <= end_date:
            event_periods.append((event_start, event_end))
            
    return event_periods

# askfin.py

def analyze_top_performers(target_stocks, event_periods, overall_period):
    """수익률 분석 함수 (딜레이 추가로 안정성 확보)"""
    analysis_results = []
    top_stocks = target_stocks.nlargest(min(len(target_stocks), 100), 'Marcap').reset_index(drop=True)
    print(f"시가총액 상위 {len(top_stocks)}개 종목에 대한 수익률 분석을 시작합니다...")
    overall_start, overall_end = overall_period

    for index, stock in top_stocks.iterrows():
        stock_code, stock_name = stock['Code'], stock['Name']
        print(f"  ({index + 1}/{len(top_stocks)}) {stock_name}({stock_code}) 분석 중...")
        try:
            overall_prices = fdr.DataReader(stock_code, overall_start, overall_end)
            if overall_prices.empty:
                time.sleep(0.2) # 실패 시에도 딜레이
                continue
            
            start_price = overall_prices['Open'].iloc[0]
            end_price = overall_prices['Close'].iloc[-1]
            period_returns = []
            for start, end in event_periods:
                prices = fdr.DataReader(stock_code, start, end)
                if len(prices) > 1:
                    event_start_price = prices['Open'].iloc[0]
                    event_end_price = prices['Close'].iloc[-1]
                    if event_start_price > 0:
                        period_returns.append((event_end_price / event_start_price) - 1)
            
            if period_returns:
                average_return = statistics.mean(period_returns)
                if pd.notna(average_return):
                    analysis_results.append({
                        "code": stock_code,
                        "name": stock_name,
                        "value": round(average_return * 100, 2),
                        "label": "평균 수익률(%)",
                        "start_price": int(start_price),
                        "end_price": int(end_price)
                    })
        except Exception as e:
            print(f"  - {stock_name}({stock_code}) 분석 중 오류 발생: {e}")
            pass
        
        # --- 각 종목 분석 후 0.2초 대기 ---
        time.sleep(0.2)
            
    return analysis_results

def analyze_volatility(target_stocks, period_tuple):
    """변동성 분석 함수 (딜레이 추가로 안정성 확보)"""
    analysis_results = []
    start_date, end_date = period_tuple
    top_stocks = target_stocks.nlargest(min(len(target_stocks), 100), 'Marcap').reset_index(drop=True)
    print(f"시가총액 상위 {len(top_stocks)}개 종목에 대한 변동성 분석을 시작합니다...")

    for index, stock_info in top_stocks.iterrows():
        code, name = stock_info['Code'], stock_info['Name']
        print(f"  ({index + 1}/{len(top_stocks)}) {name}({code}) 분석 중...")
        try:
            overall_prices = fdr.DataReader(code, start_date, end_date)
            if overall_prices.empty:
                time.sleep(0.2) # 실패 시에도 딜레이
                continue
            
            daily_returns = overall_prices['Close'].pct_change().dropna()
            volatility = daily_returns.std()
            if pd.notna(volatility):
                analysis_results.append({
                    "code": code, "name": name,
                    "value": round(volatility * 100, 2), "label": "변동성(%)",
                    "start_price": overall_prices['Open'].iloc[0],
                    "end_price": overall_prices['Close'].iloc[-1]
                })
        except Exception as e:
            print(f"  - {name}({code}) 분석 중 오류 발생: {e}")
            pass
            
        # --- 각 종목 분석 후 0.2초 대기 ---
        time.sleep(0.2)
            
    return analysis_results

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

    # 조건에 맞는 날짜(월) 필터링
    if op_str == '>': matching_series = data_series[data_series > value]
    elif op_str == '>=': matching_series = data_series[data_series >= value]
    # ... 다른 연산자 추가 가능
    else: return []

    # 해당 월의 시작일과 종료일을 분석 구간으로 설정
    return [(d.replace(day=1), (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)) for d in matching_series.index]

def get_bok_data(bok_api_key, stats_code, item_code, start_date, end_date):
    """
    한국은행 ECOS API를 통해 특정 지표의 시계열 데이터를 가져와 Pandas Series로 반환.
    """
    # BOK API는 YYYYMM 형식의 월별 조회를 사용
    start_str = start_date.strftime('%Y%m')
    end_str = end_date.strftime('%Y%m')
    
    # API 요청 URL (최대 1000개 데이터 요청)
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

QUERY_CACHE = {}

@askfin_bp.route('/analyze', methods=['POST'])
def analyze_query():
    """
    [최종 수정] AI 응답에서 JSON을 더 정확하게 추출하도록 수정한 API.
    """
    if not model:
        return jsonify({"error": "모델이 초기화되지 않았습니다. API 키를 확인하세요."}), 500
    
    data = request.get_json()
    if not data or 'query' not in data:
        return jsonify({"error": "잘못된 요청입니다."}), 400

    user_query = data['query']
    page = data.get('page', 1)

    try:
        # 캐싱 로직은 그대로 유지
        if user_query in QUERY_CACHE:
            print(f"✅ CACHE HIT: '{user_query}'에 대한 캐시된 결과를 사용합니다.")
            intent_json = QUERY_CACHE[user_query]
        else:
            print(f"🔥 CACHE MISS: '{user_query}'에 대해 Gemini API를 호출합니다.")
            prompt = PROMPT_TEMPLATE.format(user_query=user_query)
            response = model.generate_content(prompt)

            # --- ▼▼▼ JSON 추출 및 정제 로직 강화 ▼▼▼ ---
            raw_text = response.text
            # 문자열에서 첫 '{'와 마지막 '}'를 찾아 그 사이의 내용만 추출
            try:
                start = raw_text.find('{')
                end = raw_text.rfind('}') + 1
                cleaned_response = raw_text[start:end]
            except Exception:
                cleaned_response = ""
            # --- ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ ---

            if not cleaned_response or not cleaned_response.startswith('{'):
                print(f"❌ Gemini가 유효한 JSON을 반환하지 않았습니다. 응답: '{raw_text}'")
                return jsonify({"error": "AI가 요청을 처리할 수 없거나 부적절한 질문으로 판단했습니다. 다른 질문으로 다시 시도해주세요."})
            
            intent_json = json.loads(cleaned_response)
            QUERY_CACHE[user_query] = intent_json
        
        query_type = intent_json.get("query_type")
        
        final_result = {}
        if query_type == "stock_analysis":
            final_result = execute_stock_analysis(intent_json, page)
        elif query_type == "indicator_lookup":
            final_result = execute_indicator_lookup(intent_json)
        else:
            final_result = {"error": f"알 수 없는 질문 유형입니다: {query_type}"}
            
        return jsonify(final_result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"분석 중 오류 발생: {str(e)}"}), 500

@askfin_bp.route('/new_chat', methods=['POST'])
def new_chat():
    """대화 기록(세션)을 초기화합니다."""
    session.pop('chat_history', None)
    return jsonify({"status": "success", "message": "새 대화를 시작합니다."})