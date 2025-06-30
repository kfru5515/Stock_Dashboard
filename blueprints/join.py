from flask import Blueprint, render_template, request, redirect, url_for, flash

join_bp = Blueprint('join', __name__, url_prefix='/join')

@join_bp.route('/', methods=['GET', 'POST'])
def join():
    if request.method == 'POST':
        first_name = request.form.get('firstName')
        last_name = request.form.get('lastName')
        username = request.form.get('username')  
        email = request.form.get('email')
        password_1 = request.form.get('password_1')
        password_2 = request.form.get('password_2')

        # 모든 필드 입력 체크
        if not all([first_name, last_name, username, email, password_1, password_2]):
            flash("모든 필드를 입력해주세요.", "warning")
            return render_template('join.html', first_name=first_name, last_name=last_name, username=username, email=email)

        # 비밀번호 일치 여부 체크
        if password_1 != password_2:
            flash("비밀번호가 일치하지 않습니다.", "danger")
            return render_template('join.html', first_name=first_name, last_name=last_name, username=username, email=email)

        # TODO: 회원가입 DB 저장 로직 작성

        flash("회원가입이 완료되었습니다!", "success")
        return redirect(url_for('index'))  # index 라우트로 이동하며 메시지 전달

    return render_template('join.html')
