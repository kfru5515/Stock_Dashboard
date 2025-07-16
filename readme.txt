# 금융 대시보드 및 AI 분석 플랫폼
---

## 주요 기능

### 1. 대시보드 (Dashboard)
* **시장 현황:** KOSPI, KOSDAQ, USD/KRW 환율, WTI 국제 유가의 실시간(또는 근실시간) 현황 및 변동률을 차트로 시각화하여 제공합니다.
* **종목 순위:** KOSPI 및 KOSDAQ 시장의 거래량 및 거래대금 상위 10개 종목을 조회할 수 있습니다.
* **종목관련 정보: 상위 10개종목에 대한 관련 재무, 공시, 뉴스 등을 접근할수있습니다.

### 2. AI 대화형 금융 도우미 (AskFin)
* **자연어 질의 분석:** Google Gemini 모델을 활용하여 사용자의 금융 질문을 분석하고, `stock_analysis`, `indicator_lookup`, `factor_analysis`, `scenario_analysis`, `general_inquiry` 등 5가지 유형으로 분류합니다.
* **주식 분석:** 특정 기간, 조건(예: "겨울에 오른", "CPI 지수 3.5% 이상일 때")에 따른 종목의 상승률, 변동성 등을 분석하여 순위 형태로 제공합니다.
    * **개선:** 분석 결과가 0개일 경우, 일반 답변으로 폴백하여 사용자에게 더 친절한 설명을 제공합니다.
* **경제 지표 조회:** CPI, 기준금리, 환율, 유가 등 주요 경제 지표의 최신 데이터를 조회합니다.
    * **개선:** 지표 조회에 실패하거나 데이터가 부족할 경우, 일반 답변으로 폴백하여 사용자에게 안내합니다.
* **요인 분석 (Factor Analysis) - (고급 기능 시작):** (진행중) 
    * 특정 종목에 대한 가치(PER, PBR, PSR), 성장(매출액/영업이익/순이익 성장률), 모멘텀, 변동성, 퀄리티(ROE, 부채비율) 요인을 계산합니다.
    * **Gemini 기반 통찰력 생성:** 계산된 요인 데이터를 바탕으로 Gemini 모델이 해당 종목에 대한 투자 통찰력을 자연어 텍스트로 요약하여 제공합니다.
* **시나리오 분석 (Scenario Analysis) - (기능 확장 시작):**
    * "금리 인상 시나리오에서 은행주는 어떻게 될까?"와 같은 질문에 대해, 현재는 예시 답변을 제공하지만, 향후 실제 모델링을 통해 영향을 예측할 수 있도록 확장될 예정입니다.
* **일반 질문 답변:** 특정 분석 유형에 속하지 않는 일반적인 금융 질문(예: "요즘 국제 정세", "유행하는 테마주 추천")에 대해 Gemini 모델이 직접 답변합니다.
    * **개선:** 답변 텍스트의 줄바꿈(`\n`, `\r\n`)을 HTML `<br>` 태그로 변환하여 가독성을 높였습니다.
* **테마주 분류 고도화:**
    * `240107_전체 테마별 구성종목_6,680개.xlsx` 파일을 기반으로 생성된 `themes.json` 파일을 활용하여 테마별 종목을 분류합니다.
    * **개선:** `themes.json`의 종목 코드가 6자리 0-패딩 형식으로 통일되어 `FinanceDataReader` 데이터와 정확히 매칭됩니다.
    * **개선:** 사용자 질의 키워드와 `themes.json` 테마명 간의 매칭 로직이 강화되어, 부분 일치나 포함 관계에서도 테마를 유연하게 찾아냅니다.
* **페이지네이션:** 분석 결과가 많을 경우 페이지네이션을 통해 결과를 분할하여 표시합니다.
    * **개선:** 페이지 이동 시 캐시가 올바르게 작동하여, 동일한 쿼리에 대해 다시 데이터를 조회하지 않습니다.

### 3. 기업 상세 정보 모달
* 대시보드 또는 AskFin 분석 결과에서 종목명을 클릭하면, 해당 기업의 상세 정보 모달이 팝업됩니다.
* **제공 정보:** 기업 개요, 재무상태표, 손익계산서, 현금흐름표, 전체 재무정보, 주요 공시, 관련 뉴스.
* **개선:** AskFin 페이지에서 종목 클릭 시에도 대시보드와 동일한 상세 정보 모달과 데이터가 표시되도록 통일되었습니다.

---

## 기술 

* **백엔드:** Python, Flask
* **데이터 처리:** Pandas, NumPy
* **금융 데이터:**
    * `FinanceDataReader`: 주가, 지수, 환율, 원자재 데이터
    * `pykrx`: 한국 주식 시장 통계, 종목 정보
    * `dart_fss`: 금융감독원 전자공시시스템(DART) 데이터
    * `yfinance`: 해외 주식/원자재 데이터 (WTI)
* **AI 모델:** Google Gemini (GenerativeModel)
* **프런트엔드:** HTML, CSS, JavaScript, jQuery, Chart.js, Bootstrap
* **데이터베이스:** MySQL (SQLAlchemy를 통한 연동) 거의사용하지않음
* **환경 관리:** `python-dotenv` (환경 변수 관리)

---

## 실행 환경

### 1. 환경 설정

1.  **Python 설치:** Python 3.8 이상 버전을 설치합니다.
2.  **가상 환경 생성 및 활성화 (권장):**
    ```bash
    python -m venv python-env-311
    # Windows
    .\python-env-311\Scripts\activate
    # macOS/Linux
    source python-env-311/bin/activate
    ```
3.  **의존성 설치:**
    프로젝트 루트 디렉토리에서 다음 명령어를 실행하여 필요한 라이브러리들을 설치합니다.
    ```bash
    pip install -r requirements.txt
    # requirements.txt 파일이 없다면, 다음 패키지들을 직접 설치합니다:
    # Flask pandas numpy FinanceDataReader pykrx yfinance requests python-dotenv google-generativeai dart-fss pymysql sqlalchemy beautifulsoup4
    ```

### 2. API 키 설정

1.  **`.env` 파일 생성:**
    프로젝트의 최상위 디렉토리(예: `app.py`와 같은 위치)에 `.env` 파일을 생성합니다.
2.  **API 키 입력:**
    다음 내용을 `.env` 파일에 추가하고, `<YOUR_API_KEY>` 부분을 실제 발급받은 키로 대체합니다.
    ```env
    GOOGLE_AI_API_KEY=<YOUR_GEMINI_API_KEY>
    DART_API_KEY=<YOUR_DART_API_KEY>
    ECOS_API_KEY=<YOUR_BOK_ECOS_API_KEY>
    ```
    * **Google Gemini API Key:** Google AI Studio에서 발급
    * **DART API Key:** 금융감독원 DART Open API 서비스에서 발급
    * **ECOS API Key:** 한국은행 경제통계시스템(ECOS) Open API 서비스에서 발급

### 3. 데이터 파일 준비

1.  **`themes.json` 생성:** 
    * **`Theme.py` 스크립트 (최종 버전):**
        ```python
        import pandas as pd
        import json
        import os

        # 업로드된 Excel 파일 이름 (스크립트가 실행되는 위치 기준 상대 경로 또는 절대 경로)
        # 예시: 'cache' 폴더 안에 Excel 파일이 있다면
        excel_file_name = os.path.join(os.path.dirname(__file__), '240107_전체 테마별 구성종목_6,680개.xlsx')

        try:
            df = pd.read_excel(excel_file_name)
            print(f"'{excel_file_name}' 파일을 성공적으로 읽었습니다.")
            print("DataFrame의 첫 5행:")
            print(df.head())
            print("\nDataFrame의 컬럼명:")
            print(df.columns.tolist())

            ThemeNameColumn = '테마' 
            StockNameColumn = '종목명' 
            StockCodeColumn = '종목코드' 

            if (ThemeNameColumn not in df.columns or
                StockNameColumn not in df.columns or
                StockCodeColumn not in df.columns):
                print(f"\n오류: Excel 파일에 '{ThemeNameColumn}', '{StockNameColumn}', 또는 '{StockCodeColumn}' 컬럼이 없습니다.")
                print("현재 Excel 컬럼:", df.columns.tolist())
                print("파일 내용을 확인하고 변수를 올바르게 설정해주세요.")
            else:
                themes_data = {}
                for index, row in df.iterrows():
                    theme_name = str(row[ThemeNameColumn]).strip()
                    stock_name = str(row[StockNameColumn]).strip()
                    stock_code = str(row[StockCodeColumn]).strip()

                    # 종목 코드를 6자리 0-패딩 문자열로 변환
                    if stock_code.isdigit():
                        stock_code_padded = stock_code.zfill(6)
                    else:
                        stock_code_padded = stock_code 

                    if theme_name and stock_code_padded:
                        if theme_name not in themes_data:
                            themes_data[theme_name] = []
                        themes_data[theme_name].append({"code": stock_code_padded, "name": stock_name})

                output_json_file = os.path.join(os.path.dirname(__file__), "themes.json") # cache 폴더에 저장
                with open(output_json_file, 'w', encoding='utf-8') as f:
                    json.dump(themes_data, f, ensure_ascii=False, indent=4)

                print(f"\n'{output_json_file}' 파일이 성공적으로 생성되었습니다.")
                print(f"총 {len(themes_data)}개의 테마가 포함되었습니다.")
                for i, (theme, stocks) in enumerate(themes_data.items()):
                    if i >= 3: break
                    print(f"  예시 테마 '{theme}': {len(stocks)}개 종목 (첫 종목: {stocks[0]['name']} ({stocks[0]['code']}))")

        except FileNotFoundError:
            print(f"오류: '{excel_file_name}' 파일을 찾을 수 없습니다.")
            print("Excel 파일이 스크립트와 같은 디렉토리에 있는지, 또는 경로가 정확한지 확인해주세요.")
        except Exception as e:
            print(f"파일을 처리하는 중 예기치 않은 오류가 발생했습니다: {e}")
        ```
    * **실행:** `Theme.py` 스크립트를 실행하여 `themes.json` 파일을 생성합니다.
        ```bash
        python cache/Theme.py
        ```
2.  **캐시 파일 삭제:**
    `cache/market_data.json` 파일이 있다면 삭제합니다. 이는 앱이 시작될 때 최신 데이터 구조로 캐시를 재생성하도록 강제합니다.

### 4. 데이터베이스 설정 (선택 사항, `final_join` 데이터베이스가 필요하다면)
* `app.config['SQLALCHEMY_DATABASE_URI']`에 설정된 MySQL 데이터베이스(`final_join`)가 존재하고 접근 가능한지 확인합니다. 필요시 데이터베이스를 생성하고 사용자 권한을 설정합니다.

### 5. 애플리케이션 실행

1.  **Flask 앱 실행:**
    프로젝트 루트 디렉토리에서 다음 명령어를 실행합니다.
    ```bash
    python app.py
    ```
2.  **웹 브라우저 접속:**
    콘솔에 표시되는 URL(일반적으로 `http://127.0.0.1:5000/` 또는 `http://localhost:5000/`)로 접속하여 대시보드를 확인합니다.
    AskFin 페이지는 `http://127.0.0.1:5000/askfin`으로 접속합니다.

---

## 🔧 코드 구조 및 주요 파일

* `app.py`: Flask 애플리케이션의 메인 파일. 라우팅, 데이터 캐싱, 시장 데이터 조회 및 템플릿 렌더링을 담당합니다.
* `blueprints/`: 애플리케이션의 기능별 모듈(블루프린트)을 포함합니다.
    * `blueprints/askfin.py`: AI 대화형 금융 분석(AskFin)의 핵심 로직을 담당합니다. Gemini 모델 연동, 질의 분석, 요인 분석, 시나리오 분석 함수 등이 포함됩니다.
* `templates/`: Jinja2 템플릿 파일들을 포함합니다.
    * `templates/index.html`: 대시보드 페이지의 HTML 구조 및 JavaScript 로직을 포함합니다.
    * `templates/askfin.html`: AskFin 페이지의 HTML 구조 및 JavaScript 로직을 포함합니다.
    * `templates/macros.html`: 재사용 가능한 HTML 매크로(예: 랭킹 테이블)를 정의합니다.
* `cache/`: 데이터 캐시 파일 (`market_data.json`, `themes.json`)이 저장되는 디렉토리입니다.
* `.env`: 환경 변수(API 키 등)를 저장하는 파일입니다. (Git 추적 제외 권장)

---
