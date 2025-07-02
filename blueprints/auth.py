from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from functools import wraps

auth_bp = Blueprint('auth', __name__, template_folder='templates')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'admin' and password == 'password':
            session['user'] = username
            return redirect(url_for('index'))  # index는 메인 페이지
        else:
            flash('로그인 실패')
    return render_template('sign_in.html')

@auth_bp.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('home'))

# ✅ 로그인한 사용자만 접근 가능 / tables.html 렌더링
@auth_bp.route('/mypage')
@login_required
def mypage():
    return render_template('tables.html')
