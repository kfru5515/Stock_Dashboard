from flask import Blueprint, render_template, session, redirect, url_for, flash
import yfinance as yf

tables_bp = Blueprint('tables', __name__, url_prefix='/tables')

@tables_bp.route('/')
def tables():
    user = session.get('user')  # 로그인 사용자 세션에서 정보 가져오기
    if not user:
        return redirect(url_for('auth.login'))  # 로그인 페이지로 리디렉션

    # 최근 조회 종목 가져오기
    recent_codes = session.get('recent_stocks', [])
    recent_stocks = []

    for code in recent_codes:
        try:
            stock = yf.Ticker(code).info
            name = stock.get('shortName', code)
            recent_stocks.append({'code': code, 'name': name})
        except Exception:
            recent_stocks.append({'code': code, 'name': code})

    return render_template('tables.html', user=user, recent_stocks=recent_stocks)
