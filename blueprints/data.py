from flask import Blueprint, render_template

data_bp = Blueprint('data', __name__, url_prefix='/data')

@data_bp.route('/')
def data():
    return render_template('data.html')