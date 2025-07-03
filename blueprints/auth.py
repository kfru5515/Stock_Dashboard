from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from functools import wraps
from models import User
from werkzeug.security import check_password_hash

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

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user'] = user.username
            flash('로그인 성공!', 'success')
            return redirect(url_for('index'))
        else:
            flash('사용자 이름 또는 비밀번호가 올바르지 않습니다.', 'danger')

    return render_template('sign_in.html')

@auth_bp.route('/logout')
def logout():
    session.pop('user', None)
    flash('로그아웃 되었습니다.', 'info')
    return redirect(url_for('home'))

@auth_bp.route('/mypage')
@login_required
def mypage():
    return render_template('tables.html')
