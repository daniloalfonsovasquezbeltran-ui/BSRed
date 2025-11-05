# app.py
# Sirve index.html SIN cambios visuales y entrega APIs compatibles:
# /api/horarios  (unificado)
# /api/salidas   (solo salidas)
# /api/llegadas  (solo llegadas)
# Todas devuelven un ARREGLO (list).

from flask import Flask, jsonify, request, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='.', static_url_path='')

@app.route('/')
def root():
    return send_from_directory(app.static_folder, 'index.html')

# ===========================
# Datos (ejemplo)  -> reemplaza por reales cuando quieras
# Estructura base (campos canónicos):
#   tipo: "salida" | "llegada"
#   origen, destino, empresa, sector
#   salida, llegada  (HH:MM 24h)
# ===========================
BASE = [
    # --- SALIDAS ---
    {"tipo": "salida", "sector": "Coñaripe",   "origen": "Panguipulli", "destino": "Coñaripe",   "empresa": "Buses Coñaripe", "salida": "07:00", "llegada": "08:10", "dias": "Diario"},
    {"tipo": "salida", "sector": "Los Lagos",   "origen": "Panguipulli", "destino": "Los Lagos",  "empresa": "Buses Los Lagos","salida": "08:00", "llegada": "08:45", "dias": "Lun-Sáb"},
    {"tipo": "salida", "sector": "Villarrica",  "origen": "Panguipulli", "destino": "Villarrica", "empresa": "Buses Villarrica","salida": "09:30", "llegada": "11:10","dias": "Diario"},
    {"tipo": "salida", "sector": "Huerquehue",  "origen": "Panguipulli", "destino": "Huerquehue", "empresa": "Rural Huerquehue","salida": "06:45", "llegada": "07:30","dias": "Lun-Vie"},
    # --- LLEGADAS ---
    {"tipo": "llegada","sector": "Coñaripe",   "origen": "Coñaripe",    "destino": "Panguipulli","empresa": "Buses Coñaripe", "salida": "18:00", "llegada": "19:10", "dias": "Diario"},
    {"tipo": "llegada","sector": "Los Lagos",  "origen": "Los Lagos",   "destino": "Panguipulli","empresa": "Buses Los Lagos","salida": "17:15", "llegada": "18:00", "dias": "Lun-Sáb"},
    {"tipo": "llegada","sector": "Villarrica", "origen": "Villarrica",  "destino": "Panguipulli","empresa": "Buses Villarrica","salida": "15:30", "llegada": "17:10","dias": "Diario"},
    {"tipo": "llegada","sector": "Huerquehue", "origen": "Huerquehue",  "destino": "Panguipulli","empresa": "Rural Huerquehue","salida": "07:45", "llegada": "08:30","dias": "Lun-Vie"},
]

def normaliza_registro(h):
    """Duplica claves con sinónimos para mayor compatibilidad con el frontend."""
    reg = dict(h)  # copia
    # timestamps útiles
    reg["actualizado"] = datetime.now().isoformat(timespec='seconds')
    # sinónimos esperados por posibles UIs
    reg["hora_salida"] = reg.get("salida")
    reg["hora_llegada"] = reg.get("llegada")
    reg["departure"] = reg.get("salida")
    reg["arrival"] = reg.get("llegada")
    reg["from"] = reg.get("origen")
    reg["to"] = reg.get("destino")
    reg["company"] = reg.get("empresa")
    return reg

DATA = [normaliza_registro(h) for h in BASE]

def filtrar(lista):
    """Aplica filtros por querystring sin romper si no vienen."""
    tab = (request.args.get("tab") or "").strip().lower()   # salidas|llegadas
    q   = (request.args.get("q") or "").strip().lower()
    origen  = (request.args.get("origen")  or "").strip().lower()
    destino = (request.args.get("destino") or "").strip().lower()
    empresa = (request.args.get("empresa") or "").strip().lower()
    sector  = (request.args.get("sector")  or "").strip().lower()

    out = list(lista)

    if tab in ("salidas", "llegadas"):
        tipo = "salida" if tab == "salidas" else "llegada"
        out = [h for h in out if h.get("tipo") == tipo]

    if origen:
        out = [h for h in out if origen in (h.get("origen","") + " " + h.get("from","")).lower()]

    if destino:
        out = [h for h in out if destino in (h.get("destino","") + " " + h.get("to","")).lower()]

    if empresa:
        out = [h for h in out if empresa in (h.get("empresa","") + " " + h.get("company","")).lower()]

    if sector:
        out = [h for h in out if sector in h.get("sector","").lower()]

    if q:
        ql = q.lower()
        campos = ("sector","origen","destino","empresa","dias","from","to","company")
        out = [h for h in out if any(ql in str(h.get(k,"")).lower() for k in campos)]

    return out

# ---------- Endpoints ----------
@app.route('/api/horarios', methods=['GET'])
def horarios():
    # arreglo unificado (para UIs que hacen list.map sobre TODO)
    return jsonify(filtrar(DATA))

@app.route('/api/salidas', methods=['GET'])
def salidas():
    # algunas UIs llaman este endpoint directamente
    return jsonify([h for h in filtrar(DATA) if h.get("tipo") == "salida"])

@app.route('/api/llegadas', methods=['GET'])
def llegadas():
    # idem para llegadas
    return jsonify([h for h in filtrar(DATA) if h.get("tipo") == "llegada"])

@app.route('/healthz')
def healthz():
    return jsonify({"ok": True})

@app.after_request
def cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    if request.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
    
