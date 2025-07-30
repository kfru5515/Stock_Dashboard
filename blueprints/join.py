from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from db.extensions import db


join_bp = Blueprint('join', __name__, url_prefix='/join')

@join_bp.route('/', methods=['GET','POST'])
def join():
    from app import User
    if request.method == 'POST':
        # 1) 폼에서 username으로 받은 값이 이제 이메일
        em = request.form['username']  # 여기엔 you@example.com 이 들어옵니다.
        pw1 = request.form['password_1']
        pw2 = request.form['password_2']
        notes = request.form.get('notes', '')

        # 2) 이메일 형식 검증
        import re
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', em):
            flash('유효한 이메일 주소를 입력해주세요.', 'danger')
            return redirect(url_for('join.join'))

        # 3) 비밀번호 확인
        if pw1 != pw2:
            flash('비밀번호가 일치하지 않습니다.', 'danger')
            return redirect(url_for('join.join'))

        # 4) 중복 검사 (username과 email 컬럼 모두 확인)
        if User.query.filter((User.username == em) | (User.email == em)).first():
            flash('이미 등록된 이메일입니다.', 'danger')
            return redirect(url_for('join.join'))

        # 5) 사용자 생성: username과 email에 같은 값을 넣음
        user = User(
            first_name=request.form['firstName'],
            last_name =request.form['lastName'],
            username  =em,
            email     =em,
            notes     =notes
        )
        user.set_password(pw1)
        db.session.add(user)
        db.session.commit()

        flash('회원가입이 완료되었습니다!', 'success')
        return redirect(url_for('auth.login'))

    return render_template('join.html')
