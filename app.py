from flask import Flask, Blueprint, render_template, request, jsonify
from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp
import FinanceDataReader as fdr


data_bp = Blueprint('data', __name__, url_prefix='/data')

@data_bp.route('/stocks')
def stocks():
    # 예시: 코스피 지수 데이터 가져오기
    df = fdr.DataReader('KS11')
    # 데이터 일부만 json 변환 (예: 최근 5개)
    data = df.tail(5).reset_index().to_dict(orient='records')
    return jsonify(data)

@data_bp.route('/stock')
def stock():
    # 특정 종목 예시: 삼성전자(005930)
    df = fdr.DataReader('005930')
    data = df.tail(5).reset_index().to_dict(orient='records')
    return jsonify(data)

@data_bp.route('/kospi')
def kospi():
    df = fdr.DataReader('KS11')
    data = df.tail(5).reset_index().to_dict(orient='records')
    return jsonify(data)


app = Flask(__name__)
app.secret_key = '1234'

@app.route('/')
def home():
    return render_template('index_main.html')

@app.route('/index')
def index():
    return render_template('index.html')

app.register_blueprint(auth_bp, url_prefix='/auth')

app.register_blueprint(analysis_bp)
app.register_blueprint(tables_bp)
app.register_blueprint(join_bp)
app.register_blueprint(data_bp)



if __name__ == '__main__':
    app.run(debug=True)