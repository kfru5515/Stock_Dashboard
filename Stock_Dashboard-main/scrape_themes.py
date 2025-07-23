import requests
from bs4 import BeautifulSoup
import json
import time

def scrape_naver_themes():
    """네이버 증권에서 모든 테마와 관련주를 스크래핑하여 딕셔너리로 반환"""
    
    base_url = "https://finance.naver.com"
    theme_list_url = f"{base_url}/sise/theme.naver"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    themes = {}
    page = 1

    print("1단계: 네이버 증권의 전체 테마 목록을 수집합니다...")
    while True:
        try:
            print(f"  - {page} 페이지 처리 중...")
            response = requests.get(f"{theme_list_url}?page={page}", headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            theme_table = soup.select('table.type_1.theme tr')
            if len(theme_table) <= 2:
                print("  -> 테이블에 내용이 없어 수집을 중단합니다.")
                break
            for row in theme_table:
                link_tag = row.select_one('td.col_type1 > a')
                if link_tag:
                    theme_name = link_tag.text.strip()
                    theme_link = link_tag['href']
                    themes[theme_name] = f"{base_url}{theme_link}"
            
            next_page_check = soup.select_one('td.pgR > a')
            if not next_page_check:
                print("  -> '다음' 버튼이 없어 마지막 페이지로 판단, 수집을 종료합니다.")
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  -> {page} 페이지 처리 중 오류 발생: {e}")
            break

    print(f" -> 총 {len(themes)}개의 테마를 찾았습니다.")

    print("\n2단계: 각 테마별 관련주 수집을 시작합니다.")
    theme_stock_map = {}

    for i, (name, url) in enumerate(themes.items()):
        try:
            print(f"  ({i+1}/{len(themes)}) '{name}' 테마 처리 중...")
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            stock_items = soup.select('div.name_area a')
            
            stock_names = [item.text.strip() for item in stock_items]
            if stock_names:
                theme_stock_map[name] = stock_names
            time.sleep(0.3)
        except Exception as e:
            print(f"    -> '{name}' 테마 처리 중 오류 발생: {e}")
            continue
            
    return theme_stock_map

if __name__ == "__main__":
    print("===== 네이버 증권 테마 정보 스크래핑 시작 =====")
    all_themes_data = scrape_naver_themes()
    
    if all_themes_data:
        with open('themes.json', 'w', encoding='utf-8') as f:
            json.dump(all_themes_data, f, ensure_ascii=False, indent=4)
        print("\n===== 작업 완료! 'themes.json' 파일이 성공적으로 생성되었습니다. =====")
    else:
        print("\n[작업 실패] 데이터를 수집하지 못해 'themes.json' 파일을 생성하지 않았습니다.")