from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)

# =====================================================
# HORARIOS COMPLETOS – TERMINAL DE PANGUIPULLI
# =====================================================

horarios = [
    # VALDIVIA (Buses Pirehueico)
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "06:00", "llegada": "08:15"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "06:45", "llegada": "08:55"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "07:30", "llegada": "09:45"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "09:00", "llegada": "11:15"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "10:30", "llegada": "12:45"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "12:00", "llegada": "14:10"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "13:30", "llegada": "15:45"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "15:00", "llegada": "17:10"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "16:30", "llegada": "18:45"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "18:00", "llegada": "20:15"},
    {"empresa": "Buses Pirehueico", "destino": "Valdivia", "salida": "19:15", "llegada": "21:30"},

    # SANTIAGO
    {"empresa": "Pullman Bus", "destino": "Santiago", "salida": "20:30", "llegada": "07:00"},
    {"empresa": "Luna Express", "destino": "Santiago", "salida": "21:00", "llegada": "07:30"},
    {"empresa": "Pullman Bus", "destino": "Panguipulli", "salida": "Santiago 21:30", "llegada": "09:20"},
    {"empresa": "Luna Express", "destino": "Panguipulli", "salida": "Santiago 22:15", "llegada": "09:45"},

    # TEMUCO
    {"empresa": "Regional Sur", "destino": "Temuco", "salida": "07:30", "llegada": "10:00"},
    {"empresa": "Regional Sur", "destino": "Temuco", "salida": "09:45", "llegada": "12:10"},
    {"empresa": "Regional Sur", "destino": "Temuco", "salida": "13:15", "llegada": "15:45"},
    {"empresa": "Regional Sur", "destino": "Temuco", "salida": "17:30", "llegada": "20:00"},
    {"empresa": "Regional Sur", "destino": "Panguipulli", "salida": "Temuco 06:00", "llegada": "08:30"},
    {"empresa": "Regional Sur", "destino": "Panguipulli", "salida": "Temuco 09:30", "llegada": "12:00"},
    {"empresa": "Regional Sur", "destino": "Panguipulli", "salida": "Temuco 14:00", "llegada": "16:30"},
    {"empresa": "Regional Sur", "destino": "Panguipulli", "salida": "Temuco 17:00", "llegada": "19:20"},

    # COÑARIPE
    {"empresa": "Buses Coñaripe", "destino": "Coñaripe", "salida": "06:30", "llegada": "07:25"},
    {"empresa": "Buses Coñaripe", "destino": "Coñaripe", "salida": "08:00", "llegada": "08:55"},
    {"empresa": "Buses Coñaripe", "destino": "Coñaripe", "salida": "10:30", "llegada": "11:25"},
    {"empresa": "Buses Coñaripe", "destino": "Coñaripe", "salida": "13:00", "llegada": "13:55"},
    {"empresa": "Buses Coñaripe", "destino": "Coñaripe", "salida": "17:30", "llegada": "18:25"},
    {"empresa": "Buses Coñaripe", "destino": "Panguipulli", "salida": "Coñaripe 07:40", "llegada": "08:35"},
    {"empresa": "Buses Coñaripe", "destino": "Panguipulli", "salida": "Coñaripe 12:00", "llegada": "12:55"},
    {"empresa": "Buses Coñaripe", "destino": "Panguipulli", "salida": "Coñaripe 18:00", "llegada": "18:55"},

    # VILLARRICA
    {"empresa": "Buses Villarrica", "destino": "Villarrica", "salida": "06:45", "llegada": "08:20"},
    {"empresa": "Buses Villarrica", "destino": "Villarrica", "salida": "11:15", "llegada": "12:50"},
    {"empresa": "Buses Villarrica", "destino": "Villarrica", "salida": "15:45", "llegada": "17:20"},
    {"empresa": "Buses Villarrica", "destino": "Panguipulli", "salida": "Villarrica 09:00", "llegada": "10:35"},
    {"empresa": "Buses Villarrica", "destino": "Panguipulli", "salida": "Villarrica 13:30", "llegada": "15:05"},
    {"empresa": "Buses Villarrica", "destino": "Panguipulli", "salida": "Villarrica 18:00", "llegada": "19:35"},

    # LOS LAGOS
    {"empresa": "Regional Sur", "destino": "Los Lagos", "salida": "14:00", "llegada": "14:45"},
    {"empresa": "Turbus", "destino": "Los Lagos", "salida": "09:15", "llegada": "10:00"},
    {"empresa": "Regional Sur", "destino": "Panguipulli", "salida": "Los Lagos 13:00", "llegada": "13:45"},
    {"empresa": "Turbus", "destino": "Panguipulli", "salida": "Los Lagos 08:00", "llegada": "08:45"},

    # LIQUIÑE
    {"empresa": "Buses Liquiñe", "destino": "Liquiñe", "salida": "08:00", "llegada": "09:30"},
    {"empresa": "Buses Liquiñe", "destino": "Liquiñe", "salida": "14:15", "llegada": "15:45"},
    {"empresa": "Buses Liquiñe", "destino": "Liquiñe", "salida": "17:50", "llegada": "19:20"},
    {"empresa": "Buses Liquiñe", "destino": "Panguipulli", "salida": "Liquiñe 06:30", "llegada": "08:00"},
    {"empresa": "Buses Liquiñe", "destino": "Panguipulli", "salida": "Liquiñe 12:00", "llegada": "13:30"},

    # NELTUME (Buses Lafit)
    {"empresa": "Buses Lafit", "destino": "Neltume", "salida": "07:00", "llegada": "09:00"},
    {"empresa": "Buses Lafit", "destino": "Neltume", "salida": "13:30", "llegada": "15:30"},
    {"empresa": "Buses Lafit", "destino": "Panguipulli", "salida": "Neltume 10:30", "llegada": "12:30"},
    {"empresa": "Buses Lafit", "destino": "Panguipulli", "salida": "Neltume 16:45", "llegada": "18:45"},

    # PUERTO FUY
    {"empresa": "Rural Puerto Fuy", "destino": "Puerto Fuy", "salida": "11:00", "llegada": "12:20"},
    {"empresa": "Rural Puerto Fuy", "destino": "Puerto Fuy", "salida": "17:00", "llegada": "18:20"},
    {"empresa": "Rural Puerto Fuy", "destino": "Panguipulli", "salida": "Puerto Fuy 06:15", "llegada": "07:35"},
    {"empresa": "Rural Puerto Fuy", "destino": "Panguipulli", "salida": "Puerto Fuy 14:00", "llegada": "15:20"},

    # HURQUEHUE
    {"empresa": "Rural Hurquehue", "destino": "Hurquehue", "salida": "12:30", "llegada": "13:10"},
    {"empresa": "Rural Hurquehue", "destino": "Hurquehue", "salida": "16:45", "llegada": "17:25"},
    {"empresa": "Rural Hurquehue", "destino": "Panguipulli", "salida": "Hurquehue 06:30", "llegada": "07:10"},
    {"empresa": "Rural Hurquehue", "destino": "Panguipulli", "salida": "Hurquehue 14:15", "llegada": "14:55"}
]


# =====================================================
# RUTAS PARA FRONTEND Y API
# =====================================================

@app.route("/")
def front():
    return send_from_directory(os.getcwd(), "index.html")

# ARCHIVOS ESTÁTICOS (logo, manifest, sw.js, etc.)
@app.route("/<path:path>")
def static_proxy(path):
    return send_from_directory(os.getcwd(), path)

@app.route("/horarios")
def get_horarios():
    return jsonify(horarios)

@app.route("/salidas")
def get_salidas():
    return jsonify([h for h in horarios if "Panguipulli" not in h["destino"]])

@app.route("/llegadas")
def get_llegadas():
    return jsonify([h for h in horarios if "Panguipulli" in h["destino"]])

@app.route("/destinos")
def destinos():
    destinos = sorted(list(set([h["destino"] for h in horarios])))
    return jsonify(destinos)


if __name__ == "__main__":
    app.run(debug=True)
    
