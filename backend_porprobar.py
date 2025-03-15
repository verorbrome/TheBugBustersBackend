import openai  # openai v1.0.0+
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
    """
    Obtiene la estructura completa de la base de datos: nombres de tablas, columnas y relaciones.
    """
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

def generate_sql_query(user_query, schema_info):
    """
    Usa el modelo para generar una consulta SQL considerando todas las tablas relacionadas.
    """
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
    
    Genera una consulta SQL que extraiga la información relevante considerando las relaciones entre tablas.
    Solo devuelve la consulta SQL sin explicaciones ni formato adicional.
    """
    
    response = client.chat.completions.create(
        model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    
    return response.choices[0].message.content.strip()

def retrieve_relevant_data(user_query):
    """
    Recupera información relevante ejecutando una consulta SQL adecuada sobre varias tablas si es necesario.
    """
    schema_info = get_database_schema()
    sql_query = generate_sql_query(user_query, schema_info)
    
    if "No hay tablas disponibles" in sql_query:
        return "No hay datos disponibles en la base de datos."
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute(sql_query)
        results = cur.fetchall()
        conn.close()
        
        retrieved_texts = [dict(row) for row in results]
        return retrieved_texts if retrieved_texts else "No se encontró información relevante."
    except Exception as e:
        conn.close()
        return f"Error ejecutando la consulta SQL: {str(e)}"

# Cargar variables de entorno
load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = "https://litellm.dccp.pbu.dedalus.com"

if not API_KEY:
    raise ValueError("API_KEY no encontrado en el archivo .env")

# Inicializar cliente de OpenAI
client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)

# Inicializar Flask
app = Flask(__name__)
CORS(app)  # Permite peticiones desde el frontend

common_questions = [
    "¿Cuál es el tratamiento para la diabetes?",
    "¿Qué es la hipertensión?",
    "¿Cuáles son los síntomas del COVID-19?",
    "¿Cómo se puede prevenir la obesidad?",
    "¿Qué debo hacer si tengo fiebre?",
]

# Función para generar un informe en PDF con las preguntas
def generate_pdf_report(questions):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Título del informe
    pdf.set_font("Arial", "B", 16)
    pdf.cell(200, 10, txt="Informe de Preguntas Comunes", ln=True, align="C")
    pdf.ln(10)

    # Agregar las preguntas
    pdf.set_font("Arial", size=12)
    for question in questions:
        pdf.multi_cell(0, 10, txt=f"- {question}\n")

    # Guardar el informe en un archivo PDF
    pdf.output("informe_preguntas_comunes.pdf")
    return "informe_preguntas_comunes.pdf"

@app.route("/send_message", methods=["POST"])
def send_message():
    data = request.json
    message = data.get("message", "")
    
    if not message:
        return jsonify({"error": "Mensaje vacío"}), 400

    try:
        classification_response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[{"role": "user", "content": f"Determina si el siguiente mensaje es una pregunta médica o una conversación general. Responde solo con 'médica' o 'general'.\n\nMensaje: {message}"}],
            temperature=0.7
        )
        classification = classification_response.choices[0].message.content.strip().lower()
        
        if classification == "general":
            response_general = client.chat.completions.create(
                model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
                messages=[
                    {"role": "system", "content": "Eres un asistente virtual dirigido a médicos."},
                    {"role": "user", "content": message}
                ],
                temperature=0.7
            )
            response_content = response_general.choices[0].message.content.strip()
            formatted_response = re.sub(r'(\d+\.)', r'\n\1', response_content)
            return jsonify({"response": formatted_response})
        
        retrieved_data = retrieve_relevant_data(message)
        enhanced_prompt = f"Datos recuperados:\n{retrieved_data}\n\nPregunta del usuario: {message}"

        response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[
                {"role": "system", "content": "Eres un asistente virtual para médicos."},
                {"role": "user", "content": enhanced_prompt}
            ],
            temperature=0.7
        )
        response_content = response.choices[0].message.content.strip()
        formatted_response = re.sub(r'(\d+\.)', r'\n\1', response_content)
        return jsonify({"response": formatted_response})
    
    except Exception as e:
        print(f"Error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": "Ocurrió un error en el servidor", "details": str(e)}), 500

if __name__ == "__main__":
    # 🔥 Cambia el directorio de trabajo al directorio donde está el script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print("Directorio de trabajo cambiado a:", os.getcwd())  # 🔍 Para depuración
    
    print("🔥 Servidor corriendo en http://127.0.0.1:5000/")
    app.run(debug=True, port=5000)
