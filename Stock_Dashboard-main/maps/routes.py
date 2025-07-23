from . import maps_bp 

@maps_bp.route('/maps')
def maps():
    return render_template('maps.html')
