# app.py
# Sirve index.html tal cual y entrega APIs en forma de ARREGLO:
#  - /api/horarios?tab=salidas|llegadas&q=...&origen=...&destino=...&empresa=...&sector=...
#  - /api/salidas
#  - /api/llegadas
# Incluye campo de andén (anden/andén/platform/bay) y más horarios.

from flask import Flask, jsonify, request, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='.', static_url_path='')

@app.route('/')
def root():
    return send_from_directory(app.static_folder, 'index.html')

# ===========================
# DATOS: más horarios + ANDÉN
# (Puedes reemplazar empresa/horarios por los reales cuando los tengas)
# ===========================
BASE = [
    # -------- SALIDAS --------
    # Coñaripe
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Por confirmar", "salida": "06:30", "llegada": "07:40", "anden": "1", "dias": "Diario"},
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Por confirmar", "salida": "08:00", "llegada": "09:10", "anden": "1", "dias": "Diario"},
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Por confirmar", "salida": "11:30", "llegada": "12:40", "anden": "2", "dias": "Lun-Sáb"},
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Por confirmar", "salida": "15:45", "llegada": "16:55", "anden": "2", "dias": "Diario"},
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Por confirmar", "salida": "19:00", "llegada": "20:10", "anden": "3", "dias": "Lun-Vie"},

    # Los Lagos
    {"tipo": "salida", "sector": "Los Lagos", "origen": "Panguipulli", "destino": "Los Lagos", "empresa": "Por confirmar", "salida": "07:15", "llegada": "08:00", "anden": "4", "dias": "Lun-Sáb"},
    {"tipo": "salida", "sector": "Los Lagos", "origen": "Panguipulli", "destino": "Los Lagos", "empresa": "Por confirmar", "salida": "09:00", "llegada": "09:45", "anden": "4", "dias": "Diario"},
    {"tipo": "salida", "sector": "Los Lagos", "origen": "Panguipulli", "destino": "Los Lagos", "empresa": "Por confirmar", "salida": "12:30", "llegada": "13:15", "anden": "5", "dias": "Diario"},
    {"tipo": "salida", "sector": "Los Lagos", "origen": "Panguipulli", "destino": "Los Lagos", "empresa": "Por confirmar", "salida": "17:45", "llegada": "18:30", "anden": "5", "dias": "Lun-Vie"},

    # Villarrica
    {"tipo": "salida", "sector": "Villarrica","origen": "Panguipulli", "destino": "Villarrica","empresa": "Por confirmar", "salida": "06:50", "llegada": "08:30", "anden": "6", "dias": "Diario"},
    {"tipo": "salida", "sector": "Villarrica","origen": "Panguipulli", "destino": "Villarrica","empresa": "Por confirmar", "salida": "10:00", "llegada": "11:40", "anden": "6", "dias": "Diario"},
    {"tipo": "salida", "sector": "Villarrica","origen": "Panguipulli", "destino": "Villarrica","empresa": "Por confirmar", "salida": "14:15", "llegada": "15:55", "anden": "7", "dias": "Lun-Sáb"},
    {"tipo": "salida", "sector": "Villarrica","origen": "Panguipulli", "destino": "Villarrica","empresa": "Por confirmar", "salida": "18:20", "llegada": "20:00", "anden": "7", "dias": "Diario"},

    # Sectores rurales (Huerquehue, etc.)
    {"tipo": "salida", "sector": "Huerquehue","origen": "Panguipulli", "destino": "Huerquehue","empresa": "Por confirmar", "salida": "07:00", "llegada": "07:40", "anden": "8", "dias": "Lun-Vie"},
    {"tipo": "salida", "sector": "Huerquehue","origen": "Panguipulli", "destino": "Huerquehue","empresa": "Por confirmar", "salida": "13:30", "llegada": "14:10", "anden": "8", "dias": "Lun-Vie"},
    {"tipo": "salida", "sector": "Huerquehue","origen": "Panguipulli", "destino": "Huerquehue","empresa": "Por confirmar", "salida": "18:10", "llegada": "18:50", "anden": "9", "dias": "Lun-Vie"},

    # -------- LLEGADAS --------
    # Coñaripe -> Panguipulli
    {"tipo": "llegada","sector": "Coñaripe",  "origen": "Coñaripe",  "destino": "Panguipulli", "empresa": "Por confirmar", "salida": "07:10", "llegada": "08:20", "anden": "1", "dias": "Diario"},
    {"tipo": "llegada","sector": "Coñaripe",  "origen": "Coñaripe",  "destino": "Panguipulli", "empresa": "Por confirmar", "salida": "12:45", "llegada": "13:55", "anden": "2", "dias": "Lun-Sáb"},
    {"tipo": "llegada","sector": "Coñaripe",  "origen": "Coñaripe",  "destino": "Panguipulli", "empresa": "Por confirmar", "salida": "17:10", "llegada": "18:20", "anden": "3", "dias": "Diario"},

    # Los Lagos -> Panguipulli
    {"tipo": "llegada","sector": "Los Lagos", "origen": "Los Lagos", "destino": "Panguipulli", "empresa": "Por confirmar", "salida": "08:15", "llegada": "09:00", "anden": "4", "dias": "Lun-Sáb"},
    {"tipo": "llegada","sector": "Los Lagos", "origen": "Los Lagos", "destino": "Panguipulli", "empresa": "Por confirmar", "salida": "13:30", "llegada": "14:15", "anden": "5", "dias": "Diario"},
    {"tipo": "llegada","sector": "Los Lagos", "origen": "Los Lagos", "destino": "Panguipulli", "empresa": "Por confirmar", "salida": "19:00", "llegada": "19:45", "anden": "5", "dias": "Lun-Vie"},

    # Villarrica -> Panguipulli
    {"tipo": "llegada","sector": "Villarrica","origen": "Villarrica","destino": "Panguipulli", "empresa": "Por confirmar", "salida": "07:00", "llegada": "08:40", "anden": "6", "dias": "Diario"},
    {"tipo": "llegada","sector": "Villarrica","origen": "Villarrica","destino": "Panguipulli", "empresa": "Por confirmar", "salida": "12:15", "llegada": "13:55", "anden": "6", "dias": "Diario"},
    {"tipo": "llegada","sector": "Villarrica","origen": "Villarrica","destino": "Panguipulli", "empresa": "Por confirmar", "salida": "16:20", "llegada": "18:00", "anden": "7", "dias": "Lun-Sáb"},

    # Huerquehue -> Panguipulli
    {"tipo": "llegada","sector": "Huerquehue","origen": "Huerquehue","destino": "Panguipulli", "empresa": "Por confirmar", "salida": "06:30", "llegada": "07:10", "anden": "8", "dias": "Lun-Vie"},
    {"tipo": "llegada","sector": "Huerquehue","origen": "Huerquehue","destino": "Panguipulli", "empresa": "Por confirmar", "salida": "12:50", "llegada": "13:30", "anden": "8", "dias": "Lun-Vie"},
    {"tipo": "llegada","sector": "Huerquehue","origen": "Huerquehue","destino": "Panguipulli", "empresa": "Por confirmar", "salida": "17:50", "llegada": "18:30", "anden": "9", "dias": "Lun-Vie"},
]

def normaliza_registro(h):
    """Añade sinónimos y marca timestamp de actualización (compatibilidad con front)."""
    reg = dict(h)
    reg["actualizado"] = datetime.now().isoformat(timespec='seconds')
    # sinónimos de horas
    reg["hora_salida"] = reg.get("salida")
    reg["hora_llegada"] = reg.get("llegada")
    reg["departure"]   = reg.get("salida")
    reg["arrival"]     = reg.get("llegada")
    # sinónimos origen/destino/empresa
    reg["from"] = reg.get("origen")
    reg["to"]   = reg.get("destino")
    reg["company"] = reg.get("empresa")
    # sinónimos de andén
    reg["andén"] = reg.get("anden")
    reg["platform"] = reg.get("anden")
    reg["bay"] = reg.get("anden")
    return reg

DATA = [normaliza_registro(h) for h in BASE]

def filtrar(lista):
    """Aplica filtros por querystring: tab, q, origen, destino, empresa, sector."""
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
        campos = ("sector","origen","destino","empresa","dias","from","to","company","anden","andén","platform","bay")
        out = [h for h in out if any(q in str(h.get(k,"")).lower() for k in campos)]

    return out

# ---------- Endpoints (arreglo plano) ----------
@app.route('/api/horarios', methods=['GET'])
def horarios():
    return jsonify(filtrar(DATA))

@app.route('/api/salidas', methods=['GET'])
def salidas():
    return jsonify([h for h in filtrar(DATA) if h.get("tipo") == "salida"])

@app.route('/api/llegadas', methods=['GET'])
def llegadas():
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
    
