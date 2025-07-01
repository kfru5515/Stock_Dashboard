from flask import Blueprint, render_template
from datetime import datetime


tables_bp = Blueprint('tables', __name__, url_prefix='/tables')

@tables_bp.route('/')
def tables():
    user = {
        'name': '홍길동',
        'email': 'hong@example.com',
        'joined_at': datetime(2023, 1, 1),
        'recent_logs': [
            {'timestamp': '2025-06-30 13:00', 'action': '로그인'},
            {'timestamp': '2025-06-29 09:20', 'action': '비밀번호 변경'},
        ]
    }
    return render_template('tables.html', user=user)

@tables_bp.route('/change-password')
def change_password():
    return render_template('change_password.html')