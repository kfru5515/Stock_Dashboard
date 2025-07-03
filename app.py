from flask import Flask, render_template
from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp

from db.extensions import db

app = Flask(__name__)
app.secret_key = '1234'

app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://humanda5:humanda5@localhost/final_join'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

@app.route('/')
def home():
    return render_template('index_main.html')

@app.route('/index')
def index():
    return render_template('index.html')

app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(analysis_bp)
app.register_blueprint(tables_bp)
app.register_blueprint(join_bp)  # <-- 여기 한 번만 등록!
app.register_blueprint(data_bp)

if __name__ == '__main__':
    app.run(debug=True)
