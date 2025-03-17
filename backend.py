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
    Si el usuario pregunta por algún atributo que no existe en la base de datos, cámbialo por el más parecido en nombre o lógica.
    Si el usuario pregunta por un nombre, pasa siempre tanto el nombre como el apellido.
    Intenta evitar usar la tabla resumen_evolucion, y recurre a ella solo si en el resto de tablas no hay nada parecido a lo que se pregunta.
    Si te preguntan por algún nombre propio en la pregunta, búscalo porque es muy probable que aparezca en la base de datos.
    """
    response = client.chat.completions.create(
        model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        messages=[{"role": "user", "content":prompt}],
        temperature=0.3
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
        # Imprimir la consulta SQL antes de ejecutarla
        print(f"Ejecutando consulta SQL: {sql_query}")

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

answers = [
    "El tratamiento incluye cambios en la alimentación, ejercicio y, en algunos casos, insulina o medicamentos orales.",
    "Es una condición en la que la presión arterial es demasiado alta, lo que puede aumentar el riesgo de enfermedades cardíacas.",
    "Los síntomas incluyen fiebre, tos, dificultad para respirar, fatiga y pérdida del olfato o gusto.",
    "Se recomienda una alimentación balanceada, actividad física regular y evitar el sedentarismo.",
    "Se aconseja descansar, mantenerse hidratado y acudir al médico si la fiebre es alta o persistente.",
]


# Función para generar un informe en PDF con preguntas y respuestas
def generate_pdf_report(questions):
    if not questions:
        raise ValueError("La lista de preguntas no puede estar vacía.")
    
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Configurar fuente y título
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Informe de Pacientes", ln=True, align="C")
    pdf.ln(10)  # Espacio después del título
    
    pdf.set_font("Arial", size=12)
    
    for i, question in enumerate(questions, start=1):
        retrieved_data = retrieve_relevant_data(question)
        enhanced_prompt = f"Datos recuperados:\n{retrieved_data}\n\nPregunta del usuario: {question}"
        
        response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[
                {"role": "system", "content": "Eres un asistente virtual dirigido a médicos. Debes responder a las preguntas usando la información proporcionada."},
                {"role": "user", "content": f"A continuación, se proporciona la información relevante y la pregunta del usuario: {enhanced_prompt}. Con estos datos, genera una respuesta clara, concisa y útil."}
            ],
            temperature=0.3
        )
        answer = response.choices[0].message.content.strip()
        
        # Agregar pregunta
        pdf.set_font("Arial", "B", 12)
        pdf.multi_cell(0, 8, f"{i}. {question}")  # Pregunta enumerada
        pdf.ln(2)  # Espacio entre pregunta y respuesta
        
        # Agregar respuesta con indentación
        pdf.set_font("Arial", size=11)
        paragraph_indent = 5
        list_indent = 10
        
        for line in answer.split("\n"):
            if line.strip():  # Evita líneas en blanco
                if re.match(r'^[0-9]+\.', line.strip()):  # Detectar listas enumeradas
                    pdf.cell(list_indent)  # Mayor indentación para listas
                    pdf.multi_cell(0, 6, line.strip(), align="L")
                else:
                    pdf.cell(paragraph_indent)  # Pequeña indentación para respuesta general
                    wrapped_lines = pdf.multi_cell(0, 6, line.strip(), align="L", border=0)
        
        pdf.ln(7)  # Espacio entre bloques de pregunta-respuesta
    
    pdf.output("informe_pacientes.pdf", "F")
    return "informe_pacientes.pdf"


@app.route('/get_patients', methods=['GET'])
def get_pacientes():
    """
    Devuelve los pacientes (nombre, apellidos, id) desde la base de datos.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Consulta SQL para obtener los pacientes
        cur.execute("SELECT PacienteID, Nombre, Apellido FROM resumen_pacientes")
        pacientes = cur.fetchall()
        conn.close()
        
        # Convertir los resultados a una lista de diccionarios
        pacientes_data = [{"id": paciente["PacienteID"], "nombre": paciente["Nombre"], "apellido": paciente["Apellido"]} for paciente in pacientes]
        
        return jsonify(pacientes_data)
    except Exception as e:
        conn.close()
        return jsonify({"error": f"Error al obtener los pacientes: {str(e)}"}), 500

@app.route("/send_message", methods=["POST"])
def send_message():
    data = request.json
    message = data.get("message", "")
    
    if not message:
        return jsonify({"error": "Mensaje vacío"}), 400

    try:
        classification_response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[{"role": "user", "content": f"Determina si el siguiente mensaje es una pregunta médica o una conversación general. Responde solo con 'médica' o 'general'. Si te pregunta algo sobre algún paciente, seguro que es médica\n\nMensaje: {message}"}],
            temperature=0.3
        )
        classification = classification_response.choices[0].message.content.strip().lower()
        
        if classification == "general":
            response_general = client.chat.completions.create(
                model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
                messages=[
                    {"role": "system", "content": "Eres un asistente virtual dirigido a médicos. Te van a pedir información sobre algún paciente y tendrás que responder utilizando la información que se te proporcione, de la mejor manera posible"},
                    {"role": "user", "content": message}
                ],
                temperature=0.3
            )
            return jsonify({"response": response_general.choices[0].message.content})
        
        retrieved_data = retrieve_relevant_data(message)
        enhanced_prompt = f"Datos recuperados:\n{retrieved_data}\n\nPregunta del usuario: {message}"

        print(f"\n\n{enhanced_prompt}\n\n")

        response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[
                {"role": "system", "content": "Eres un asistente virtual dirigido a médicos. Te van a pedir información sobre algún paciente y tendrás que responder utilizando la información que se te proporcione, de la mejor manera posible."},
                {"role": "user", "content":f"Aquí tienes la pregunta del usuario y la respuesta: {enhanced_prompt}. Con esos datos, da una respuesta lógica, buena, breve y convincente al médico que te pregunta. Si solo recibes los datos de un paciente, no menciones que solo tienes datos de uno; esa es la respuesta, ya filtrada de una gran base de datos, con lo que te tienes que mostar seguro y responder con determinación, y no olvides justificar por qué has elegido esa respuesta en caso de pregunta más bien abierta. Si no estás seguro de la respuesta, déjalo claro, pero sin pasarte. Si te piden un nombre y sabes el apellido, pasa tanto nombre como apellido directamente."}
            ],
            temperature=0.3
        )
        return jsonify({"response": response.choices[0].message.content})
    
    except Exception as e:
        print(f"Error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": "Ocurrió un error en el servidor", "details": str(e)}), 500

if __name__ == "__main__":
    # 🔥 Cambia el directorio de trabajo al directorio donde está el script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print("Directorio de trabajo cambiado a:", os.getcwd())  # 🔍 Para depuración
    generate_pdf_report(common_questions)
    print("🔥 Servidor corriendo en http://127.0.0.1:5000/")
    app.run(debug=True, port=5000)
