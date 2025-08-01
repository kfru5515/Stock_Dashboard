from flask import Blueprint, request, redirect, url_for, session, flash, abort, render_template
from functools import wraps
import firebase_admin
from firebase_admin import auth, credentials
import os
import json
import traceback

auth_bp = Blueprint('auth', __name__, template_folder='templates') # templates 폴더는 이제 sign_in.html이 필요 없으므로 제거해도 됨


if not firebase_admin._apps: # 이미 초기화되지 않았다면 초기화 시도
    try:
        firebase_admin_config_json = os.getenv('FIREBASE_ADMIN_CONFIG_JSON')
        if firebase_admin_config_json:
            cred_json = json.loads(firebase_admin_config_json)
            cred = credentials.Certificate(cred_json)
            firebase_admin.initialize_app(cred)
            print("Firebase Admin SDK 초기화 성공 (auth.py)")
        else:
            print("경고: Firebase Admin SDK 초기화에 필요한 FIREBASE_ADMIN_CONFIG_JSON 환경 변수가 없습니다.")
            print("  - Firebase Admin SDK가 초기화되지 않아 인증 관련 백엔드 기능이 동작하지 않을 수 있습니다.")
    except Exception as e:
        print(f"Firebase Admin SDK 초기화 실패 (auth.py): {e}")
        traceback.print_exc()

def firebase_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            flash('로그인이 필요합니다.', 'danger')
            return redirect(url_for('index')) 
        
        try:
            id_token = auth_header.split('Bearer ')[1]
            decoded_token = auth.verify_id_token(id_token)
            
            session['user'] = {
                'uid': decoded_token['uid'],
                'email': decoded_token.get('email', 'N/A'),
                'name': decoded_token.get('name', 'N/A')
            }
            request.firebase_user = decoded_token
            return f(*args, **kwargs)
        except auth.InvalidIdTokenError:
            session.pop('user', None) 
            flash('인증이 만료되었거나 유효하지 않습니다. 다시 로그인해주세요.', 'danger')
            return redirect(url_for('index'))
        except Exception as e:
            session.pop('user', None)
            flash(f'인증 처리 중 오류 발생: {e}', 'danger')
            print(f"인증 오류: {e}")
            traceback.print_exc()
            return redirect(url_for('index'))
    return decorated_function


@auth_bp.route('/login', methods=['GET'])
def login():
    flash('로그인 또는 회원가입을 위해 우측 상단 버튼을 클릭해주세요.', 'info')
    return redirect(url_for('index')) 

@auth_bp.route('/logout')
def logout():

    session.pop('user', None)
    flash('로그아웃 되었습니다.', 'success')
    return redirect(url_for('index'))

@auth_bp.route('/mypage')
@firebase_login_required 
def mypage():
    user_email = session['user']['email'] if 'user' in session else '알 수 없는 사용자'
    flash(f'환영합니다, {user_email}님! 마이페이지입니다.', 'info')
    return render_template('tables.html')