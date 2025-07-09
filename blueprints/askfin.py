import dart_fss as dart

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

def execute_indicator_lookup(intent_json):
    """경제 지표를 조회하고 자연어 답변을 생성하는 함수"""
    target = intent_json.get("target", "")
    bok_api_key = os.getenv("ECOS_API_KEY")
    if not bok_api_key: return {"error": "한국은행 API 키가 설정되지 않았습니다."}

    INDICATOR_MAP = {
        "CPI": {"stats_code": "901Y001", "item_code": "0", "name": "소비자물가지수"},
        "기준금리": {"stats_code": "722Y001", "item_code": "0001000", "name": "기준금리"},
    }
    
    found_indicator = None
    for key, value in INDICATOR_MAP.items():
        if key in target or value['name'] in target:
            found_indicator = value
            break
            
    if not found_indicator:
        # 'analysis_subject'를 포함하여 반환
        return {
            "query_intent": intent_json,
            "analysis_subject": "알 수 없는 지표",
            "result": [f"'{target}' 지표는 아직 지원하지 않습니다."]
        }
        
    end_date = datetime.now().strftime('%Y%m')
    start_date = (datetime.now() - timedelta(days=60)).strftime('%Y%m')
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{bok_api_key}/json/kr/1/10/{found_indicator['stats_code']}/MM/{start_date}/{end_date}/{found_indicator['item_code']}"

    try:
        response = requests.get(url, timeout=10).json()
        rows = response.get("StatisticSearch", {}).get("row", [])
        if len(rows) < 2:
            return {
                "query_intent": intent_json,
                "analysis_subject": found_indicator['name'],
                "result": [f"최근 {found_indicator['name']} 데이터를 조회할 수 없습니다."]
            }
            
        latest = rows[-1]
        previous = rows[-2]
        latest_date = f"{latest['TIME'][:4]}년 {latest['TIME'][4:]}월"
        change = float(latest['DATA_VALUE']) - float(previous['DATA_VALUE'])
        change_str = f"{abs(change):.2f} 상승" if change > 0 else f"{abs(change):.2f} 하락" if change < 0 else "변동 없음"

        result_sentence = (f"가장 최근({latest_date}) {found_indicator['name']}는 {latest['DATA_VALUE']}이며, 전월 대비 {change_str}했습니다.")
        
        # 'analysis_subject'와 함께 최종 결과 반환
        return {
            "query_intent": intent_json,
            "analysis_subject": found_indicator['name'],
            "result": [result_sentence] # 결과를 리스트로 감싸서 형식 통일
        }
    except Exception as e:
        print(f"지표 조회 중 오류: {e}")
        return {"error": "지표 조회 중 오류가 발생했습니다."}
    

@askfin_bp.route('/stock/<code>/profile')
def get_stock_profile(code):
    """
    DART, pykrx 정보를 통합하고, 예외 처리를 통해 안정성을 극대화한 최종 API.
    """
    try:
        # --- 1. DART에서 기본 프로필 정보 가져오기 ---
        corp_list = dart.get_corp_list()
        corp = corp_list.find_by_stock_code(code)
        if not corp:
            return jsonify({"error": f"종목코드 '{code}'에 해당하는 기업 정보를 찾을 수 없습니다."}), 404

        info_dict = corp.__dict__.get('_info', {})
        sector = info_dict.get('sector')
        profile_data = {
            '기업명': info_dict.get('corp_name', 'N/A'),
            '업종': sector,
            '주요제품': info_dict.get('product', 'N/A'),
        }

        # --- 2. pykrx에서 핵심 투자 지표 가져오기 ---
        latest_business_day = stock.get_nearest_business_day_in_a_week()
        
        # 현재가 조회
        df_ohlcv = stock.get_market_ohlcv_by_date(fromdate=latest_business_day, todate=latest_business_day, ticker=code)
        current_price = 0
        if not df_ohlcv.empty:
            current_price = df_ohlcv.iloc[0]['종가']
            profile_data['현재가'] = f"{current_price:,} 원"

        # 시가총액 조회
        df_cap = stock.get_market_cap_by_ticker(latest_business_day, code)
        if not df_cap.empty:
            market_cap = df_cap.iloc[0]['시가총액']
            if market_cap > 1_0000_0000_0000:
                profile_data['시가총액'] = f"{market_cap / 1_0000_0000_0000:.2f} 조원"
            else:
                profile_data['시가총액'] = f"{market_cap / 1_0000_0000:.2f} 억원"
        
        # --- ▼▼▼ 펀더멘털 및 적정주가 계산 전체를 try-except로 감싸기 ▼▼▼ ---
        try:
            # 펀더멘털 정보 조회
            df_fundamental = stock.get_market_fundamental_by_ticker(latest_business_day, code)
            eps = 0
            if not df_fundamental.empty:
                fundamental = df_fundamental.iloc[0]
                eps = fundamental.get('EPS', 0)
                per = fundamental.get('PER', 0)
                pbr = fundamental.get('PBR', 0)
                div = fundamental.get('DIV', 0)
                profile_data['PER'] = f"{per:.2f} 배" if per > 0 else "N/A"
                profile_data['PBR'] = f"{pbr:.2f} 배" if pbr > 0 else "N/A"
                profile_data['배당수익률'] = f"{div:.2f} %" if div > 0 else "N/A"

            # 업종 PER 기반 적정주가 계산
            if sector and eps > 0:
                krx_list = fdr.StockListing('KRX')
                sector_stocks = krx_list[krx_list['Sector'] == sector]
                sector_pers = []
                # 업종 평균 계산 시 너무 많은 요청을 보내지 않도록 종목 수 제한
                for ticker in sector_stocks['Code'].head(30):
                    funda = stock.get_market_fundamental_by_ticker(latest_business_day, ticker)
                    if not funda.empty and funda.iloc[0]['PER'] > 0:
                        sector_pers.append(funda.iloc[0]['PER'])
                
                if sector_pers:
                    avg_per = statistics.mean(sector_pers)
                    fair_price = eps * avg_per
                    upside = ((fair_price / current_price) - 1) * 100 if current_price > 0 else 0
                    profile_data['적정주가(업종PER기반)'] = f"{int(fair_price):,} 원"
                    profile_data['상승여력'] = f"{upside:.2f} %"
        
        except KeyError:
            # KeyError 발생 시, 펀더멘털/적정주가 정보 없이 그냥 넘어감
            print(f"'{code}' 종목의 펀더멘털 데이터를 찾을 수 없어 해당 정보는 건너뜁니다.")
            pass
        # --- ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ ---

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

def execute_stock_analysis(intent_json):
    """주식 분석을 수행하는 함수"""
    print(f"주식 분석 시작: {intent_json}")

    period_str = intent_json.get("period")
    condition = intent_json.get("condition")
    target_str = intent_json.get("target")
    action_str = intent_json.get("action", "")

    # 위에서 수정한 get_target_stocks 함수를 호출합니다.
    target_stocks, analysis_subject = get_target_stocks(target_str)
    
    # 받은 종목 리스트가 비어있는지 여기서 확인합니다.
    if target_stocks.empty:
        return {
            "query_intent": intent_json,
            "analysis_subject": analysis_subject,
            "result": [f"{analysis_subject}에 해당하는 종목을 찾을 수 없습니다."]
        }

    start_date, end_date = parse_period(period_str)
    
    event_periods = []
    if isinstance(condition, dict) and condition.get("type") == "indicator":
        event_periods = handle_indicator_condition(condition, (start_date, end_date))
    elif isinstance(condition, str):
        if "금리" in condition:
            bok_api_key = os.getenv("ECOS_API_KEY")
            if not bok_api_key: return {"error": "한국은행 API 키가 설정되지 않았습니다."}
            event_periods = handle_interest_rate_condition(bok_api_key, (start_date, end_date))
        elif any(s in condition for s in ["여름", "겨울"]):
            season = "여름" if "여름" in condition else "겨울"
            event_periods = handle_season_condition((start_date, end_date), season)
    
    if not event_periods:
        event_periods = [(start_date, end_date)]

    result_data = []
    if "오른" in action_str:
        result_data = analyze_top_performers(target_stocks, event_periods, (start_date, end_date))
    elif "내린" in action_str:
        result_data = analyze_top_performers(target_stocks, event_periods, (start_date, end_date))
        sort_descending = False 

    else:
        return {"error": f"'{action_str}' 액션은 아직 지원하지 않습니다."}

    sort_key = 'average_return_pct'
    sorted_result = sorted(result_data, key=lambda x: x.get(sort_key, -np.inf), reverse=True)
    
    return {
        "query_intent": intent_json,
        "analysis_subject": analysis_subject,
        "result": sorted_result[:20] if sorted_result else ["조건을 만족하는 종목이 없습니다."]
    }

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
    """
    주어진 종목들과 기간들에 대해 수익률을 분석하고 상위 종목을 반환.
    
    :param target_stocks: 분석할 종목들의 DataFrame.
    :param event_periods: 분석할 특정 기간들의 리스트 [(start_date, end_date), ...].
    :param overall_period: 전체 분석 기간 (현재는 사용되지 않으나 확장성 위해 유지).
    :return: 종목별 분석 결과 리스트 (딕셔너리 형태).
    """
    analysis_results = []
    
    # KOSPI와 KOSDAQ만 필터링하고 시가총액으로 정렬
    target_stocks = target_stocks[target_stocks['Market'].isin(['KOSPI', 'KOSDAQ'])]
    top_stocks = target_stocks.nlargest(100, 'Marcap').reset_index(drop=True)

    print(f"시가총액 상위 {len(top_stocks)}개 종목에 대한 분석을 시작합니다...")

    for index, stock in top_stocks.iterrows():
        stock_code = stock['Code']
        stock_name = stock['Name']
        
        period_returns = []
        
        print(f"  ({index + 1}/{len(top_stocks)}) {stock_name}({stock_code}) 분석 중...")

        for start, end in event_periods:
            try:
                prices = fdr.DataReader(stock_code, start, end)
                
                if not prices.empty and len(prices) > 1:
                    start_price = prices['Open'].iloc[0]
                    end_price = prices['Close'].iloc[-1]
                    
                    if start_price > 0:
                        period_return = (end_price / start_price) - 1
                        period_returns.append(period_return)

            except Exception as e:
                # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 이 부분이 수정되었습니다 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
                # 오류가 발생해도 아무것도 하지 않고 계속 진행하도록 pass를 추가
                pass
                # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

        if period_returns:
            average_return = statistics.mean(period_returns)
            analysis_results.append({
                "code": stock_code,
                "name": stock_name,
                "average_return_pct": round(average_return * 100, 2),
                "event_count": len(period_returns)
            })

    print("주식 분석 완료.")
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

@askfin_bp.route('/analyze', methods=['POST'])
def analyze_query():
    if not model:
        return jsonify({"error": "모델이 초기화되지 않았습니다. API 키를 확인하세요."}), 500
    data = request.get_json()
    if not data or 'query' not in data: return jsonify({"error": "잘못된 요청입니다."}), 400

    user_query = data['query']

    try:
        prompt = PROMPT_TEMPLATE.format(user_query=user_query)
        response = model.generate_content(prompt)
        cleaned_response = response.text.strip().replace("```json", "").replace("```", "")
        intent_json = json.loads(cleaned_response)
        
        query_type = intent_json.get("query_type")
        
        if query_type == "stock_analysis":
            final_result = execute_stock_analysis(intent_json)
        elif query_type == "indicator_lookup":
            final_result = execute_indicator_lookup(intent_json)
        else:
            final_result = {"error": f"알 수 없는 질문 유형입니다: {query_type}"}
            
        return jsonify(final_result)

    except Exception as e:
        print("="*30, "\n!!! AN ERROR OCCURRED IN /analyze !!!")
        traceback.print_exc()
        print("="*30)
        return jsonify({"error": f"분석 중 오류 발생: {str(e)}"}), 500