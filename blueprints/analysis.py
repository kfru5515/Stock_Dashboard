# blueprints/analysis.py

from flask import Blueprint, render_template, request, current_app
import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import requests
import json # JSON 출력을 위해 추가
import traceback
from run import EnhancedStockPredictor

# .env 파일에서 환경 변수 로드
load_dotenv()

# 'analysis' 이름의 Blueprint 생성
analysis_bp = Blueprint('analysis', __name__, template_folder='../templates')

def get_stock_data(codes, period_days=365):
    # ... (수정 없음) ...
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)
    all_data = pd.DataFrame()
    for code in codes:
        try:
            df = fdr.DataReader(code, start_date, end_date)
            price_col = 'Adj Close' if 'Adj Close' in df.columns else 'Close'
            all_data[code] = df[price_col]
        except Exception as e:
            print(f"Error fetching data for {code}: {e}")
            continue
    return all_data


@analysis_bp.route('/quant-report')
def quant_report():
    """캐시된 퀀트 리포트 데이터를 사용하여 페이지를 렌더링합니다."""
    try:
        report_data = current_app.config.get('QUANT_REPORT_CACHE')

        if not report_data:
            return render_template('error.html', error_message="퀀트 리포트 데이터가 아직 준비되지 않았습니다. 서버 로그를 확인해주세요.")

        return render_template('quant_report.html', report=report_data, now=datetime.now())

    except Exception as e:
        print(f"퀀트 리포트 페이지 렌더링 중 오류: {e}")
        traceback.print_exc()
        return render_template('error.html', error_message="퀀트 리포트 페이지를 표시하는 중 오류가 발생했습니다.")


    
@analysis_bp.route('/analysis')
def analysis_page():
    # ... (한글 입력 처리 로직, 수정 없음) ...
    try:
        krx_list = fdr.StockListing('KRX')
    except Exception as e:
        print(f"KRX 종목 리스트를 불러오는 데 실패했습니다: {e}")
        krx_list = pd.DataFrame()

    name_to_code = pd.Series(krx_list.Code.values, index=krx_list.Name).to_dict()
    stock_names_or_codes_str = request.args.get('codes', '삼성전자,SK하이닉스')
    input_list = [item.strip() for item in stock_names_or_codes_str.split(',')]
    stock_codes = []
    for item in input_list:
        if item in name_to_code:
            stock_codes.append(name_to_code[item])
        elif not krx_list.empty and item in krx_list['Code'].values:
            stock_codes.append(item)
    stock_codes = sorted(list(set(stock_codes)))

    price_data = get_stock_data(stock_codes)

    if price_data.empty:
        context = {
            'error_message': '유효한 종목이 없거나 데이터를 가져올 수 없습니다.',
            'stock_codes_str': stock_names_or_codes_str,
        }
        return render_template('analysis.html', **context)
        
    krx_list.set_index('Code', inplace=True)
    stock_names = {code: krx_list.loc[code, 'Name'] for code in price_data.columns}

    cpi_available = False
    try:
        api_key = os.getenv("ECOS_API_KEY")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        start_str = start_date.strftime('%Y%m')
        end_str = end_date.strftime('%Y%m')
        
        url = (f"http://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/100/"
               f"901Y001/M/{start_str}/{end_str}/0")

        response = requests.get(url)
        response.raise_for_status()
        
        data = response.json()
        
        # --- ▼▼▼ 디버깅 코드 추가 ▼▼▼ ---
        # API 서버가 보낸 응답 전체를 예쁘게 출력해서 확인합니다.
        print("\n--- ECOS API 전체 응답 ---")
        print(json.dumps(data, indent=4, ensure_ascii=False))
        # --- ▲▲▲ 디버깅 코드 추가 ▲▲▲ ---

        # 응답 구조를 확인하고 데이터 추출
        if 'StatisticSearch' in data and 'row' in data['StatisticSearch']:
            cpi_data_raw = data['StatisticSearch']['row']
        else:
            # 에러 응답일 경우 빈 리스트로 초기화
            cpi_data_raw = []

        if cpi_data_raw:
            cpi_df = pd.DataFrame(cpi_data_raw)
            cpi_df = cpi_df[['TIME', 'DATA_VALUE']]
            cpi_df.rename(columns={'TIME': 'Date', 'DATA_VALUE': 'CPI'}, inplace=True)
            cpi_df['Date'] = pd.to_datetime(cpi_df['Date'], format='%Y%m')
            cpi_df.set_index('Date', inplace=True)
            cpi_df['CPI'] = cpi_df['CPI'].astype(float)

            cpi_daily = cpi_df.reindex(price_data.index).bfill().ffill()

            if not cpi_daily.isnull().values.any():
                normalized_cpi = cpi_daily / cpi_daily.iloc[0]
                cpi_available = True
        else:
            print("ECOS API에서 CPI 데이터를 가져오지 못했습니다. (row 리스트가 비어있거나 없음)")

    except requests.exceptions.RequestException as e:
        print(f"API 요청 실패: {e}")
        cpi_available = False
    except Exception as e:
        print(f"CPI 데이터 처리 중 오류 발생: {e}")
        cpi_available = False
    # ▲▲▲▲▲ requests 처리 완료 ▲▲▲▲▲

    # ... (이하 코드 동일) ...
    normalized_price = price_data / price_data.iloc[0]
    daily_returns = price_data.pct_change().dropna()
    correlation_matrix = daily_returns.corr().to_dict()
    chart_datasets = []
    colors = ['#4BC0C0', '#FF6384', '#36A2EB', '#FFCE56', '#9966FF']
    
    for i, code in enumerate(price_data.columns):
        dataset = {'label': stock_names.get(code, code), 'data': list(normalized_price[code]), 'borderColor': colors[i % len(colors)], 'fill': False, 'tension': 0.1}
        chart_datasets.append(dataset)

    if cpi_available:
        cpi_dataset = {'label': '소비자물가지수 (CPI)', 'data': list(normalized_cpi['CPI']), 'borderColor': '#FFA500', 'backgroundColor': 'rgba(255, 165, 0, 0.1)', 'fill': False, 'tension': 0.1, 'borderDash': [5, 5]}
        chart_datasets.append(cpi_dataset)

    context = {
        'stock_names': stock_names,
        'labels': [d.strftime('%Y-%m-%d') for d in normalized_price.index],
        'datasets': chart_datasets,
        'correlation_matrix': correlation_matrix,
        'stock_codes_str': stock_names_or_codes_str,
    }
    return render_template('analysis.html', **context)
