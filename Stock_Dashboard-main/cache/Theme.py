import pandas as pd
import json

excel_file_name = "전체 테마별 구성종목.xlsx"

# Excel 파일 읽기
try:
    df = pd.read_excel(excel_file_name)
    print(f"'{excel_file_name}' 파일을 성공적으로 읽었습니다.")
    print("DataFrame의 첫 5행:")
    print(df.head())
    print("\nDataFrame의 컬럼명:")
    print(df.columns.tolist())

    # 필요한 컬럼 확인
    ThemeNameColumn = '테마' 
    StockNameColumn = '종목명' 
    StockCodeColumn = '종목코드' 

    # 필요한 컬럼들이 DataFrame에 있는지 확인
    if (ThemeNameColumn not in df.columns or
        StockNameColumn not in df.columns or
        StockCodeColumn not in df.columns):
        print(f"\n오류: Excel 파일에 '{ThemeNameColumn}', '{StockNameColumn}', 또는 '{StockCodeColumn}' 컬럼이 없습니다.")
        print("현재 Excel 컬럼:", df.columns.tolist())
        print("파일 내용을 확인하고 변수를 올바르게 설정해주세요.")
    else:
        # 테마별 종목 (코드와 이름)을 저장할 딕셔너리
        themes_data = {}

        # DataFrame을 순회하며 테마별 종목 정보 그룹화
        for index, row in df.iterrows():
            theme_name = str(row[ThemeNameColumn]).strip()
            stock_name = str(row[StockNameColumn]).strip()
            stock_code = str(row[StockCodeColumn]).strip()

            # --- 수정된 부분: 종목 코드를 6자리 0-패딩 문자열로 변환 ---
            if stock_code.isdigit(): # 숫자로만 이루어진 코드인 경우에만 패딩
                stock_code_padded = stock_code.zfill(6)
            else:
                stock_code_padded = stock_code # 숫자가 아니면 원본 그대로 유지 (잘못된 코드일 가능성)
            # --------------------------------------------------------

            if theme_name and stock_code_padded: # 테마명과 패딩된 종목코드가 비어있지 않은 경우에만 추가
                if theme_name not in themes_data:
                    themes_data[theme_name] = []
                
                # 종목명과 패딩된 종목코드를 딕셔너리 형태로 저장
                themes_data[theme_name].append({"code": stock_code_padded, "name": stock_name})

        # JSON 파일로 저장
        output_json_file = "themes.json"
        with open(output_json_file, 'w', encoding='utf-8') as f:
            json.dump(themes_data, f, ensure_ascii=False, indent=4)

        print(f"\n'{output_json_file}' 파일이 성공적으로 생성되었습니다.")
        print(f"총 {len(themes_data)}개의 테마가 포함되었습니다.")
        # 몇 가지 예시 테마와 종목 수 출력
        for i, (theme, stocks) in enumerate(themes_data.items()):
            if i >= 3: # 상위 3개 테마만 예시로 출력
                break
            print(f"  예시 테마 '{theme}': {len(stocks)}개 종목 (첫 종목: {stocks[0]['name']} ({stocks[0]['code']}))")

except FileNotFoundError:
    print(f"오류: '{excel_file_name}' 파일을 찾을 수 없습니다.")
    print("Excel 파일이 스크립트와 같은 디렉토리에 있는지, 또는 경로가 정확한지 확인해주세요.")
except Exception as e:
    print(f"파일을 처리하는 중 예기치 않은 오류가 발생했습니다: {e}")