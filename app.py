from flask import Flask, Blueprint, render_template, request, jsonify
from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp
import FinanceDataReader as fdr
import pandas as pd




app = Flask(__name__)
app.secret_key = '1234'

@app.route('/')
@app.route('/index')
def index():
    # 1. 데이터 가져오기 (최근 180일 정도)
    kospi_df = fdr.DataReader('KS11').tail(180).reset_index()
    kosdaq_df = fdr.DataReader('KQ11').tail(180).reset_index()

    # 2. 함수로 일/주/월봉 구성
    def resample_data(df, rule):
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        df_resampled = df['Close'].resample(rule).last().dropna()
        return [{'Date': d.strftime('%Y-%m-%d'), 'Close': float(c)} for d, c in df_resampled.items()]

    # 3. 데이터 포맷팅 (30개만 잘라서 사용)
    kospi_daily = kospi_df.tail(30)[['Date', 'Close']]
    kosdaq_daily = kosdaq_df.tail(30)[['Date', 'Close']]

    kospi_data = kospi_daily.to_dict(orient='records')
    kosdaq_data = kosdaq_daily.to_dict(orient='records')
    kospi_weekly_data = resample_data(kospi_df.copy(), 'W')
    kosdaq_weekly_data = resample_data(kosdaq_df.copy(), 'W')
    kospi_monthly_data = resample_data(kospi_df.copy(), 'M')
    kosdaq_monthly_data = resample_data(kosdaq_df.copy(), 'M')

    return render_template('index.html',
        kospi_data=kospi_data,
        kosdaq_data=kosdaq_data,
        kospi_weekly_data=kospi_weekly_data,
        kosdaq_weekly_data=kosdaq_weekly_data,
        kospi_monthly_data=kospi_monthly_data,
        kosdaq_monthly_data=kosdaq_monthly_data
    )


    
app.register_blueprint(auth_bp, url_prefix='/auth')

app.register_blueprint(analysis_bp)
app.register_blueprint(tables_bp)
app.register_blueprint(join_bp)
app.register_blueprint(data_bp)



if __name__ == '__main__':
    app.run(debug=True)