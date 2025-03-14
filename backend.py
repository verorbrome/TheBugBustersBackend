import openai  # openai v1.0.0+
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os
import traceback

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
        response = client.chat.completions.create(
            model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[{"role": "user", "content": message}],
            #max_tokens=10000
        )
        message_response = response.choices[0].message.content
        return jsonify({"response": message_response})
    
    except Exception as e:
        print(f"Error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": "Ocurrió un error en el servidor", "details": str(e)}), 500

# Función para procesar mensajes y estructurarlos mejor

def process_message(message):
    request = client.chat.completions.create(
        model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        messages=[
            {"role": "system", "content": "Transforma el siguiente texto en un prompt bien estructurado..."},
            {"role": "user", "content": message}
        ],
        temperature=0.8,
        stream=False
    )
    return request.choices[0].message.content

# Función para generar respuestas con el modelo

def chat(prompt):
    request = client.chat.completions.create(
        model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        messages=[
            {"role": "system", "content": "Eres un experto en fútbol..."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.8,
        stream=False
    )
    return request.choices[0].message.content

if __name__ == "__main__":
    print("🔥 Servidor corriendo en http://127.0.0.1:5000/")
    app.run(debug=True, port=5000)
