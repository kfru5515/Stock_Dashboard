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

from fuzzywuzzy import fuzz, process

# --- Global Caches for Initial Loading ---
GLOBAL_KRX_LISTING = None
GLOBAL_TICKER_NAME_MAP = None 
GLOBAL_NAME_TICKER_MAP = None
ANALYSIS_CACHE = {} 
GLOBAL_SECTOR_MASTER_DF = None 
GLOBAL_STOCK_SECTOR_MAP = None 
STOCK_DETAIL_CACHE = {} 
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
You are a financial analyst. Your primary task is to analyze a user's query and convert it into a structured JSON object.

- You MUST respond with a JSON object that follows the schema.
- **EXCEPTION**: If the user's query is a general question, a greeting, or something that CANNOT be structured into the JSON schema, you MUST respond with a conversational, friendly answer in plain text INSTEAD of JSON.
- For "comparison_analysis", the "target" MUST be an array of strings.
- Be specific with the "period" value. If the user says "이번주", use "이번주". If they say "지난 1분기", use "지난 1분기".

## JSON Schema:
{{"query_type": "string", "period": "string|null", "condition": "string|object|null", "target": "string|array|null", "action": "string|null"}}

## Examples (JSON Output):

# --- 기본 예시 ---
1.  User Query: "지난 3년 동안 겨울에 오른 콘텐츠 관련 주식"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "지난 3년", "condition": "겨울", "target": "콘텐츠 관련주", "action": "오른 주식"}}
    ```
2.  User Query: "최근 CPI 지수 알려줘"
    JSON Output:
    ```json
    {{"query_type": "indicator_lookup", "period": "최근", "condition": null, "target": "CPI 지수", "action": "조회"}}
    ```
3.  User Query: "인공지능, 2차전지 중 지난 1년간 가장 많이 오른 테마는?"
    JSON Output:
    ```json
    {{"query_type": "comparison_analysis", "period": "지난 1년간", "condition": null, "target": ["인공지능", "2차전지"], "action": "가장 많이 오른 테마"}}
    ```

# --- 구체적인/단기 기간 예시 ---
4.  User Query: "이번주 가장 많이 오른 주식은 뭐야?"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "이번주", "condition": null, "target": "주식", "action": "가장 많이 오른 주식"}}
    ```
5.  User Query: "오늘 제일 많이 내린 반도체주는?"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "오늘", "condition": null, "target": "반도체주", "action": "제일 많이 내린 주식"}}
    ```

# --- 분기/실적 및 재무지표 조건 예시 ---
6.  User Query: "올해 1분기 실적이 좋았던 IT 주식 찾아줘"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "올해 1분기", "condition": {{"type": "earnings", "performance": "good"}}, "target": "IT 주식", "action": "찾아줘"}}
    ```
7.  User Query: "PBR이 1보다 낮은 우량주 알려줘"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": null, "condition": {{"type": "fundamental", "indicator": "PBR", "operator": "<", "value": 1}}, "target": "우량주", "action": "알려줘"}}
    ```

# --- 단일 종목 현재가 조회 예시 ---
8.  User Query: "삼성전자 지금 얼마야?"
    JSON Output:
    ```json
    {{"query_type": "single_stock_price", "period": null, "condition": null, "target": "삼성전자", "action": "현재가 조회"}}
    ```
9.  User Query: "한화오션 주가 알려줄래"
    JSON Output:
    ```json
    {{"query_type": "single_stock_price", "period": null, "condition": null, "target": "한화오션", "action": "현재가 조회"}}
    ```
# --- ▼▼▼ [추가] 코스닥 종목 예시 ▼▼▼ ---
10. User Query: "에코프로비엠 현재 주가"
    JSON Output:
    ```json
    {{"query_type": "single_stock_price", "period": null, "condition": null, "target": "에코프로비엠", "action": "현재가 조회"}}
    ```
11. User Query: "셀트리온제약 주가"
    JSON Output:
    ```json
    {{"query_type": "single_stock_price", "period": null, "condition": null, "target": "셀트리온제약", "action": "현재가 조회"}}
    ```
12. User Query: "카카오게임즈 얼마에요?"
    JSON Output:
    ```json
    {{"query_type": "single_stock_price", "period": null, "condition": null, "target": "카카오게임즈", "action": "현재가 조회"}}
    ```

13. User Query: "오늘 거래량이 가장 많이 터진 주식은?"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "오늘", "condition": {{"type": "volume", "level": "highest"}}, "target": "주식", "action": "거래량이 가장 많이 터진 주식"}}
    ```
14. User Query: "금리 인상기에 가장 많이 올랐던 은행주는?"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": "금리 인상기", "condition": null, "target": "은행주", "action": "가장 많이 올랐던 주식"}}
    ```
15. User Query: "배당수익률 높은 통신주 알려줘"
    JSON Output:
    ```json
    {{"query_type": "stock_analysis", "period": null, "condition": {{"type": "fundamental", "indicator": "dividend_yield", "operator": ">", "value": "high"}}, "target": "통신주", "action": "배당수익률 높은 주식"}}
    ```
16. User Query: "가장 많이 오른 테마들 나열해줘"
    JSON Output:
    ```json
    {{"query_type": "theme_ranking", "period": null, "condition": null, "target": "테마", "action": "가장 많이 오른 테마"}}
    ```

## Example (Plain Text Output):

1.  User Query: "주식 초보인데 어떤 종목이 좋아?"
    Plain Text Output: 주식 투자를 처음 시작하시는군요! 특정 종목을 추천해드리기보다는, 먼저 시가총액이 크고 모두가 잘 아는 우량주부터 소액으로 시작해보시는 것을 권해드려요. 삼성전자나 SK하이닉스 같은 종목들의 최근 뉴스와 리포트를 꾸준히 살펴보시는 것도 좋은 공부가 될 거예요.
2.  User Query: "3년 뒤에 삼성전자 주가 얼마일까?"
    Plain Text Output: 미래의 주가를 정확히 예측하는 것은 불가능합니다. 주가는 수많은 경제 지표, 시장 상황, 기업의 실적 등에 따라 변동하기 때문입니다. 다만, 기업의 재무 상태나 성장 가능성을 분석하며 장기적인 관점에서 투자 결정을 내리는 것이 좋습니다.
3.  User Query: "단타랑 장기투자 중에 뭐가 더 나아?"
    Plain Text Output: 단타와 장기투자는 각자의 장단점이 있어 어느 한쪽이 절대적으로 낫다고 말하기는 어렵습니다. 단타는 빠른 수익을 기대할 수 있지만 높은 위험과 스트레스를 동반하며, 장기투자는 안정적이지만 수익을 보기까지 오랜 시간이 걸릴 수 있습니다. 본인의 투자 성향과 목표에 맞는 방법을 선택하는 것이 가장 중요합니다.

4.  User Query: "PER이 뭐야?"
    Plain Text Output: PER(주가수익비율)은 주가를 주당순이익(EPS)으로 나눈 값으로, 기업이 벌어들이는 이익에 비해 주가가 높게 혹은 낮게 평가되었는지를 나타내는 대표적인 투자 지표입니다. PER이 낮을수록 주가가 저평가되었다고 해석하는 경우가 많습니다.
5.  User Query: "안녕하세요"
    Plain Text Output: 안녕하세요! 금융 분석 AI입니다. 주식이나 경제에 대해 궁금한 점이 있으시면 무엇이든 물어보세요.

## Task:
User Query: "{user_query}"
Your Output:
"""

except Exception as e:
    print(f"AskFin Blueprint: 모델 초기화 실패 - {e}")
    model = None

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

        GLOBAL_KRX_LISTING['FullCode'] = GLOBAL_KRX_LISTING.apply(
            lambda row: f"{row['Code']}.KQ" if row['Market'] == 'KOSDAQ' else f"{row['Code']}.KS", axis=1
        )
        print(f"  - GLOBAL_KRX_LISTING에 'FullCode' 컬럼 추가 완료.")

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

def analyze_institutional_buying(start_date, end_date):
    """
    주어진 기간 동안 기관의 순매수 대금을 기준으로 상위 종목을 분석합니다.
    """
    print(f"DEBUG: {start_date} ~ {end_date} 기간의 기관 순매수 분석을 시작합니다.")
    try:
        df_kospi = stock.get_market_trading_value_by_date(start_date, end_date, "KOSPI")
        df_kosdaq = stock.get_market_trading_value_by_date(start_date, end_date, "KOSDAQ")
        
        df_all = pd.concat([df_kospi, df_kosdaq]).reset_index()

        if '기관계' in df_all.columns:
            df_all.rename(columns={'기관계': '기관'}, inplace=True)

        if '기관' not in df_all.columns:
            print("DEBUG: 투자자별 거래대금 데이터에 '기관' 컬럼이 없습니다.")
            return []

        institutional_net_buy = df_all.groupby('티커')['기관'].sum().sort_values(ascending=False)
        
        top_stocks = institutional_net_buy.head(50).reset_index()
        
        if GLOBAL_TICKER_NAME_MAP is None:
            initialize_global_data()
            
        analysis_results = []
        for index, row in top_stocks.iterrows():
            ticker = row['티커']
            net_buy_value = row['기관']
            
            analysis_results.append({
                "code": ticker,
                "name": GLOBAL_TICKER_NAME_MAP.get(ticker, "N/A"),
                "value": round(net_buy_value / 1_0000_0000, 2), # 억 단위로 변환
                "label": "기관 순매수(억 원)",
            })
        
        print(f"DEBUG: 기관 순매수 상위 {len(analysis_results)}개 종목 분석 완료.")
        return analysis_results

    except Exception as e:
        print(f"기관 순매수 분석 중 오류 발생: {e}")
        traceback.print_exc()
        return []

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

def execute_comparison_analysis(intent_json):
    """
    [수정됨] 여러 테마를 비교 분석하여 결과를 HTML 테이블로 생성하는 함수.
    """
    try:
        targets = intent_json.get("target", [])
        period_str = intent_json.get("period")
        action_str = intent_json.get("action", "")

        if not isinstance(targets, list) or len(targets) < 2:
            return {"error": "비교 분석을 위해서는 두 개 이상의 대상이 필요합니다."}

        start_date, end_date = parse_period(period_str)
        analysis_period_info = f"{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}"
        
        comparison_results = []

        for theme in targets:
            target_stocks, _, _ = get_target_stocks(theme)
            if target_stocks.empty:
                continue

            performance_data = analyze_top_performers(target_stocks, [(start_date, end_date)], (start_date, end_date))
            if not performance_data:
                continue

            valid_returns = [item['value'] for item in performance_data if 'value' in item and pd.notna(item['value'])]
            if valid_returns:
                average_return = statistics.mean(valid_returns)
                comparison_results.append({"theme": theme, "average_return": round(average_return, 2)})

        if not comparison_results:
            return {"error": "요청하신 테마들의 수익률을 분석할 수 없었습니다."}

        reverse_sort = "내린" not in action_str
        sorted_results = sorted(comparison_results, key=lambda x: x['average_return'], reverse=reverse_sort)
        
        # --- HTML 테이블 구조로 직접 생성 ---
        table_html = '<div class="table-responsive"><table class="table table-sm table-hover financial-table">'
        table_html += '<thead><tr><th>순위</th><th>테마</th><th>주요 종목 평균 수익률</th></tr></thead><tbody>'
        for i, result in enumerate(sorted_results):
            table_html += f"<tr><td>{i+1}</td><td><strong>{result['theme']}</strong></td><td><strong>{result['average_return']:.2f}%</strong></td></tr>"
        table_html += '</tbody></table></div>'
        
        result_text = table_html
        result_text += f"<br><small><i>*분석 기간: {analysis_period_info}<br>*본 분석은 각 테마에 포함된 시가총액 상위 종목들을 기준으로 계산되었으며, 실제 수익률과 다를 수 있습니다. 이 정보는 투자 추천이 아니며, 참고 자료로만 활용하시기 바랍니다.</i></small>"

        return {
            "query_intent": intent_json,
            "analysis_subject": f"'{', '.join(targets)}' 테마 비교 분석",
            "result": [result_text]
        }
    except Exception as e:
        traceback.print_exc()
        return {"error": f"비교 분석 실행 중 오류 발생: {e}"}

def execute_theme_ranking(intent_json, page, user_query, cache_key=None):
    """
    [개선] 전체 테마 성과를 분석하고 캐시에 저장하여 페이지네이션을 지원하는 함수.
    """
    try:
        if cache_key and cache_key in ANALYSIS_CACHE and 'full_result' in ANALYSIS_CACHE[cache_key]:
            sorted_result = ANALYSIS_CACHE[cache_key]['full_result']
            analysis_subject = ANALYSIS_CACHE[cache_key]['analysis_subject']
            description = ANALYSIS_CACHE[cache_key]['description']
            print(f" CACHE HIT: 테마 랭킹 전체 결과 {len(sorted_result)}개를 사용합니다.")
        else:
            print(f" CACHE MISS: 새로운 전체 테마 분석을 시작합니다.")
            period_str = intent_json.get("period")
            action_str = intent_json.get("action", "")
            start_date, end_date = parse_period(period_str if period_str else "최근 1개월")
            analysis_period_info = f"{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}"
            
            themes_from_file = {}
            try:
                themes_file_path = os.path.join(os.path.dirname(__file__), '..', 'cache', 'themes.json')
                with open(themes_file_path, 'r', encoding='utf-8') as f:
                    themes_from_file = json.load(f)
            except Exception as e:
                return {"error": f"테마 목록 파일 로드 실패: {e}"}

            all_themes_results = []
            # --- [핵심 수정] 테마 수 제한 제거 ---
            for theme in themes_from_file.keys():
                target_stocks, _, _ = get_target_stocks(theme)
                if target_stocks.empty: continue

                performance_data = analyze_top_performers(target_stocks, [(start_date, end_date)], (start_date, end_date))
                if not performance_data: continue

                valid_returns = [item['value'] for item in performance_data if 'value' in item and pd.notna(item['value'])]
                if valid_returns:
                    average_return = statistics.mean(valid_returns)
                    all_themes_results.append({"theme": theme, "average_return": round(average_return, 2)})

            if not all_themes_results:
                return {"error": "전체 테마의 수익률을 분석할 수 없었습니다."}

            reverse_sort = "내린" not in action_str
            sorted_results = sorted(all_themes_results, key=lambda x: x['average_return'], reverse=reverse_sort)
            
            analysis_subject = "전체 테마 성과 순위"
            description = f"분석 기간: {analysis_period_info}"
            
            if not cache_key: cache_key = str(hash(user_query + str(intent_json)))
            ANALYSIS_CACHE[cache_key] = {
                'intent_json': intent_json, 
                'analysis_subject': analysis_subject,
                'description': description,
                'full_result': sorted_results
            }
        
        items_per_page = 20
        total_items = len(sorted_results)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        paginated_result = sorted_results[start_index:end_index]

        final_result_list = []
        for item in paginated_result:
            final_result_list.append({
                "name": item['theme'],
                "value": item['average_return'],
                "label": "평균 수익률(%)"
            })

        return {
            "query_intent": intent_json,
            "analysis_subject": analysis_subject,
            "description": description,
            "result": final_result_list,
            "pagination": { "current_page": page, "total_pages": total_pages, "total_items": total_items },
            "cache_key": cache_key
        }
    except Exception as e:
        traceback.print_exc()
        return {"error": f"테마 랭킹 분석 중 오류 발생: {e}"}
            

def execute_indicator_lookup(intent_json):
    """
    [최종 수정] 여러 소스의 경제 지표를 조회하고 챗봇 답변을 생성하는 메인 함수
    """
    target_query = intent_json.get("target", "").lower() # 사용자 쿼리의 대상 지표 (소문자로 변환하여 비교 용이)

    FDR_INDICATOR_MAP = {
        "환율": {"code": "USD/KRW", "name": "원/달러 환율"},
        "원달러환율": {"code": "USD/KRW", "name": "원/달러 환율"},
        "유가": {"code": "CL=F", "name": "WTI 국제 유가"},
        "wti": {"code": "CL=F", "name": "WTI 국제 유가"},
        "금값": {"code": "GC=F", "name": "금 선물"},
        "금가격": {"code": "GC=F", "name": "금 선물"},
        "미국채10년": {"code": "US10YT=X", "name": "미 10년물 국채 금리"},
        "미국10년국채": {"code": "US10YT=X", "name": "미 10년물 국채 금리"},
        "코스피": {"code": "KS11", "name": "코스피 지수"},
        "코스닥": {"code": "KQ11", "name": "코스닥 지수"},
    }
    
    BOK_INDICATOR_MAP = {
        "cpi": {"stats_code": "901Y001", "item_code": "0", "name": "소비자물가지수"},
        "소비자물가지수": {"stats_code": "901Y001", "item_code": "0", "name": "소비자물가지수"},
        "소비자물가": {"stats_code": "901Y001", "item_code": "0", "name": "소비자물가지수"}, # 추가
        "물가지수": {"stats_code": "901Y001", "item_code": "0", "name": "소비자물가지수"}, # 추가
        "물가": {"stats_code": "901Y001", "item_code": "0", "name": "소비자물가지수"}, # 추가

        "기준금리": {"stats_code": "722Y001", "item_code": "0001000", "name": "한국 기준금리"},
        "한국기준금리": {"stats_code": "722Y001", "item_code": "0001000", "name": "한국 기준금리"},
        "금리": {"stats_code": "722Y001", "item_code": "0001000", "name": "한국 기준금리"}, # 추가
    }

    for key, indicator_info in FDR_INDICATOR_MAP.items():
        if key in target_query or indicator_info['name'].lower() in target_query:
            result = _get_fdr_indicator(indicator_info, intent_json)
            if result and "error" not in result:
                return result
            return {
                "query_intent": intent_json,
                "analysis_subject": "지표 조회 실패",
                "result": [f"'{indicator_info['name']}' 지표 데이터를 가져오는 데 문제가 발생했습니다. 잠시 후 다시 시도해주세요."]
            }
    # 임의로 유사도 추가
    for key, indicator_info in BOK_INDICATOR_MAP.items():
        similarity_score = fuzz.ratio(target_query, key.lower())
        if similarity_score > 70: 
            similarity_score_name = fuzz.ratio(target_query, indicator_info['name'].lower())
            if similarity_score_name > 70 or similarity_score > 70:
                result = _get_bok_indicator(indicator_info, intent_json)
                if result and "error" not in result:
                    return result
            return {
                "query_intent": intent_json,
                "analysis_subject": "지표 조회 실패",
                "result": [f"'{indicator_info['name']}' 지표 데이터를 가져오는 데 문제가 발생했습니다. 잠시 후 다시 시도해주세요."]
            }

    return {
        "query_intent": intent_json,
        "analysis_subject": "지표 조회 실패",
        "result": [f"요청하신 '{intent_json.get('target', '지표')}'는 지원하지 않는 지표이거나 데이터를 찾을 수 없습니다. (지원 지표: 환율, 유가, 금값, 미국채10년, 코스피, 코스닥, CPI, 기준금리)"]
    }




@askfin_bp.route('/stock/<code>/profile')
def get_stock_profile(code):
    """
    [수정] DART API를 사용하여 '주요 공시' 목록을 조회하도록 변경합니다.
    """
    response_data = {}
    company_name = None
    now = time.time()
    #  12시간(43200초)이 지나지 않았으면 캐시된 데이터 사용
    if code in STOCK_DETAIL_CACHE and (now - STOCK_DETAIL_CACHE[code]['timestamp'] < 43200):
        print(f"✅ CACHE HIT: 종목코드 '{code}'의 상세 정보를 캐시에서 반환합니다.")
        return jsonify(STOCK_DETAIL_CACHE[code]['data'])

    print(f"🔥 CACHE MISS: 종목코드 '{code}'의 상세 정보를 API를 통해 새로 조회합니다.")

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
    if "error" not in response_data:
        STOCK_DETAIL_CACHE[code] = {
            'data': response_data,
            'timestamp': now
        }
        
    return jsonify(response_data)

def get_target_stocks(target_str):
    """
    [개선] FuzzyWuzzy와 Sector(업종) 기반 검색, 그리고 종목명 직접 검색 로직 강화
    """
    global GLOBAL_KRX_LISTING
    if GLOBAL_KRX_LISTING is None:
        initialize_global_data()
        if GLOBAL_KRX_LISTING is None:
            return pd.DataFrame(), "초기화 실패", None

    krx = GLOBAL_KRX_LISTING
    analysis_subject = "시장 전체"
    disambiguation_candidates = None
    GENERIC_TARGETS = {"주식", "종목", "급등주", "우량주", "인기주", "전체"}
    
    if not target_str or target_str.strip() in GENERIC_TARGETS:
        return krx, analysis_subject, disambiguation_candidates

    keyword = target_str.replace(" 관련주", "").replace(" 테마주", "").replace(" 테마", "").replace("주", "").strip()

    # 1. 테마 파일에서 Fuzzy Matching
    try:
        themes_file_path = os.path.join(os.path.dirname(__file__), '..', 'cache', 'themes.json')
        with open(themes_file_path, 'r', encoding='utf-8') as f:
            themes_from_file = json.load(f)
        
        best_match = process.extractOne(keyword, themes_from_file.keys(), scorer=fuzz.token_sort_ratio)
        if best_match and best_match[1] > 80:
            matched_theme_name = best_match[0]
            target_codes = [stock.get('code') for stock in themes_from_file[matched_theme_name] if stock.get('code')]
            target_stocks = krx[krx['Code'].isin(target_codes)]
            if not target_stocks.empty:
                analysis_subject = f"'{matched_theme_name}' 테마"
                return target_stocks, analysis_subject, None
    except Exception as e:
        print(f"경고: 'themes.json' 파일 처리 중 오류: {e}")

    # 2. Sector(업종) 기반 검색
    if 'Sector' in krx.columns:
        sector_matches = krx[krx['Sector'].str.contains(keyword, na=False)]
        if not sector_matches.empty:
            analysis_subject = f"'{keyword}' 업종"
            return sector_matches, analysis_subject, None

    # 3. 종목명 직접 검색
    partial_matches = krx[krx['Name'].str.contains(keyword, na=False)]
    if not partial_matches.empty:
        analysis_subject = f"'{keyword}' 포함 종목"
        return partial_matches, analysis_subject, None
    
    return pd.DataFrame(), f"'{target_str}'", None


def parse_period(period_str):
    """'지난 3년간' 같은 문자열을 시작일과 종료일로 변환하는 함수 (기능 확장)"""
    today = datetime.now()
    if not period_str:
        return today - timedelta(days=365), today # 기본값: 최근 1년

    try:
        if "오늘" in period_str:
            return today - timedelta(days=1), today
        if "어제" in period_str:
            yesterday = today - timedelta(days=1)
            return yesterday.replace(hour=0, minute=0, second=0, microsecond=0), yesterday.replace(hour=23, minute=59, second=59)
        if "이번주" in period_str:
            start_of_week = today - timedelta(days=today.weekday()) # 이번 주 월요일
            return start_of_week, today
        if "지난주" in period_str:
            end_of_last_week = today - timedelta(days=today.weekday() + 1)
            start_of_last_week = end_of_last_week - timedelta(days=6)
            return start_of_last_week.replace(hour=0, minute=0), end_of_last_week.replace(hour=23, minute=59)
        if "지난 달" in period_str or "지난달" in period_str:
            first_day_of_current_month = today.replace(day=1)
            last_day_of_last_month = first_day_of_current_month - timedelta(days=1)
            first_day_of_last_month = last_day_of_last_month.replace(day=1)
            return first_day_of_last_month, last_day_of_last_month

        if "분기" in period_str:
            quarter_match = re.search(r'(\d)분기', period_str)
            if quarter_match:
                quarter = int(quarter_match.group(1))
                year = today.year
                if "작년" in period_str:
                    year -= 1
                
                start_month = 3 * quarter - 2
                end_month = 3 * quarter
                start_date = datetime(year, start_month, 1)
                # 다음 달의 첫날에서 하루를 빼서 마지막 날을 구함
                if end_month == 12:
                    end_date = datetime(year, 12, 31)
                else:
                    end_date = datetime(year, end_month + 1, 1) - timedelta(days=1)
                return start_date, end_date

        # --- 기존 로직 (일/개월/년) ---
        if "일" in period_str:
            days_match = re.search(r'(\d+)', period_str)
            if days_match:
                days = int(days_match.group(0))
                return today - timedelta(days=days), today
        if "개월" in period_str:
            months_match = re.search(r'(\d+)', period_str)
            if months_match:
                months = int(months_match.group(0))
                return today - timedelta(days=30 * months), today
        if "년간" in period_str or "년" in period_str:
            years_match = re.search(r'(\d+)', period_str)
            if years_match:
                years = int(years_match.group(0))
                return today - timedelta(days=365 * years), today

    except (ValueError, TypeError, AttributeError):
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

def execute_single_stock_price(intent_json):
    """
    [완전판] 띄어쓰기 대응, 그래프 데이터 생성 기능이 모두 포함된 버전
    """
    try:
        if GLOBAL_NAME_TICKER_MAP is None:
            initialize_global_data()

        target_name = intent_json.get("target")
        if not target_name:
            return {"error": "종목명이 지정되지 않았습니다."}

        cleaned_name = target_name.replace(" ", "")
        ticker = GLOBAL_NAME_TICKER_MAP.get(cleaned_name)
        
        if not ticker:
            ticker = GLOBAL_NAME_TICKER_MAP.get(target_name) 
            if not ticker:
                return {
                    "analysis_subject": "오류",
                    "result": [f"'{target_name}'에 해당하는 종목을 찾을 수 없습니다. 종목명을 확인해주세요."]
                }

        latest_bday_dt = datetime.strptime(stock.get_nearest_business_day_in_a_week(), '%Y%m%d')
        start_date_for_chart = (latest_bday_dt - timedelta(days=45)).strftime('%Y%m%d')
        latest_bday_str = latest_bday_dt.strftime('%Y%m%d')

        df = stock.get_market_ohlcv_by_date(fromdate=start_date_for_chart, todate=latest_bday_str, ticker=ticker)

        if df.empty:
            return {
                "analysis_subject": "정보 없음",
                "result": [f"'{target_name}'의 거래 정보를 찾을 수 없습니다."]
            }

        chart_data = []
        df_for_chart = df.reset_index()
        for index, row in df_for_chart.iterrows():
            chart_data.append({
                "date": row['날짜'].strftime('%Y-%m-%d'),
                "price": row['종가']
            })

        stock_info = df.iloc[-1]
        current_price = stock_info['종가']
        change = stock_info['종가'] - stock_info['시가']
        
        price_color_class = "price-up" if change > 0 else "price-down" if change < 0 else ""
        change_str = f"{abs(change):,}원 상승" if change > 0 else f"{abs(change):,}원 하락" if change < 0 else "변동 없음"
        date_str = df.index[-1].strftime('%Y-%m-%d')
        
        result_sentence = (
            f"{target_name}({ticker})의 가장 최근 종가({date_str})는 "
            f"<span class='{price_color_class}'>{current_price:,}원</span>이며, 시가 대비 <span class='{price_color_class}'>{change_str}</span>했습니다."
        )

        return {
            "query_intent": intent_json,
            "analysis_subject": f"{target_name} 현재가",
            "result": [result_sentence],
            "chart_data": chart_data,
            "stock_code": ticker,
            "stock_name": target_name
        }
    except Exception as e:
        traceback.print_exc()
        return {"error": f"단일 종목 가격 조회 중 오류 발생: {e}"}
        
def execute_stock_analysis(intent_json, page, user_query, cache_key=None):
    """
    [개선] 재무 지표 필터링 및 캐싱 로직 강화
    """
    try:
        action_str = intent_json.get("action", "")
        
        if cache_key and cache_key in ANALYSIS_CACHE and 'full_result' in ANALYSIS_CACHE[cache_key]:
            sorted_result = ANALYSIS_CACHE[cache_key]['full_result']
            analysis_subject = ANALYSIS_CACHE[cache_key]['analysis_subject']
            print(f" CACHE HIT: 캐시된 전체 결과 {len(sorted_result)}개를 사용합니다.")
        else:
            print(f" CACHE MISS: 새로운 분석을 시작합니다.")
            target_str = intent_json.get("target")
            condition_obj = intent_json.get("condition")
            target_stocks, analysis_subject, disambiguation_candidates = get_target_stocks(target_str)
            
            if disambiguation_candidates:
                return {"analysis_subject": f"'{target_str}' 종목 명확화 필요", "result_type": "disambiguation", "candidates": disambiguation_candidates, "result": []}

            if target_stocks.empty:
                return {"analysis_subject": analysis_subject, "result": [f"{analysis_subject}에 해당하는 종목을 찾을 수 없습니다."]}

            if isinstance(condition_obj, dict) and condition_obj.get("type") == "fundamental":
                today_str = stock.get_nearest_business_day_in_a_week()
                funda_df = stock.get_market_fundamental(today_str, market="ALL")
                target_stocks = pd.merge(target_stocks, funda_df, left_on='Code', right_index=True, how='inner')
                if not target_stocks.empty:
                    indicator = condition_obj.get("indicator", "").upper()
                    operator = condition_obj.get("operator")
                    value = float(condition_obj.get("value"))
                    if indicator in target_stocks.columns:
                        if operator == "<": target_stocks = target_stocks[target_stocks[indicator] < value]
                        elif operator == ">": target_stocks = target_stocks[target_stocks[indicator] > value]
                        analysis_subject += f" ({indicator} {operator} {value})"

            if target_stocks.empty: return {"analysis_subject": analysis_subject, "result": ["조건을 만족하는 종목이 없습니다."]}

            start_date, end_date = parse_period(intent_json.get("period"))
            
            result_data = analyze_top_performers(target_stocks, [(start_date, end_date)], (start_date, end_date))
            reverse_sort = False if "내린" in action_str else True
            sorted_result = sorted(result_data, key=lambda x: x.get('value', -99999), reverse=reverse_sort)
            
            if not cache_key: cache_key = str(hash(user_query + str(intent_json)))
            ANALYSIS_CACHE[cache_key] = {'intent_json': intent_json, 'analysis_subject': analysis_subject, 'full_result': sorted_result}
            print(f"새로운 분석 결과 {len(sorted_result)}개를 캐시에 저장했습니다. (키: {cache_key})")
        
        items_per_page = 20
        total_items = len(sorted_result)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        paginated_result = sorted_result[start_index:end_index]

        return {
            "query_intent": intent_json,
            "analysis_subject": analysis_subject,
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

def _fetch_and_analyze_single_stock(stock_code, stock_name, overall_start, overall_end, event_periods):
    """
    단일 종목의 전체 기간 데이터를 조회하고, 그 안에서 이벤트 기간 수익률을 분석하는 헬퍼 함수 (병렬 처리를 위함)
    """
    try:
        print(f"       데이터 조회 시작: {stock_name}({stock_code})")
        overall_prices = fdr.DataReader(stock_code, overall_start, overall_end)

        if overall_prices.empty:
            print(f"       -> [분석 실패] {stock_name}({stock_code}): fdr.DataReader가 빈 데이터를 반환했습니다.")
            return None

        print(f"       -> [데이터 확인] {stock_name}({stock_code}): 전체 기간({overall_start.strftime('%Y-%m-%d')}~{overall_end.strftime('%Y-%m-%d')}) 데이터 {len(overall_prices)}개 로드 성공.")
        print(f"       -> [이벤트 기간 확인] 분석할 이벤트 기간 수: {len(event_periods)}개, 첫 기간: {event_periods[0] if event_periods else 'N/A'}")

        start_price = int(overall_prices['Open'].iloc[0])
        end_price = int(overall_prices['Close'].iloc[-1])

        period_returns = []
        for i, (start, end) in enumerate(event_periods):
            start_ts = pd.to_datetime(start)
            end_ts = pd.to_datetime(end)

            prices_in_period = overall_prices.loc[start_ts:end_ts]
            print(f"       -> 이벤트 기간 {i+1} ({start_ts.date()}~{end_ts.date()}) 데이터 슬라이싱 결과: {len(prices_in_period)}개")

            if len(prices_in_period) > 1:
                event_start_price = prices_in_period['Open'].iloc[0]
                event_end_price = prices_in_period['Close'].iloc[-1]
                if event_start_price > 0:
                    period_returns.append((event_end_price / event_start_price) - 1)

        if not period_returns:
            print(f"       -> [분석 실패] {stock_name}({stock_code}): 유효한 수익률을 계산할 수 있는 이벤트 기간이 없습니다.")
            return None

        average_return = statistics.mean(period_returns)
        if pd.notna(average_return):
            return {
                "code": stock_code, "name": stock_name,
                "value": round(average_return * 100, 2), "label": "평균 수익률(%)",
                "start_price": start_price,
                "end_price": end_price,
            }
    except Exception as e:
        print(f"       -> [분석 실패] {stock_name}({stock_code}) 분석 중 예외 발생: {e}")

    return None

def analyze_top_performers(target_stocks, event_periods, overall_period):
    """
    [성능 최적화] 전체 기간 데이터를 한 번에 조회 후, 메모리에서 조건 기간을 슬라이싱하여 분석 속도를 개선합니다.
    또한, 여러 종목의 데이터 조회를 병렬로 처리합니다.
    """
    analysis_results = []
    
    try:
        print("DEBUG: nlargest 실행 전, target_stocks 정보:")
        target_stocks.info() 
        
        top_stocks = target_stocks.nlargest(min(len(target_stocks), 50), 'Marcap').reset_index(drop=True)

    except Exception as e:

        print(target_stocks)
        return [] 

    print(f"시가총액 상위 {len(top_stocks)}개 종목에 대한 수익률 분석을 시작합니다...")
    overall_start, overall_end = overall_period

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
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
                print(f"   ({i + 1}/{len(top_stocks)}) {stock_name}({stock_code}) 분석 완료.")
            except Exception as exc:
                print(f"   - {stock_name}({stock_code}) 분석 중 예외 발생: {exc}")
    
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
def analyze_top_volume_stocks(date_str):
    """
    주어진 날짜의 거래량 상위 종목을 분석하여 반환합니다.
    """
    print(f"DEBUG: {date_str} 기준 거래량 상위 종목 분석을 시작합니다.")
    try:
        # KOSPI와 KOSDAQ 시장의 전체 시세 정보를 가져옵니다.
        df_all = stock.get_market_ohlcv(date_str, market="ALL")

        if '거래량' not in df_all.columns:
            print("DEBUG: OHLCV 데이터에 '거래량' 컬럼이 없습니다.")
            return []

        # 거래량을 기준으로 내림차순 정렬하고 상위 50개 선택
        top_stocks = df_all.sort_values(by='거래량', ascending=False).head(50)
        
        if GLOBAL_TICKER_NAME_MAP is None:
            initialize_global_data()
            
        analysis_results = []
        for ticker, row in top_stocks.iterrows():
            analysis_results.append({
                "code": ticker,
                "name": GLOBAL_TICKER_NAME_MAP.get(ticker, "N/A"),
                "value": int(row['거래량']),
                "label": "거래량",
                "end_price": int(row['종가']),
                "start_price": int(row['시가']) # 테이블 형식 통일을 위해 추가
            })
        
        print(f"DEBUG: 거래량 상위 {len(analysis_results)}개 종목 분석 완료.")
        return analysis_results

    except Exception as e:
        print(f"거래량 분석 중 오류 발생: {e}")
        traceback.print_exc()
        return []
    

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

QUERY_HANDLERS = {
    "stock_analysis": execute_stock_analysis,
    "comparison_analysis": execute_comparison_analysis,
    "indicator_lookup": execute_indicator_lookup,
    "single_stock_price": execute_single_stock_price,
    "theme_ranking": execute_theme_ranking  
}

@askfin_bp.route('/')
def askfin_page():
    return render_template('askfin.html')

@askfin_bp.route('/analyze', methods=['POST'])
def analyze_query():
    if not model:
        return jsonify({"error": "모델이 초기화되지 않았습니다. API 키를 확인하세요."}), 500
    
    data = request.get_json()
    user_query = data.get('query')
    page = data.get('page', 1)
    cache_key = data.get('cache_key')

    if not user_query:
        return jsonify({"error": "잘못된 요청입니다."}), 400

    raw_text = ""
    try:
        if cache_key and cache_key in ANALYSIS_CACHE:
            print(f"✅ CACHE HIT: 캐시된 분석 결과를 사용합니다. (키: {cache_key})")
            intent_json = ANALYSIS_CACHE[cache_key]['intent_json']
            query_type = intent_json.get("query_type") # 캐시에서 query_type 가져오기
            handler = QUERY_HANDLERS.get(query_type)
            if handler:
                 # 캐시를 사용할 때는 페이지 정보만 넘겨서 재계산
                 return jsonify(handler(intent_json, page, user_query, cache_key))
        
        print(f"🔥 CACHE MISS: '{user_query}'에 대해 Gemini API 분석을 요청합니다.")
        prompt = PROMPT_TEMPLATE.format(user_query=user_query)
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        try:
            start = raw_text.find('{')
            end = raw_text.rfind('}') + 1
            cleaned_response = raw_text[start:end]
            intent_json = json.loads(cleaned_response)
        except (json.JSONDecodeError, IndexError):
            print("DEBUG: JSON 파싱 실패. 일반 텍스트로 처리합니다.")
            return jsonify({"analysis_subject": "일반 답변", "result": [raw_text.replace('\n', '<br>')]} )

        query_type = intent_json.get("query_type")
        
        if not query_type and intent_json.get("target") and intent_json.get("condition"):
            query_type = "stock_analysis"
            intent_json["query_type"] = query_type
        
        handler = QUERY_HANDLERS.get(query_type)

        if handler:
            if query_type in ["stock_analysis", "theme_ranking"]:
                final_result = handler(intent_json, page, user_query)
            else:
                final_result = handler(intent_json)
        else:
            final_result = {"analysis_subject": "일반 답변", "result": [raw_text.replace('\n', '<br>')]}
        
        return jsonify(final_result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"분석 중 심각한 오류 발생: {str(e)}"}), 500
    
            
@askfin_bp.route('/new_chat', methods=['POST'])
def new_chat():
    """대화 기록(세션)을 초기화합니다."""
    session.pop('chat_history', None)
    return jsonify({"status": "success", "message": "새 대화를 시작합니다."})