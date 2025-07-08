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
    

@askfin_bp.route('/stock/<string:code>/financials')
def get_stock_financials(code):
    """네이버 증권에서 기업의 연간 실적 정보를 스크래핑하여 반환하는 API (우선주 처리 기능 포함)"""
    try:

        _load_ticker_maps()

        target_code = code
        stock_name = TICKER_NAME_MAP.get(code)
        
        if not stock_name:
            return jsonify({"error": f"종목코드 '{code}'에 해당하는 종목을 찾을 수 없습니다."}), 404

        is_preferred = '우' in stock_name or stock_name[-1].isdigit() or 'B' in stock_name[-2:]
        
        if is_preferred:
            common_stock_name = re.sub(r'(\d?[우B]?)$', '', stock_name).strip()
            common_stock_code = NAME_TICKER_MAP.get(common_stock_name)
            
            if common_stock_code:
                print(f"우선주 '{stock_name}' 요청 -> 보통주 '{common_stock_name}'({common_stock_code}) 정보로 조회합니다.")
                target_code = common_stock_code
            else:
                return jsonify({"error": f"'{stock_name}'의 보통주를 찾을 수 없어 재무 정보 조회가 불가능합니다."}), 404

        url = f"https://finance.naver.com/item/main.naver?code={target_code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.select_one('div.cop_analysis table.tbl_type1')
        
        if not table:
            return jsonify({"error": "재무 정보를 담고 있는 테이블을 찾을 수 없습니다. (ETF 등 재무정보가 없는 종목일 수 있습니다)"}), 404

        financial_info = []
        th_list = table.select('thead > tr > th')
        dates = [th.text.strip() for th in th_list if th.get('scope') == 'col']
        
        tr_list = table.select('tbody > tr')
        required_items = ['매출액', '영업이익', '당기순이익']
        
        for tr in tr_list:
            item_title_element = tr.select_one('th')
            if item_title_element:
                item_title = item_title_element.text.strip()
                if item_title in required_items:
                    values = [td.text.strip() for td in tr.select('td')]
                    item_data = {'item': item_title, 'values': dict(zip(dates, values))}
                    financial_info.append(item_data)

        if is_preferred:
            result_data = {
                "financial_info": financial_info,
                "message": f"'{stock_name}'(우선주)의 재무정보는 보통주 '{TICKER_NAME_MAP.get(target_code)}' 기준입니다."
            }
            return jsonify(result_data)

        return jsonify({"financial_info": financial_info})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_target_stocks(target_str):
    """타겟 문자열에 해당하는 종목 리스트(DataFrame)를 반환하는 함수 (themes.json 사용)"""
    
    GENERIC_TARGETS = {"주식", "종목", "급등주", "우량주", "인기주", "전체"}
    print("KOSPI 및 KOSDAQ 종목 목록 로딩 중...")
    krx = fdr.StockListing('KRX')
    print("종목 목록 로딩 완료.")
    
    analysis_subject = "시장 전체"
    target_stocks = krx

    if target_str and target_str.strip():
        analysis_subject = f"'{target_str}'"
        
        # 1. 사용자의 원본 입력 그대로 테마 맵에서 먼저 찾아봅니다.
        if target_str in THEME_MAP:
            print(f"테마 '{target_str}'에 대한 종목을 검색합니다.")
            theme_stock_names = THEME_MAP[target_str]
            _load_ticker_maps()
            target_codes = [NAME_TICKER_MAP.get(name) for name in theme_stock_names if NAME_TICKER_MAP.get(name)]
            target_stocks = krx[krx['Code'].isin(target_codes)]
            return target_stocks, analysis_subject # 찾았으면 여기서 함수 종료

        # 2. 원본 입력이 없다면, 키워드를 정리해서 다시 찾아봅니다.
        keyword = target_str.replace(" 관련주", "").replace(" 테마", "").replace("주", "").strip()
        if keyword in THEME_MAP:
            print(f"정리된 키워드 '{keyword}'로 테마를 다시 검색합니다.")
            theme_stock_names = THEME_MAP[keyword]
            _load_ticker_maps()
            target_codes = [NAME_TICKER_MAP.get(name) for name in theme_stock_names if NAME_TICKER_MAP.get(name)]
            target_stocks = krx[krx['Code'].isin(target_codes)]

        # 3. 테마 맵에 최종적으로 없다면, 종목명에서 검색합니다.
        elif target_str not in GENERIC_TARGETS:
            print(f"종목명에 '{keyword}' 키워드가 포함된 종목을 검색합니다.")
            target_stocks = krx[krx['Name'].str.contains(keyword, na=False)]
        
        else:
            analysis_subject = "시장 전체"
            target_stocks = krx
            
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
    
    # 변수들을 미리 기본값으로 초기화합니다.
    analysis_subject = "시장 전체"
    target_stocks = krx

    if target_str and target_str.strip():
        # 검색이 시작되면 analysis_subject를 우선 사용자 요청으로 설정합니다.
        analysis_subject = f"'{target_str}'"

        if target_str in THEME_MAP:
            print(f"테마 '{target_str}'에 대한 종목을 검색합니다.")
            target_codes = THEME_MAP[target_str]
            target_stocks = krx[krx['Code'].isin(target_codes)]
        
        elif target_str not in GENERIC_TARGETS:
            print(f"종목명에 '{target_str}' 키워드가 포함된 종목을 검색합니다.")
            keyword = target_str.replace(" 관련주", "").replace("주", "")
            target_stocks = krx[krx['Name'].str.contains(keyword, na=False)]
        
        # '주식'과 같은 일반적인 타겟이 들어오면, 제목을 다시 '시장 전체'로 설정합니다.
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
            # 겨울은 12월 1일에 시작하여 다음 해 2월 말일에 끝남
            # 해당 연도의 1, 2월 (이전 연도 겨울의 일부)
            dec_first_prev_year = datetime(year - 1, 12, 1)
            feb_last_this_year = datetime(year, 3, 1) - timedelta(days=1)
            if dec_first_prev_year <= end_date and feb_last_this_year >= start_date:
                event_periods.append((max(dec_first_prev_year, start_date), min(feb_last_this_year, end_date)))

        elif season == "여름":
            # 여름은 6월 1일부터 8월 31일까지
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
        # 금리 인상일로부터 1주일 후까지의 기간을 이벤트로 설정
        event_start = hike_date
        event_end = event_start + timedelta(days=7)
        
        # 해당 이벤트 기간이 사용자가 요청한 전체 기간 내에 있는지 확인
        if event_start >= start_date and event_end <= end_date:
            event_periods.append((event_start, event_end))
            
    return event_periods

def analyze_top_performers(target_stocks, event_periods, overall_period):
    """
    주어진 종목들과 기간들에 대해 수익률을 분석하고 상위 종목을 반환.
    
    :param target_stocks: 분석할 종목들의 DataFrame.
    :param event_periods: 분석할 특정 기간들의 리스트 [(start_date, end_date), ...].
    :param overall_period: 전체 분석 기간 (현재는 사용되지 않으나 확장성 위해 유지).
    :return: 종목별 분석 결과 리스트 (딕셔너리 형태).
    """
    analysis_results = []
    
    # 분석 대상 종목 수를 제한하여 과도한 시간 소요 방지 (예: 시가총액 상위 500개)
    # KOSPI와 KOSDAQ만 필터링하고 시가총액으로 정렬
    target_stocks = target_stocks[target_stocks['Market'].isin(['KOSPI', 'KOSDAQ'])]
    top_stocks = target_stocks.nlargest(500, 'Marcap').reset_index(drop=True)

    print(f"시가총액 상위 {len(top_stocks)}개 종목에 대한 분석을 시작합니다...")

    for index, stock in top_stocks.iterrows():
        stock_code = stock['Code']
        stock_name = stock['Name']
        
        period_returns = []
        
        print(f"  ({index + 1}/{len(top_stocks)}) {stock_name}({stock_code}) 분석 중...")

        for start, end in event_periods:
            try:
                # 이벤트 기간의 주가 데이터 조회
                prices = fdr.DataReader(stock_code, start, end)
                
                if not prices.empty and len(prices) > 1:
                    # 기간 시작일의 시가와 종료일의 종가로 수익률 계산
                    start_price = prices['Open'].iloc[0]
                    end_price = prices['Close'].iloc[-1]
                    
                    if start_price > 0:
                        period_return = (end_price / start_price) - 1
                        period_returns.append(period_return)

            except Exception as e:
                # 데이터가 없는 경우 등 예외 발생 시 건너뜀
                # print(f"    - {stock_name} 데이터 조회 오류: {start.date()}~{end.date()} ({e})")
                continue
        
        # 모든 이벤트 기간에 대한 평균 수익률 계산
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

        # 데이터프레임으로 변환 후 처리
        df = pd.DataFrame(rows)
        df['TIME'] = pd.to_datetime(df['TIME'], format='%Y%m') # 날짜 형식 변환
        df['DATA_VALUE'] = pd.to_numeric(df['DATA_VALUE'])   # 숫자 형식 변환
        df = df.set_index('TIME')                             # 날짜를 인덱스로 설정
        
        # 날짜를 기준으로 정렬된 데이터 값(Series) 반환
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
        
        # query_type에 따라 다른 함수 호출
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