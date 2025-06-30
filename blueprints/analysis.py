from flask import Blueprint, render_template

analysis_bp = Blueprint('analysis', __name__, url_prefix='/analysis')

@analysis_bp.route('/')
def analysis():
    return render_template('analysis.html')