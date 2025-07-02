from flask import Blueprint, request, jsonify, render_template
import FinanceDataReader as fdr
from jinja2.runtime import Undefined

analysis_bp = Blueprint('analysis', __name__, url_prefix='/analysis')

@analysis_bp.route('/compare')
def compare():
    stock1 = request.args.get('stock1')
    stock2 = request.args.get('stock2')

    if not stock1 or not stock2:
        return jsonify({'error': '두 종목 코드를 입력하세요.'})

    try:
        df1 = fdr.DataReader(stock1).tail(60)[['Close']]
        df2 = fdr.DataReader(stock2).tail(60)[['Close']]

        if df1.empty or df2.empty:
            return jsonify({'error': '종목 데이터를 찾을 수 없습니다.'})

        # 날짜 기준 inner join
        df = df1.join(df2, lsuffix='_1', rsuffix='_2', how='inner')

        dates = df.index.strftime('%Y-%m-%d').tolist()
        stock1_prices = df['Close_1'].tolist()
        stock2_prices = df['Close_2'].tolist()

        stock1_name = stock1 if not isinstance(stock1, Undefined) else '종목1'
        stock2_name = stock2 if not isinstance(stock2, Undefined) else '종목2'

        return jsonify({
            'dates': dates,
            'stock1_prices': stock1_prices,
            'stock2_prices': stock2_prices,
            'stock1_name': stock1_name,
            'stock2_name': stock2_name,
        })

    except Exception as e:
        return jsonify({'error': str(e)})

@analysis_bp.route('/')
def analysis_page():
    return render_template('analysis.html')
