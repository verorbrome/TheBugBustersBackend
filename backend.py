import openai  # openai v1.0.0+
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os
import traceback
import sqlite3

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_database_schema():
    """
    Obtiene la estructura de la base de datos: nombres de tablas y columnas.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = cur.fetchall()

    schema_info = {}
    for table in tables:
        table_name = table[0]  # Asegurar que accedemos correctamente al nombre de la tabla
        cur.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cur.fetchall()]  # row[1] es el nombre de la columna
        schema_info[table_name] = columns
    
    conn.close()
    return schema_info if schema_info else {"error": "No hay tablas en la base de datos."}

def generate_sql_query(user_query, schema_info):
    """
    Usa el modelo para generar una consulta SQL basada en la estructura de la base de datos y la pregunta del usuario.
    """
    if "error" in schema_info:
        return "No hay tablas disponibles para realizar la consulta."
    
    schema_text = "\n".join([f"Tabla: {table}, Columnas: {', '.join(columns)}" for table, columns in schema_info.items()])
    prompt = f"Estructura de la base de datos:\n{schema_text}\n\nPregunta del usuario: {user_query}\n\nGenera una consulta SQL que pueda recuperar información relevante sobre un paciente si se menciona su nombre. Solo devuelve la consulta sin explicaciones."
    
    response = client.chat.completions.create(
        model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    
    return response.choices[0].message.content.strip()

def retrieve_relevant_data(user_query):
    """
    Recupera información relevante generando y ejecutando una consulta SQL adecuada.
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
        
        retrieved_texts = "\n".join(str(dict(row)) for row in results)
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

@app.route("/send_message", methods=["POST"])
def send_message():
    data = request.json
    message = data.get("message", "")
    
    if not message:
        return jsonify({"error": "Mensaje vacío"}), 400

    try:
        # Determinar si la consulta es médica o general usando el modelo
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
                    {"role": "system", "content": "Eres un asistente virtual dirigido a médicos. Debes responder con respeto y profesionalismo, ajustando la longitud de tu respuesta a la complejidad del mensaje."},
                    {"role": "user", "content": f"Responde al siguiente mensaje de manera concisa si es breve y de manera más detallada si es complejo. Además, menciona que el usuario puede hacer preguntas médicas si lo desea.\n\nMensaje: {message}"}
                ],
                temperature=0.7
            )
            return jsonify({"response": response_general.choices[0].message.content})
        
        # Recuperar datos relevantes antes de generar respuesta
        retrieved_data = retrieve_relevant_data(message)
        enhanced_prompt = f"Datos recuperados de la base de datos:\n{retrieved_data}\n\nPregunta del usuario: {message}\n\nResponde basándote en los datos proporcionados o indica si no hay suficiente información."

        response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[
                {"role": "system", "content": "Eres un asistente virtual para médicos. Responde de manera profesional y respetuosa, adaptando la longitud del mensaje a la pregunta realizada."},
                {"role": "user", "content": enhanced_prompt}
            ],
            temperature=0.7
        )
        message_response = response.choices[0].message.content
        return jsonify({"response": message_response})
    
    except Exception as e:
        print(f"Error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": "Ocurrió un error en el servidor", "details": str(e)}), 500

if __name__ == "__main__":
    print("🔥 Servidor corriendo en http://127.0.0.1:5000/")
    app.run(debug=True, port=5000)
