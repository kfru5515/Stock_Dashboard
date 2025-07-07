import os
import json
import statistics

import traceback
import requests
import google.generativeai as genai
import FinanceDataReader as fdr
import pandas as pd
from flask import Blueprint, render_template, request, jsonify
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

askfin_bp = Blueprint('askfin', __name__, url_prefix='/askfin')

try:
    API_KEY = os.getenv("GOOGLE_AI_API_KEY")
    if not API_KEY:
        raise ValueError("GOOGLE_AI_API_KEY가 .env 파일에 설정되지 않았습니다.")
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

    PROMPT_TEMPLATE = """
You are a machine that strictly follows instructions. Your one and only task is to extract key intents from a user's query and convert it into a structured JSON object.

- You MUST only respond with a JSON object.
- Do not provide any explanation, comments, or conversational text.
- If a value is not present in the query, use `null`.
- Your entire response must be only the JSON object and nothing else.

## JSON Schema to follow:
{{
  "period": "string or null",
  "condition": "string or null",
  "target": "string or null",
  "action": "string or null"
}}

## Examples:

1.  User Query: "작년에 20% 오른 IT 주식 찾아줘"
    JSON Output:
    ```json
    {{
      "period": "작년",
      "condition": null,
      "target": "IT 주식",
      "action": "20% 오른 주식"
    }}
    ```

2.  User Query: "지난 3년 동안 겨울에 오른 콘텐츠 관련 주식을 보여줘"
    JSON Output:
    ```json
    {{
      "period": "지난 3년",
      "condition": "겨울",
      "target": "콘텐츠 관련주",
      "action": "오른 주식"
    }}
    ```
    
3. User Query: "삼성전자"
   JSON Output:
    ```json
    {{
      "period": null,
      "condition": null,
      "target": "삼성전자",
      "action": null
    }}
    ```

## Task:
User Query: "{user_query}"
JSON Output:
"""
except Exception as e:
    print(f"AskFin Blueprint: 모델 초기화 실패 - {e}")
    model = None

# --- Helper Functions ---

def parse_period(period_str):
    """'지난 3년간' 같은 문자열을 시작일과 종료일로 변환하는 함수"""
    end_date = datetime.now()
    if period_str and "년간" in period_str:
        years = int(period_str.replace("지난", "").replace("년간", "").strip())
        start_date = end_date - timedelta(days=365 * years)
        return start_date, end_date
    # TODO: '개월', '작년' 등 다른 기간 처리 로직 추가
    return end_date - timedelta(days=365), end_date # 기본값: 1년


def get_interest_rate_hike_dates(api_key):
    """한국은행 API로 기준금리 인상일을 가져오는 함수."""
    stats_code, item_code = "722Y001", "0001000"
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=5*365)).strftime('%Y%m%d')
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/{stats_code}/DD/{start_date}/{end_date}/{item_code}"
    
    try:
        response = requests.get(url)
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

def execute_askfin_query(intent_json):
    """LLM이 변환한 JSON을 받아 실제 데이터 분석을 수행하는 핵심 로직 함수."""
    print(f"분석 시작: {intent_json}")

    # 포괄적인 타겟 키워드 목록을 정의합니다.
    GENERIC_TARGETS = {"주식", "종목", "급등주", "우량주", "인기주", "전체"}

    condition = intent_json.get("condition")
    target = intent_json.get("target")
    period = intent_json.get("period")
    
    analysis_subject = ""
    
    print("KOSPI 및 KOSDAQ 종목 목록 로딩 중...")
    krx = fdr.StockListing('KRX')
    print("종목 목록 로딩 완료.")

    # target이 명확할 때만 필터링하고, 아니면 전체 목록을 사용합니다.
    if target and target.strip() and target not in GENERIC_TARGETS:
        keyword = target.replace(" 관련주", "").replace("주", "")
        target_stocks = krx[krx['Name'].str.contains(keyword, na=False)]
        analysis_subject = f"'{target}'"
    else:
        target_stocks = krx
        analysis_subject = "시장 전체"
    
    if target_stocks.empty:
        return {"query_intent": intent_json, "result": [f"'{target}'에 해당하는 종목을 찾을 수 없습니다."]}

    result_data = []

    # 1. '금리 인상' 같은 특정 조건이 있을 경우
    if condition and "금리" in condition:
        bok_api_key = os.getenv("ECOS_API_KEY")
        if not bok_api_key: return {"error": "한국은행 API 키가 설정되지 않았습니다."}
        
        event_dates = get_interest_rate_hike_dates(bok_api_key)
        if not event_dates: return {"query_intent": intent_json, "result": ["금리 인상 기록이 없습니다."]}
        
        print(f"금리 인상일({len(event_dates)}개) 기준으로 분석 중...")
        for code, name in zip(target_stocks['Code'], target_stocks['Name']):
            is_matched_all_events = True
            event_returns = []
            for event_date in event_dates:
                try:
                    df = fdr.DataReader(code, event_date, event_date + timedelta(days=10))
                    if len(df) > 5 and df['Close'].iloc[5] > df['Close'].iloc[0]:
                        period_return = (df['Close'].iloc[5] / df['Close'].iloc[0] - 1) * 100
                        event_returns.append(period_return)
                    else:
                        is_matched_all_events = False; break
                except Exception:
                    is_matched_all_events = False; break
            
            if is_matched_all_events and event_returns:
                avg_return = statistics.mean(event_returns)
                volatility = statistics.stdev(event_returns) if len(event_returns) > 1 else 0.0
                result_data.append({"name": name, "code": code, "average_return_pct": round(avg_return, 2), "volatility_pct": round(volatility, 2)})

    # 2. 특정 조건 없이 기간만 주어졌을 경우
    else:
        start_date, end_date = parse_period(period)
        print(f"{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')} 기간 분석 중...")
        
        # 성능을 위해 target_stocks를 작은 단위로 잘라서 처리 (예: 100개씩)
        for code, name in zip(target_stocks['Code'][:100], target_stocks['Name'][:100]): # 상위 100개만 테스트
            try:
                df = fdr.DataReader(code, start_date, end_date)
                if len(df) > 1:
                    total_return = (df['Close'].iloc[-1] / df['Close'].iloc[0] - 1) * 100
                    volatility = df['Close'].pct_change().std() * 100
                    result_data.append({"name": name, "code": code, "total_return_pct": round(total_return, 2), "volatility_pct": round(volatility, 2)})
            except Exception as e:
                continue

    # 정렬 키를 경우에 따라 다르게 설정
    sort_key = 'average_return_pct' if (condition and "금리" in condition) else 'total_return_pct'
    sorted_result = sorted(result_data, key=lambda x: x.get(sort_key, 0), reverse=True)

    return {
        "query_intent": intent_json,
        "analysis_subject": analysis_subject,
        "result": sorted_result[:20] if sorted_result else ["조건을 만족하는 종목이 없습니다."]
    }


@askfin_bp.route('/')
def askfin_page():
    return render_template('askfin.html')

@askfin_bp.route('/analyze', methods=['POST'])
def analyze_query():
    if not model:
        return jsonify({"error": "모델이 초기화되지 않았습니다. API 키를 확인하세요."}), 500

    data = request.get_json()
    if not data or 'query' not in data:
        return jsonify({"error": "잘못된 요청입니다. 'query' 필드를 포함해주세요."}), 400
    
    user_query = data['query']
    
    try:
        prompt = PROMPT_TEMPLATE.format(user_query=user_query)
        response = model.generate_content(prompt)
        cleaned_response = response.text.strip().replace("```json", "").replace("```", "").strip()
        intent_json = json.loads(cleaned_response)

        final_result = execute_askfin_query(intent_json)
        
        return jsonify(final_result)
    
    except Exception as e:
        print("="*30, "\n!!! AN ERROR OCCURRED IN /analyze !!!")
        traceback.print_exc()
        print("="*30)
        return jsonify({"error": f"분석 중 오류 발생: {str(e)}"}), 500