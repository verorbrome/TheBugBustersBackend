import openai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os
import traceback
import sqlite3
from fpdf import FPDF
import re

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_database_schema():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cur.fetchall()]
    
    schema_info = {}
    for table in tables:
        cur.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cur.fetchall()]
        
        cur.execute(f"PRAGMA foreign_key_list({table})")
        foreign_keys = [{"from": row[3], "to": row[2], "table": row[2]} for row in cur.fetchall()]
        
        schema_info[table] = {"columns": columns, "foreign_keys": foreign_keys}
    
    conn.close()
    return schema_info if schema_info else {"error": "No hay tablas en la base de datos."}

def generate_sql_query(user_query, schema_info, patient_id=None):
    if "error" in schema_info:
        return "No hay tablas disponibles para realizar la consulta."
    
    schema_text = "\n".join(
        [f"Tabla: {table}, Columnas: {', '.join(info['columns'])}, Relaciones: {info['foreign_keys']}" 
         for table, info in schema_info.items()]
    )
    
    prompt = f"""
    Estructura de la base de datos:
    {schema_text}
    
    Pregunta del usuario: {user_query}
    
    Genera una consulta SQL válida que extraiga la información relevante considerando las relaciones entre tablas.
    Si se proporciona un ID de paciente ({patient_id}), incluye siempre el filtro 'WHERE PacienteID = {patient_id}' para limitar los resultados a ese paciente.
    Asegúrate de que la consulta sea sintácticamente correcta y utilice solo columnas existentes en las tablas.
    Si la pregunta es vaga (como "¿Cómo se encuentra?"), selecciona columnas relevantes como estado de salud (EstadoAlIngreso, DiagnosticoPrincipal), signos vitales (PresionSistolica, Temperatura, etc.) o notas (Nota), si están disponibles; de lo contrario, usa las columnas básicas (Nombre, Apellido).
    Solo devuelve la consulta SQL sin explicaciones ni formato adicional.
    No digas que no tienes acceso a una base de datos.
    """
    
    try:
        response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        sql_query = response.choices[0].message.content.strip()
        
        if not sql_query.upper().startswith("SELECT"):
            raise ValueError("La consulta generada no es válida (no es SELECT).")
        
        if patient_id and "PACIENTEID" not in sql_query.upper():
            if "WHERE" in sql_query.upper():
                sql_query += f" AND PacienteID = {patient_id}"
            else:
                sql_query += f" WHERE PacienteID = {patient_id}"
        
        return sql_query
    except Exception as e:
        print(f"Error generando consulta SQL: {str(e)}")
        if patient_id:
            return f"SELECT Nombre, Apellido FROM resumen_pacientes WHERE PacienteID = {patient_id}"
        return "SELECT * FROM resumen_pacientes LIMIT 1"

def retrieve_relevant_data(user_query, patient_id=None):
    schema_info = get_database_schema()
    sql_query = generate_sql_query(user_query, schema_info, patient_id)
    
    if "No hay tablas disponibles" in sql_query:
        return "No hay datos disponibles en la base de datos."
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        print(f"Ejecutando consulta SQL: {sql_query}")
        cur.execute(sql_query)
        results = cur.fetchall()
        conn.close()
        
        retrieved_texts = [dict(row) for row in results]
        return retrieved_texts if retrieved_texts else f"No se encontró información específica para el paciente {patient_id} en la base de datos." if patient_id else "No se encontró información relevante."
    except Exception as e:
        conn.close()
        error_msg = f"Error ejecutando la consulta SQL: {str(e)}"
        print(error_msg)
        return error_msg

# Cargar variables de entorno
load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = "https://litellm.dccp.pbu.dedalus.com"

if not API_KEY:
    raise ValueError("API_KEY no encontrado en el archivo .env")

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)

app = Flask(__name__)
CORS(app)

common_questions = [
    "¿Cuál es el tratamiento para la diabetes?",
    "¿Qué es la hipertensión?",
    "¿Cuáles son los síntomas del COVID-19?",
    "¿Cómo se puede prevenir la obesidad?",
    "¿Qué debo hacer si tengo fiebre?",
]

answers = [
    "El tratamiento incluye cambios en la alimentación, ejercicio y, en algunos casos, insulina o medicamentos orales.",
    "Es una condición en la que la presión arterial es demasiado alta, lo que puede aumentar el riesgo de enfermedades cardíacas.",
    "Los síntomas incluyen fiebre, tos, dificultad para respirar, fatiga y pérdida del olfato o gusto.",
    "Se recomienda una alimentación balanceada, actividad física regular y evitar el sedentarismo.",
    "Se aconseja descansar, mantenerse hidratado y acudir al médico si la fiebre es alta o persistente.",
]

def generate_pdf_report(questions, answers):
    if len(questions) != len(answers):
        raise ValueError("Las listas de preguntas y respuestas deben tener la misma longitud.")
    
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Informe de Pacientes", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Arial", size=12)
    for i, (question, answer) in enumerate(zip(questions, answers), start=1):
        pdf.set_font("Arial", "B", 12)
        pdf.multi_cell(0, 8, f"{i}. {question}")
        pdf.set_font("Arial", size=11)
        pdf.multi_cell(0, 8, answer)
        pdf.ln(5)

    pdf.output("informe_pacientes.pdf", "F")
    return "informe_pacientes.pdf"

@app.route('/get_patients', methods=['GET'])
def get_pacientes():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT PacienteID, Nombre, Apellido FROM resumen_pacientes")
        pacientes = cur.fetchall()
        conn.close()
        
        pacientes_data = [{"id": paciente["PacienteID"], "nombre": paciente["Nombre"], "apellido": paciente["Apellido"]} for paciente in pacientes]
        return jsonify(pacientes_data)
    except Exception as e:
        conn.close()
        return jsonify({"error": f"Error al obtener los pacientes: {str(e)}"}), 500

@app.route("/send_message", methods=["POST"])
def send_message():
    data = request.json
    message = data.get("message", "")
    patient_id = data.get("patientId")
    history = data.get("history", [])  # Historial de los últimos mensajes
    
    if not message:
        return jsonify({"error": "Mensaje vacío"}), 400

    try:
        # Clasificar si la pregunta está relacionada con el paciente usando IA
        classification_prompt = f"""
        Determina si la siguiente pregunta está relacionada con un paciente específico o es una pregunta general no relacionada con un paciente en particular.
        - Si la pregunta menciona "paciente", "él", "ella", "su", un ID numérico ({patient_id} si está presente), incluye algún verbo en la tercera persona del singular, o términos como "diagnóstico", "tratamiento", "estado", "cómo se encuentra", "historial", etc., clasifícala como 'patient_related'. 
        - Antes de clasificar una pregunta como 'general', accede a los datos del paciente y mira si puedes aportar información útil relacionada con la pregunta sobre el paciente.
        - Si la pregunta es sobre temas médicos generales (como "¿Qué es la hipertensión?") o no tiene referencia implícita a un paciente, clasifícala como 'general'.
        Responde solo con 'patient_related' o 'general'.

        Pregunta: {message}
        """
        classification_response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[{"role": "user", "content": classification_prompt}],
            temperature=0.3
        )
        classification = classification_response.choices[0].message.content.strip().lower()

        # Construir los mensajes para el modelo, incluyendo el historial
        messages = []
        if history:
            messages.extend(history)  # Añadir el historial de los últimos 10 mensajes

        if patient_id and classification == "patient_related":
            # Pregunta relacionada con el paciente seleccionado
            context_prompt = f"Eres un asistente virtual dirigido a médicos. La conversación es sobre el paciente con ID {patient_id}. Responde utilizando la información de este paciente disponible en la base de datos. Si no hay datos suficientes para responder, sugiere consultar el historial médico físico. No digas que no tienes acceso a una base de datos específica de pacientes."
            retrieved_data = retrieve_relevant_data(message, patient_id)
            enhanced_prompt = f"Datos recuperados del paciente {patient_id}:\n{retrieved_data}\n\nPregunta del usuario: {message}"
        else:
            # Pregunta general, incluso con paciente seleccionado
            context_prompt = "Eres un asistente virtual dirigido a médicos. Responde de manera breve y profesional a preguntas generales o específicas, utilizando tu conocimiento general si no se requiere información de un paciente específico. Si te pregunta por un paciente específico, consulta la base de datos y responde según los datos que te pidan de dicho paciente."
            enhanced_prompt = f"Pregunta del usuario: {message}"

        messages.append({"role": "system", "content": context_prompt})
        messages.append({"role": "user", "content": enhanced_prompt})

        response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=messages,
            temperature=0.3
        )
        return jsonify({"response": response.choices[0].message.content})
    
    except Exception as e:
        print(f"Error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": "Ocurrió un error en el servidor", "details": str(e)}), 500

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print("Directorio de trabajo cambiado a:", os.getcwd())
    generate_pdf_report(common_questions, answers)
    print("🔥 Servidor corriendo en http://127.0.0.1:5000/")
    app.run(debug=True, port=5000)
