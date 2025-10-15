from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)


def formato_ampm(hora_24):
    return datetime.strptime(hora_24, "%H:%M").strftime("%I:%M %p")


horarios_data = [

    {"id": 1, "tipo": "salida", "empresa": "Buses JAC", "destino": "Valdivia", "origen": "Panguipulli", "salida": "06:15", "llegada": "08:30", "estado": "A tiempo", "anden": 1},
    {"id": 2, "tipo": "salida", "empresa": "Tur Bus", "destino": "Temuco", "origen": "Panguipulli", "salida": "06:45", "llegada": "09:15", "estado": "A tiempo", "anden": 2},
    {"id": 3, "tipo": "salida", "empresa": "Buses Liquiñe", "destino": "Coñaripe", "origen": "Panguipulli", "salida": "07:00", "llegada": "07:45", "estado": "A tiempo", "anden": 4},
    {"id": 4, "tipo": "salida", "empresa": "Buses Galicia", "destino": "Lanco", "origen": "Panguipulli", "salida": "07:30", "llegada": "08:15", "estado": "Próximo", "anden": 3},
    {"id": 5, "tipo": "salida", "empresa": "Buses Lafit", "destino": "Calafquen", "origen": "Panguipulli", "salida": "08:00", "llegada": "08:30", "estado": "Próximo", "anden": 5},
    {"id": 6, "tipo": "salida", "empresa": "Tur Bus", "destino": "Santiago", "origen": "Panguipulli", "salida": "08:30", "llegada": "18:00", "estado": "Próximo", "anden": 2},
    {"id": 7, "tipo": "salida", "empresa": "Buses JAC", "destino": "Villarrica", "origen": "Panguipulli", "salida": "09:00", "llegada": "09:40", "estado": "Próximo", "anden": 1},
    {"id": 8, "tipo": "salida", "empresa": "Jet Sur", "destino": "Santiago", "origen": "Panguipulli", "salida": "19:45", "llegada": "06:00", "estado": "Próximo", "anden": 6},
    {"id": 9, "tipo": "salida", "empresa": "Transantin", "destino": "Santiago", "origen": "Panguipulli", "salida": "20:00", "llegada": "06:30", "estado": "Próximo", "anden": 7},
    {"id": 18, "tipo": "salida", "empresa": "Buses JAC", "destino": "Valdivia", "origen": "Panguipulli", "salida": "12:00", "llegada": "14:15", "estado": "Próximo", "anden": 1},
    {"id": 19, "tipo": "salida", "empresa": "Tur Bus", "destino": "Temuco", "origen": "Panguipulli", "salida": "13:30", "llegada": "16:00", "estado": "Próximo", "anden": 2},
    {"id": 20, "tipo": "salida", "empresa": "Buses Galicia", "destino": "Lanco", "origen": "Panguipulli", "salida": "15:45", "llegada": "16:30", "estado": "Próximo", "anden": 3},


    {"id": 10, "tipo": "llegada", "empresa": "Buses Lafit", "destino": "Panguipulli", "origen": "Lanco", "salida": "05:45", "llegada": "06:30", "estado": "A tiempo", "anden": 3},
    {"id": 11, "tipo": "llegada", "empresa": "Tur Bus", "destino": "Panguipulli", "origen": "Temuco", "salida": "05:45", "llegada": "08:00", "estado": "A tiempo", "anden": 2},
    {"id": 12, "tipo": "llegada", "empresa": "Buses Galicia", "destino": "Panguipulli", "origen": "Valdivia", "salida": "05:45", "llegada": "07:55", "estado": "A tiempo", "anden": 1},
    {"id": 13, "tipo": "llegada", "empresa": "Buses JAC", "destino": "Panguipulli", "origen": "Villarrica", "salida": "07:00", "llegada": "07:40", "estado": "Próximo", "anden": 1},
    {"id": 14, "tipo": "llegada", "empresa": "Buses Liquiñe", "destino": "Panguipulli", "origen": "Coñaripe", "salida": "08:00", "llegada": "08:45", "estado": "Próximo", "anden": 4},
    {"id": 15, "tipo": "llegada", "empresa": "Tur Bus", "destino": "Panguipulli", "origen": "Santiago", "salida": "21:00", "llegada": "06:30", "estado": "Próximo", "anden": 2},
    {"id": 16, "tipo": "llegada", "empresa": "Jet Sur", "destino": "Panguipulli", "origen": "Santiago", "salida": "21:25", "llegada": "07:00", "estado": "Próximo", "anden": 6},
    {"id": 17, "tipo": "llegada", "empresa": "Transantin", "destino": "Panguipulli", "origen": "Neltume", "salida": "18:30", "llegada": "19:40", "estado": "Próximo", "anden": 7},
    {"id": 21, "tipo": "llegada", "empresa": "Buses JAC", "destino": "Panguipulli", "origen": "Valdivia", "salida": "10:00", "llegada": "12:15", "estado": "A tiempo", "anden": 1},
    {"id": 22, "tipo": "llegada", "empresa": "Tur Bus", "destino": "Panguipulli", "origen": "Temuco", "salida": "11:00", "llegada": "13:30", "estado": "A tiempo", "anden": 2},
    {"id": 23, "tipo": "llegada", "empresa": "Buses Galicia", "destino": "Panguipulli", "origen": "Lanco", "salida": "14:00", "llegada": "14:45", "estado": "Próximo", "anden": 3},
]


for h in horarios_data:
    h["salida"] = formato_ampm(h["salida"])
    h["llegada"] = formato_ampm(h["llegada"])

@app.route('/api/horarios', methods=['GET'])
def get_horarios():
    return jsonify(horarios_data)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != '' and os.path.exists(path):
        return send_from_directory('.', path)
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
