# app.py
# Flask que sirve index.html (sin cambiarlo) y entrega /api/horarios en JSON

from flask import Flask, jsonify, request, send_from_directory, make_response
from datetime import datetime

# Sirve archivos estáticos desde la RAÍZ del repo (donde está index.html)
app = Flask(__name__, static_folder='.', static_url_path='')

# ------- Rutas de archivos estáticos y página ---------
@app.route('/')
def root():
    # Muestra tu index.html tal cual, sin cambios visuales
    return send_from_directory(app.static_folder, 'index.html')

# Si tu index.html referencia /manifest.json, /sw.js, /logo.png, etc.,
# Flask los servirá automáticamente desde la raíz por ser static_folder='.'


# ------- API de horarios (JSON) ---------
# Estructura de ejemplo: ajusta/expande más tarde con tus datos reales
HORARIOS = [
    # Ejemplos mínimos para que el frontend no quede en blanco.
    # Puedes reemplazar/expandir con tus horarios reales cuando quieras.
    {
        "sector": "Panguipulli",
        "origen": "Panguipulli",
        "destino": "Terminal Panguipulli",
        "empresa": "Por confirmar",
        "salida": "07:00",
        "llegada": "07:20",
        "dias": "Lunes a Viernes"
    },
    {
        "sector": "Coñaripe",
        "origen": "Panguipulli",
        "destino": "Coñaripe",
        "empresa": "Por confirmar",
        "salida": "08:30",
        "llegada": "09:40",
        "dias": "Diario"
    },
    {
        "sector": "Los Lagos",
        "origen": "Panguipulli",
        "destino": "Los Lagos",
        "empresa": "Por confirmar",
        "salida": "10:00",
        "llegada": "10:50",
        "dias": "Lunes a Sábado"
    },
    {
        "sector": "Villarrica",
        "origen": "Panguipulli",
        "destino": "Villarrica",
        "empresa": "Por confirmar",
        "salida": "12:00",
        "llegada": "13:40",
        "dias": "Diario"
    },
    {
        "sector": "Huerquehue",
        "origen": "Panguipulli",
        "destino": "Huerquehue",
        "empresa": "Por confirmar",
        "salida": "06:45",
        "llegada": "07:30",
        "dias": "Lunes a Viernes"
    },
]

def filtrar(hs, q, campo):
    if not q:
        return hs
    q = q.strip().lower()
    return [h for h in hs if h.get(campo, "").lower() == q or q in h.get(campo, "").lower()]

@app.route('/api/horarios', methods=['GET'])
def api_horarios():
    """
    Devuelve JSON con los horarios.
    Filtros opcionales por querystring:
      ?sector=Coñaripe&origen=Panguipulli&destino=Villarrica&empresa=...
    """
    data = HORARIOS[:]
    data = filtrar(data, request.args.get('sector'), 'sector')
    data = filtrar(data, request.args.get('origen'), 'origen')
    data = filtrar(data, request.args.get('destino'), 'destino')
    data = filtrar(data, request.args.get('empresa'), 'empresa')

    payload = {
        "terminal": "Terminal Panguipulli",
        "actualizado": datetime.now().isoformat(timespec='seconds'),
        "total": len(data),
        "items": data
    }
    return jsonify(payload)

# ------- Salud y CORS básicos ---------
@app.route('/healthz')
def healthz():
    return jsonify({"ok": True}), 200

@app.after_request
def add_cors_headers(resp):
    # Permite que tu index.html consuma /api/horarios incluso si se carga estático
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    # Evita cache agresivo en respuestas API mientras desarrollas
    if request.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp

# ------- Run local -------
if __name__ == '__main__':
    # host='0.0.0.0' para que funcione en Render/Docker también
    app.run(host='0.0.0.0', port=5000, debug=True)
    
