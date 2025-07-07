from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash
from db.extensions import db
from models import User


join_bp = Blueprint('join', __name__, url_prefix='/join')

@join_bp.route('/', methods=['GET', 'POST'])
def join():
    if 'user' in session:
        flash("이미 로그인된 상태입니다.", "warning")
        return redirect(url_for('index'))

    if request.method == 'POST':
        first_name = request.form.get('firstName')
        last_name = request.form.get('lastName')
        username = request.form.get('username')
        email = request.form.get('email')
        password_1 = request.form.get('password_1')
        password_2 = request.form.get('password_2')

        if not all([first_name, last_name, username, email, password_1, password_2]):
            flash("모든 필드를 입력해주세요.", "warning")
            return render_template('join.html', first_name=first_name, last_name=last_name, username=username, email=email)

        if password_1 != password_2:
            flash("비밀번호가 일치하지 않습니다.", "danger")
            return render_template('join.html', first_name=first_name, last_name=last_name, username=username, email=email)

        # 중복 사용자 검사
        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            flash("이미 존재하는 사용자 이름 또는 이메일입니다.", "danger")
            return render_template('join.html', first_name=first_name, last_name=last_name, username=username, email=email)

        # 비밀번호 해시 생성
        hashed_password = generate_password_hash(password_1)

        # 새로운 사용자 생성
        new_user = User(
            first_name=first_name,
            last_name=last_name,
            username=username,
            email=email,
            password=hashed_password
        )
        db.session.add(new_user)
        db.session.commit()

        flash("회원가입이 완료되었습니다!", "success")
        return redirect(url_for('index'))

    return render_template('join.html')
