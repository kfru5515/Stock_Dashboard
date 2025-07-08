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

# .env 파일 로드
load_dotenv()

# Blueprint 객체 생성
askfin_bp = Blueprint('askfin', __name__, url_prefix='/askfin')

# --- LLM 설정 (질문 유형 분류 기능 추가) ---
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
    GENERIC_TARGETS = {"주식", "종목", "급등주", "우량주", "인기주", "전체"}
    print("KOSPI 및 KOSDAQ 종목 목록 로딩 중...")
    krx = fdr.StockListing('KRX')
    print("종목 목록 로딩 완료.")
    if target_str and target_str.strip() and target_str not in GENERIC_TARGETS:
        keyword = target_str.replace(" 관련주", "").replace("주", "")
        target_stocks = krx[krx['Name'].str.contains(keyword, na=False)]
        analysis_subject = f"'{target_str}'"
    else:
        target_stocks = krx
        analysis_subject = "시장 전체"

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

    # 1. 의도(Intent) 파싱
    period_str = intent_json.get("period")
    condition = intent_json.get("condition")
    target_str = intent_json.get("target")
    action_str = intent_json.get("action", "")

    # 2. 분석 대상 및 기간 설정
    target_stocks, analysis_subject = get_target_stocks(target_str)
    if target_stocks.empty:
        return {"query_intent": intent_json, "result": [f"'{target_str}'에 해당하는 종목을 찾을 수 없습니다."]}
    
    start_date, end_date = parse_period(period_str)
    
    # 3. 조건(Condition)에 따라 분석할 날짜 구간(event_periods) 결정
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
    
    # 조건이 없으면 전체 기간을 하나의 이벤트로 간주
    if not event_periods:
        event_periods = [(start_date, end_date)]

    # 4. 행동(Action)에 따라 적절한 분석 함수 호출
    result_data = []
    if "오른" in action_str: # '가장 많이 오른', '오른' 모두 이 로직 사용
        result_data = analyze_top_performers(target_stocks, event_periods, (start_date, end_date))
    else:
        # TODO: 다른 Action(예: 변동성)에 대한 핸들러 추가
        return {"error": f"'{action_str}' 액션은 아직 지원하지 않습니다."}

    # 5. 결과 정렬 및 반환
    sort_key = 'average_return_pct'
    sorted_result = sorted(result_data, key=lambda x: x.get(sort_key, -np.inf), reverse=True)
    
    return {
        "query_intent": intent_json,
        "analysis_subject": analysis_subject,
        "result": sorted_result[:20] if sorted_result else ["조건을 만족하는 종목이 없습니다."]
    }

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