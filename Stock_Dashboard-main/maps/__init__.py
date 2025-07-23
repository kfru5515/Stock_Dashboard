from flask import Blueprint, render_template

maps_bp = Blueprint('templates', __name__, template_folder='templates')

@maps_bp.route('/maps')
def maps():
    return render_template('maps.html')
