from flask import Blueprint, render_template

tables_bp = Blueprint('tables', __name__, url_prefix='/tables')

@tables_bp.route('/')
def tables():
    return render_template('tables.html')
