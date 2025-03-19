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

def generate_sql_query(user_query, schema_info):
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
    
    Genera una consulta SQL válida para SQLite que extraiga información relevante de forma general (sin filtrar por un paciente específico a menos que se indique explícitamente en la pregunta).
    Devuelve datos agregados o representativos de todos los pacientes según la pregunta (por ejemplo, promedios, conteos o listas).
    Asegúrate de que:
    - La consulta sea sintácticamente correcta y use SOLO columnas existentes en las tablas listadas.
    - Usa GROUP_CONCAT (sin SEPARATOR, ya que SQLite no lo soporta) en lugar de STRING_AGG para combinar valores como Medicamentos o Procedimientos.
    - Si la pregunta menciona "resultados de laboratorio", usa la tabla resumen_lab_iniciales y columnas como Glucosa, Hemoglobina, etc.
    - Si la pregunta es vaga (como "muestra una tabla"), selecciona columnas relevantes como PacienteID, Nombre, Apellido, y signos vitales o datos de laboratorio.
    - Ordena las columnas en el SELECT en este orden prioritario cuando estén disponibles: PacienteID primero, luego Nombre, Apellido, Fecha, y después el resto de columnas en el orden que consideres lógico (no alfabético).
    - Si ordenas por PacienteID (por ejemplo, con ORDER BY), usa CAST(PacienteID AS INTEGER) para asegurar un orden numérico correcto (1, 2, 3, ...), no como texto (1, 10, 100, ...).
    - Si la pregunta no especifica un orden, incluye ORDER BY CAST(PacienteID AS INTEGER) por defecto para tablas con PacienteID.
    Solo devuelve la consulta SQL sin explicaciones ni formato adicional.
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
        
        return sql_query
    except Exception as e:
        print(f"Error generando consulta SQL: {str(e)}")
        return """
        SELECT 
            rp.PacienteID,
            rp.Nombre,
            rp.Apellido,
            rp.FechaIngreso,
            AVG(rp.PresionSistolica) as PromedioPresion,
            AVG(rp.Temperatura) as PromedioTemperatura
        FROM 
            resumen_pacientes rp
        GROUP BY 
            rp.PacienteID, rp.Nombre, rp.Apellido, rp.FechaIngreso
        ORDER BY 
            CAST(rp.PacienteID AS INTEGER)
        """

def retrieve_relevant_data(user_query):
    schema_info = get_database_schema()
    sql_query = generate_sql_query(user_query, schema_info)
    
    if "No hay tablas disponibles" in sql_query:
        return "No hay datos disponibles en la base de datos."
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    max_attempts = 3  # Número máximo de reintentos
    attempt = 0
    
    while attempt < max_attempts:
        try:
            print(f"Ejecutando consulta SQL (intento {attempt + 1}): {sql_query}")
            cur.execute(sql_query)
            # Obtener nombres de columnas en el orden del SELECT
            column_names = [description[0] for description in cur.description]
            results = cur.fetchall()
            # Devolver filas como listas ordenadas en lugar de diccionarios
            retrieved_texts = [list(row) for row in results]
            conn.close()
            return {"columns": column_names, "data": retrieved_texts} if retrieved_texts else "No se encontró información relevante en la base de datos."
        except sqlite3.OperationalError as e:
            error_msg = str(e)
            print(f"Error ejecutando la consulta SQL: {error_msg}")
            
            # Manejar funciones no soportadas como STRING_AGG
            if "no such function: STRING_AGG" in error_msg:
                sql_query = sql_query.replace("STRING_AGG", "GROUP_CONCAT")
                print(f"Consulta ajustada reemplazando STRING_AGG por GROUP_CONCAT: {sql_query}")
            # Manejar errores de "no such column"
            elif "no such column" in error_msg:
                match = re.search(r"no such column: ([\w\.]+)", error_msg)
                if match:
                    bad_column = match.group(1)
                    print(f"Columna problemática detectada: {bad_column}")
                    sql_query_lines = sql_query.split('\n')
                    new_query_lines = [line for line in sql_query_lines if bad_column not in line]
                    sql_query = '\n'.join(new_query_lines).strip()
                    print(f"Consulta ajustada eliminando columna: {sql_query}")
                else:
                    conn.close()
                    return f"Error ejecutando la consulta SQL: {error_msg}"
            else:
                conn.close()
                return f"Error ejecutando la consulta SQL: {error_msg}"
            
            if not sql_query.strip():
                conn.close()
                return "No se pudo generar una consulta válida tras eliminar partes problemáticas."
            
            attempt += 1
            if attempt == max_attempts:
                conn.close()
                return f"Error persistente tras {max_attempts} intentos: no se pudo ajustar la consulta."
        except Exception as e:
            error_msg = f"Error inesperado ejecutando la consulta SQL: {str(e)}"
            print(error_msg)
            conn.close()
            return error_msg
    
    conn.close()
    return "No se pudo procesar la consulta tras varios intentos."

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
        pacientes_data = [{"id": paciente["PacienteID"], "nombre": paciente["Nombre"], "apellido": paciente["Apellido"]} for paciente in pacientes]
        return jsonify(pacientes_data)
    except Exception as e:
        return jsonify({"error": f"Error al obtener los pacientes: {str(e)}"}), 500
    finally:
        conn.close()

@app.route("/send_message", methods=["POST"])
def send_message():
    data = request.json
    message = data.get("message", "")
    patient_id = data.get("patientId")
    history = data.get("history", [])
    
    if not message:
        return jsonify({"error": "Mensaje vacío"}), 400

    try:
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

        messages = []
        if history:
            messages.extend(history)

        if patient_id and classification == "patient_related":
            context_prompt = f"Eres un asistente virtual dirigido a médicos. La conversación es sobre el paciente con ID {patient_id}. Responde utilizando la información de este paciente disponible en la base de datos. Si no hay datos suficientes para responder, sugiere consultar el historial médico físico. No digas que no tienes acceso a una base de datos específica de pacientes."
            retrieved_data = retrieve_relevant_data(f"Datos del paciente con ID {patient_id}")
            enhanced_prompt = f"Datos recuperados del paciente {patient_id}:\n{retrieved_data}\n\nPregunta del usuario: {message}"
        else:
            context_prompt = "Eres un asistente virtual dirigido a médicos. Responde de manera breve y profesional a preguntas generales o específicas, utilizando tu conocimiento general si no se requiere información de un paciente específico."
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
        print(f"Error en send_message: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": "Ocurrió un error en el servidor", "details": str(e)}), 500

@app.route('/get_patient_data', methods=['POST'])
def get_patient_data():
    data = request.json
    message = data.get("message", "")
    
    if not message:
        return jsonify({"error": "Mensaje no proporcionado"}), 400

    try:
        retrieved_data = retrieve_relevant_data(message)
        if isinstance(retrieved_data, str):
            print(f"No se pudieron recuperar datos: {retrieved_data}")
            return jsonify({"error": retrieved_data}), 400
        
        print(f"Datos recuperados para chat general: {retrieved_data}")
        return jsonify(retrieved_data)
    except Exception as e:
        print(f"Error en get_patient_data: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": f"Error al procesar los datos: {str(e)}"}), 500

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print("Directorio de trabajo cambiado a:", os.getcwd())
    generate_pdf_report(common_questions, answers)
    print("🔥 Servidor corriendo en http://127.0.0.1:5000/")
    app.run(debug=True, port=5000)
