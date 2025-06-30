from flask import Flask, render_template
from blueprints.analysis import analysis_bp
from blueprints.tables import tables_bp
from blueprints.join import join_bp
from blueprints.data import data_bp
from blueprints.auth import auth_bp

app = Flask(__name__)
app.secret_key = '1234'

@app.route('/')
def home():
    return render_template('index_main.html')

@app.route('/index')
def index():
    return render_template('index.html')

app.register_blueprint(auth_bp, url_prefix='/auth')

app.register_blueprint(analysis_bp)
app.register_blueprint(tables_bp)
app.register_blueprint(join_bp)
app.register_blueprint(data_bp)



if __name__ == '__main__':
    app.run(debug=True)