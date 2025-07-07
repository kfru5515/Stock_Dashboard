from flask import Blueprint, render_template, session
from .auth import login_required
from models import User

tables_bp = Blueprint('tables', __name__, url_prefix='/tables')

@tables_bp.route('/')
@login_required
def tables():
    username = session.get('user')
    user_obj = User.query.filter_by(username=username).first()

    if not user_obj:
        return "사용자 정보를 찾을 수 없습니다.", 404

    user = {
        'name': f"{user_obj.first_name} {user_obj.last_name}",
        'email': user_obj.email,
        'recent_logs': [
            {'timestamp': '2025-06-30 13:00', 'action': '로그인'},
            {'timestamp': '2025-06-29 09:20', 'action': '비밀번호 변경'},
        ]
    }

    return render_template('tables.html', user=user)

@tables_bp.route('/change-password')
@login_required
def change_password():
    return render_template('change_password.html')
