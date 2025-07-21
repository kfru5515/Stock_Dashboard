import requests
import json
from bs4 import BeautifulSoup
import os
from datetime import datetime
from dotenv import load_dotenv

# .env 파일에서 환경 변수를 로드합니다.
load_dotenv()

# 캐시 파일이 저장될 기본 경로 설정
CACHE_DIR = 'cache'

def fetch_and_save_ecos_key_statistics(api_key):
    """
    한국은행 ECOS KeyStatisticList API에서 주요 통계 지표 목록을 가져와 JSON 파일로 저장합니다.
    파일은 'cache' 폴더에 생성됩니다.
    """
    base_url = "https://ecos.bok.or.kr/api/KeyStatisticList"

    # API 호출을 위해 전체 URL을 구성합니다.
    full_url = f"{base_url}/{api_key}/xml/kr/1/100"

    # 'cache' 폴더가 없으면 생성합니다.
    os.makedirs(CACHE_DIR, exist_ok=True)

    try:
        print(f"ECOS KeyStatisticList API 호출 시도: {full_url}")
        # API 요청을 보냅니다. 타임아웃 10초를 설정합니다.
        response = requests.get(full_url, timeout=10)
        # HTTP 오류(예: 4xx, 5xx)가 발생하면 예외를 발생시킵니다.
        response.raise_for_status()

        # --- API 응답 내용 출력 (디버깅용, 필요 없으면 주석 처리 또는 삭제) ---
        print("\n--- API 응답 내용 시작 ---")
        print(response.content.decode('utf-8')) # XML 내용을 콘솔에 출력 (한글 깨짐 방지)
        print("--- API 응답 내용 끝 ---\n")
        # --- 디버깅용 코드 끝 ---

        soup = BeautifulSoup(response.content, 'xml')
        items = soup.find_all('row') # 'row' 태그는 동일하게 사용될 가능성이 높음

        if not items:
            print("ECOS 주요 통계 현황 API에서 데이터를 찾을 수 없습니다.")
            return

        key_statistics_data = []
        for item in items:
            class_name = item.find('CLASS_NAME').get_text() if item.find('CLASS_NAME') else 'N/A'
            keystat_name = item.find('KEYSTAT_NAME').get_text() if item.find('KEYSTAT_NAME') else 'N/A'
            data_value = item.find('DATA_VALUE').get_text() if item.find('DATA_VALUE') else 'N/A'
            cycle = item.find('CYCLE').get_text() if item.find('CYCLE') else 'N/A'
            unit_name = item.find('UNIT_NAME').get_text() if item.find('UNIT_NAME') else 'N/A'

            key_statistics_data.append({
                "CLASS_NAME": class_name,
                "KEYSTAT_NAME": keystat_name,
                "DATA_VALUE": data_value,
                "CYCLE": cycle,
                "UNIT_NAME": unit_name
            })
            
        # 현재 날짜와 시간을 기반으로 파일 이름을 생성합니다.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 'cache' 폴더 내에 파일 경로를 생성
        file_name = os.path.join(CACHE_DIR, f"ecos_key_statistics_{timestamp}.json")

        # 추출된 데이터를 JSON 파일로 저장합니다.
        with open(file_name, 'w', encoding='utf-8') as f:
            json.dump(key_statistics_data, f, ensure_ascii=False, indent=4)

        print(f"ECOS 주요 통계 지표 데이터를 '{file_name}' 파일에 성공적으로 저장했습니다. 총 {len(key_statistics_data)}개 항목.")

    except requests.exceptions.Timeout:
        print("API 요청 시간 초과 오류가 발생했습니다.")
    except requests.exceptions.RequestException as e:
        print(f"API 요청 오류가 발생했습니다: {e}")
    except Exception as e: # JSONDecodeError 포함
        print(f"데이터 처리 중 알 수 없는 오류가 발생했습니다: {e}")

if __name__ == "__main__":
    # 환경 변수에서 ECOS_API_KEY를 가져옵니다.
    ecos_api_key = os.getenv("ECOS_API_KEY")

    if ecos_api_key:
        fetch_and_save_ecos_key_statistics(ecos_api_key)
    else:
        print("오류: 환경 변수 'ECOS_API_KEY'가 설정되어 있지 않습니다.")
        print("스크립트와 같은 폴더에 '.env' 파일을 생성하고 'ECOS_API_KEY=YOUR_API_KEY' 형식으로 API 키를 추가해주세요.")