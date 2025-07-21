import os
import requests
from dotenv import load_dotenv

load_dotenv()

def get_cpi_data_from_bok():
    api_key = os.getenv("ECOS_API_KEY")
    if not api_key:
        raise ValueError("ECOS_API_KEY가 .env에서 로드되지 않았습니다.")

    url = f"http://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/100/901Y014/2020/2025/0000001/M"
    response = requests.get(url)
    data = response.json()
    rows = data['StatisticSearch']['row']

    cpi_dates = [f"{r['TIME'][:4]}-{r['TIME'][4:]}" for r in rows]
    cpi_values = [float(r['DATA_VALUE']) for r in rows]

    return cpi_dates[-12:], cpi_values[-12:]
