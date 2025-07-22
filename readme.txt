AskFin
AI 기반 대화형 금융 분석 플랫폼
AskFin은 복잡한 금융 데이터를 AI와의 대화를 통해 손쉽게 분석하고, 직관적인 대시보드로 시장 현황을 파악할 수 있는 개인형 금융 분석 플랫폼입니다.



해결하고자 하는 문제
금융 정보는 방대하고 전문 지식을 요구하며, 특정 조건에 맞는 정보를 찾는 것은 매우 비효율적입니다. AskFin은 이러한 정보의 비대칭성과 접근성의 장벽을 허물어 누구나 데이터 기반의 현명한 의사결정을 내릴 수 있도록 돕고자 합니다 .




핵심 기능
1. 실시간 시장 현황 대시보드
주요 시장 지표(KOSPI, KOSDAQ, 환율, WTI)를 실시간으로 추적하고, 대화형 차트를 통해 시계열 흐름을 분석할 수 있습니다 . 또한, 거래량/거래대금 상위 종목, 주요 경제 지표, 국내외 뉴스를 통합 제공하여 시장의 전반적인 상황을 빠르게 파악하도록 돕습니다 .


### 2. AI 기반 대화형 분석

"지난 3년간 겨울에 오른 제약주는?"과 같은 복잡하고 구체적인 질문을 AI가 정확히 이해하고 분석합니다. Gemini 1.5 Flash 모델을 활용하여 사용자의 자연어 쿼리를 분석 가능한 구조화된 데이터로 변환하고, 그 결과를 명확한 테이블 형태로 제공합니다.



### 3. 원클릭 심층 정보 탐색

분석 결과 테이블의 종목명을 클릭 한 번으로, 해당 기업의 상세 정보(관련 뉴스, 기업 개요, 재무 정보, DART 공시)를 즉시 확인할 수 있습니다. 분산된 정보를 한곳에 모아 사용자의 효율적인 정보 탐색을 지원합니다.



기술 스택 (Tech Stack)
Back-end

Python: 서버 로직 및 데이터 분석을 위한 핵심 언어 


Flask: API 서버 구축 및 HTTP 요청 처리를 위한 경량 웹 프레임워크 


SQLAlchemy: MySQL 데이터베이스 연동(ORM) 

Front-end

HTML/CSS/JS: 웹의 구조, 스타일, 동적 기능 구현 


Bootstrap: 반응형 UI 컴포넌트(모달, 버튼) 구축 


Chart.js: 대시보드의 대화형 금융 차트 시각화 

AI 모델 및 데이터 처리

Google Gemini: 자연어 질문의 핵심 의도 분석 엔진 


FuzzyWuzzy: 문자열 유사도 분석을 통한 사용자 의도 보정 


Data Libraries: FinanceDataReader, pykrx, yfinance, DART FSS, ECOS API 등 





시스템 아키텍처
AskFin은 모듈성과 확장성을 고려하여 Presentation, Application, Data & Service의 3계층 아키텍처로 설계되었습니다.



시작하기 (Getting Started)
1. 프로젝트 복제
git clone https://github.com/kfru5515/Stock_Dashboard
cd Stock_Dashboard
2. 가상환경 설정 및 라이브러리 설치
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
3. 환경 변수 설정
프로젝트 루트 디렉터리에 .env 파일을 생성하고 아래와 같이 API 키를 입력합니다.

GOOGLE_AI_API_KEY="YOUR_GOOGLE_AI_KEY"
DART_API_KEY="YOUR_DART_API_KEY"
ECOS_API_KEY="YOUR_ECOS_API_KEY"
NEWS_API_KEY="YOUR_NEWS_API_KEY"
4. 애플리케이션 실행
flask run


작성자
전현준