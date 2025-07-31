from flask import Blueprint, request, redirect, url_for, session, flash, abort, render_template
from functools import wraps
import firebase_admin
from firebase_admin import auth, credentials
import os
import json
import traceback

auth_bp = Blueprint('auth', __name__, template_folder='templates') # templates 폴더는 이제 sign_in.html이 필요 없으므로 제거해도 됨

# Firebase Admin SDK 초기화
# 앱 시작 시 app.py에서 한 번만 호출되는 것이 가장 일반적입니다.
# 하지만 auth_bp에서만 초기화한다면 여기에 넣을 수 있습니다.
# 중요한: 서비스 계정 키는 환경 변수나 Secret Manager를 통해 안전하게 전달되어야 합니다.
if not firebase_admin._apps: # 이미 초기화되지 않았다면 초기화 시도
    try:
        # Cloud Run 환경 변수 'FIREBASE_ADMIN_CONFIG_JSON'에서 서비스 계정 키 JSON 문자열을 가져옴
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

# Firebase 인증이 필요한 라우트를 위한 데코레이터
def firebase_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 클라이언트에서 전송된 Authorization 헤더에서 ID 토큰을 가져옴
        # (클라이언트 JS에서 fetch 요청 시 토큰을 헤더에 포함시켜야 함)
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            flash('로그인이 필요합니다.', 'danger')
            return redirect(url_for('index')) # 로그인 모달이 뜨는 메인 페이지로 리다이렉트
        
        try:
            id_token = auth_header.split('Bearer ')[1]
            # Firebase Admin SDK를 사용하여 ID 토큰 검증
            decoded_token = auth.verify_id_token(id_token)
            
            # 토큰이 유효하면 사용자 정보를 Flask 세션에 저장
            session['user'] = {
                'uid': decoded_token['uid'],
                'email': decoded_token.get('email', 'N/A'),
                'name': decoded_token.get('name', 'N/A')
            }
            request.firebase_user = decoded_token # 요청 객체에 디코딩된 토큰 정보 저장 (선택 사항)
            return f(*args, **kwargs)
        except auth.InvalidIdTokenError:
            # 유효하지 않은 토큰 (만료 또는 변조)
            session.pop('user', None) # 세션 초기화
            flash('인증이 만료되었거나 유효하지 않습니다. 다시 로그인해주세요.', 'danger')
            return redirect(url_for('index'))
        except Exception as e:
            # 다른 오류 (예: 토큰 형식 오류, 네트워크 문제 등)
            session.pop('user', None)
            flash(f'인증 처리 중 오류 발생: {e}', 'danger')
            print(f"인증 오류: {e}")
            traceback.print_exc()
            return redirect(url_for('index'))
    return decorated_function

# 로그인 라우트는 이제 프론트엔드 JavaScript (base.html에 모달로 구현)에서 처리됩니다.
# 이 라우트는 이제 Flask 로그인 페이지를 렌더링하는 대신,
# 단순한 리다이렉트나 플래시 메시지를 띄우는 역할을 합니다.
@auth_bp.route('/login', methods=['GET'])
def login():
    flash('로그인 또는 회원가입을 위해 우측 상단 버튼을 클릭해주세요.', 'info')
    return redirect(url_for('index')) # 메인 페이지로 리다이렉트하여 모달이 뜨도록 유도

@auth_bp.route('/logout')
def logout():
    # 실제 Firebase 로그아웃은 클라이언트(JavaScript)에서 auth.signOut()을 호출해야 합니다.
    # 여기서는 Flask 세션만 지웁니다.
    session.pop('user', None)
    flash('로그아웃 되었습니다.', 'success')
    return redirect(url_for('index'))

@auth_bp.route('/mypage')
@firebase_login_required # Firebase 기반 인증 데코레이터 사용
def mypage():
    # 이제 session['user'] 또는 request.firebase_user에서 사용자 정보를 사용할 수 있습니다.
    user_email = session['user']['email'] if 'user' in session else '알 수 없는 사용자'
    flash(f'환영합니다, {user_email}님! 마이페이지입니다.', 'info')
    # mypage.html 템플릿이 없다면, tables.html 등으로 대체 가능
    # return render_template('mypage.html', user_email=user_email)
    return render_template('tables.html') # tables.html로 대체된 경우