# app.py
# Sirve index.html tal cual y expone /api/horarios devolviendo UN ARREGLO (list) directo.

from flask import Flask, jsonify, request, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='.', static_url_path='')

# --------- Rutas de la página ----------
@app.route('/')
def root():
    return send_from_directory(app.static_folder, 'index.html')

# Cualquier archivo referenciado por el index (manifest, sw, logos) se sirve desde la raíz.


# --------- Datos de ejemplo (reemplaza por los reales cuando quieras) ----------
HORARIOS = [
    {
        "sector": "Panguipulli",
        "origen": "Panguipulli",
        "destino": "Terminal Panguipulli",
        "empresa": "Por confirmar",
        "salida": "07:00",
        "llegada": "07:20",
        "dias": "Lunes a Viernes",
        "actualizado": datetime.now().isoformat(timespec='seconds')
    },
    {
        "sector": "Coñaripe",
        "origen": "Panguipulli",
        "destino": "Coñaripe",
        "empresa": "Por confirmar",
        "salida": "08:30",
        "llegada": "09:40",
        "dias": "Diario",
        "actualizado": datetime.now().isoformat(timespec='seconds')
    },
    {
        "sector": "Los Lagos",
        "origen": "Panguipulli",
        "destino": "Los Lagos",
        "empresa": "Por confirmar",
        "salida": "10:00",
        "llegada": "10:50",
        "dias": "Lunes a Sábado",
        "actualizado": datetime.now().isoformat(timespec='seconds')
    },
    {
        "sector": "Villarrica",
        "origen": "Panguipulli",
        "destino": "Villarrica",
        "empresa": "Por confirmar",
        "salida": "12:00",
        "llegada": "13:40",
        "dias": "Diario",
        "actualizado": datetime.now().isoformat(timespec='seconds')
    },
    {
        "sector": "Huerquehue",
        "origen": "Panguipulli",
        "destino": "Huerquehue",
        "empresa": "Por confirmar",
        "salida": "06:45",
        "llegada": "07:30",
        "dias": "Lunes a Viernes",
        "actualizado": datetime.now().isoformat(timespec='seconds')
    },
]

def filtrar(lista, campo, valor):
    if not valor:
        return lista
    v = valor.strip().lower()
    return [h for h in lista if v in str(h.get(campo, "")).lower()]

# --------- API: devuelve UN ARREGLO ---------
@app.route('/api/horarios', methods=['GET'])
def api_horarios():
    """
    Devuelve un arreglo JSON de horarios.
    Filtros opcionales por querystring: sector, origen, destino, empresa, q (búsqueda libre)
    Ej: /api/horarios?sector=Coñaripe
    """
    data = HORARIOS[:]
    data = filtrar(data, 'sector', request.args.get('sector'))
    data = filtrar(data, 'origen', request.args.get('origen'))
    data = filtrar(data, 'destino', request.args.get('destino'))
    data = filtrar(data, 'empresa', request.args.get('empresa'))

    # búsqueda libre "q" sobre varios campos
    q = request.args.get('q')
    if q:
        ql = q.strip().lower()
        data = [
            h for h in data
            if any(ql in str(h.get(k, "")).lower()
                   for k in ('sector', 'origen', 'destino', 'empresa', 'dias'))
        ]
    return jsonify(data)  # <- array directo (no objeto)

# --------- Salud y CORS básicos ---------
@app.route('/healthz')
def healthz():
    return jsonify({"ok": True}), 200

@app.after_request
def add_cors_headers(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    if request.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
    
