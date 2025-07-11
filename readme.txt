
ASK- FIN

주요 기능

- 자연어 질의응답: "삼성전자 리포트 보여줘" 또는 "지난 1년간 가장 많이 오른 IT주는?"과 같은 일상적인 언어로 질문하여 주식 정보를 분석합니다.
- 기업 프로필 요약: PER, PBR, 배당수익률, 시가총액 등 핵심적인 기업 정보를 카드 형태로 제공합니다.
- AI 뉴스 요약: 대상 기업과 관련된 최신 뉴스를 AI가 분석하고 핵심 내용을 요약하여 제공합니다.
- 수급 동향 시각화: 최근 30일간의 개인, 기관, 외국인 투자자별 순매수 현황을 그래프로 시각화하여 수급 동향을 쉽게 파악할 수 있습니다.
- 실시간 종목 검색: KRX(한국거래소)에 상장된 전체 종목을 실시간으로 검색하고 분석할 수 있습니다.

기술 스택

- Backend: Python, Flask
- AI/LLM: Google Gemini API
- Frontend: HTML, CSS, JavaScript 
- Data Source: pykrx , 한은, financedatareader, ... etc 증권사 API를 통한 주식 데이터 수집


>> 설치 및 실행 방법 <<

1. 프로젝트 클론
   git clone https://github.com/your-username/Stock_Dashboard.git
   cd Stock_Dashboard

2. 가상 환경 생성 및 활성화
   # Windows
   python -m venv venv
   .\venv\Scripts\activate

   # macOS / Linux
   python3 -m venv venv
   source venv/bin/activate

3. 필요 라이브러리 설치
   아래 명령어를 실행하기 전, 프로젝트에 사용된 모든 라이브러리를 requirements.txt 파일에 미리 저장해두세요.
   pip install -r requirements.txt
   
   * requirements.txt 예시:
     flask
     google-generativeai
     pykrx
     pandas
     numpy
     # 기타 필요한 라이브러리

4. 환경 변수 설정
   프로젝트 루트 디렉토리에 .env 파일을 생성하고 Gemini API 키를 추가합니다.
   
   * .env 파일 내용:
     GEMINI_API_KEY="YOUR_GOOGLE_API_KEY"

5. 애플리케이션 실행
   flask run
   
   실행 후 웹 브라우저에서 http://127.0.0.1:5000 으로 접속하세요.


>> 프로젝트 구조 <<

Stock_Dashboard/
├── blueprints/
│   └── askfin.py         # 주식 분석 및 API 라우팅
├── static/
│   ├── css/
│   └── js/
├── templates/
│   └── index.html        # 메인 대시보드 HTML
├── .env                  # 환경 변수 파일 (API 키 등)
├── app.py                # Flask 애플리케이션 메인 파일
├── requirements.txt      # 파이썬 패키지 목록
└── README.md             # 프로젝트 소개 파일 (Markdown 원본)
