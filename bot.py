# ... (imports)
import os
import logging
import time
import sqlite3
import hashlib
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import google.generativeai as genai

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DB_NAME = "bot_memory.db"

# Initialize Gemini
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    try:
        print("Available Models:")
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f" - {m.name}")
    except Exception as e:
        print(f"Error listing models: {e}")

# --- Database Management ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Table to track uploaded files
    c.execute('''CREATE TABLE IF NOT EXISTS files
                 (file_hash TEXT PRIMARY KEY,
                  telegram_file_id TEXT,
                  gemini_id TEXT,
                  file_name TEXT,
                  upload_date TEXT)''')
    
    # Table to track conversation history
    # Note: 'file_hash' is kept for legacy/audit but we will query by user_id mainly
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  file_hash TEXT,
                  role TEXT,
                  message TEXT,
                  timestamp TEXT)''')
    conn.commit()
    conn.close()

def get_file_by_hash(file_hash):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT gemini_id, file_name FROM files WHERE file_hash = ?", (file_hash,))
    result = c.fetchone()
    conn.close()
    return result

def save_file_record(file_hash, telegram_file_id, gemini_id, file_name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)",
              (file_hash, telegram_file_id, gemini_id, file_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def log_interaction(user_id, role, message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO history (user_id, file_hash, role, message, timestamp) VALUES (?, ?, ?, ?, ?)",
              (user_id, 'global', role, message, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_chat_history(user_id, limit=20):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Fetch global history for the user
    c.execute("SELECT role, message FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?", 
              (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return rows[::-1]

# Global user session: {user_id: {'files': [{'hash': '...', 'name': '...', 'gemini_id': '...'}]}}
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola! Soy tu **Analista Legal Multi-Documento**.\n\n"
        "1. **Sube tus PDFs** uno a uno (Leyes, Decretos, Informes).\n"
        "2. Yo los iré guardando en tu 'escritorio virtual'.\n"
        "3. **Haz preguntas generales** o específicas. Cruzaré la información de TODOS los documentos que hayas subido.\n\n"
        "Usa `/clear` si quieres borrar la mesa y empezar de cero."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GOOGLE_API_KEY:
        await update.message.reply_text("Error: Falta GOOGLE_API_KEY.")
        return

    msg = await update.message.reply_text("📥 Procesando documento... ⚙️")
    
    document = update.message.document
    if document.mime_type != 'application/pdf':
        await msg.edit_text("Solo acepto archivos PDF por ahora.")
        return

    try:
        # 1. Download and Hash
        file_obj = await context.bot.get_file(document.file_id)
        file_content = await file_obj.download_as_bytearray()
        
        file_hash = hashlib.sha256(file_content).hexdigest()
        file_name = document.file_name

        # 2. Check DB for Deduplication
        existing_record = get_file_by_hash(file_hash)
        
        gemini_id = None
        
        if existing_record:
            gemini_id, stored_name = existing_record
            await msg.edit_text(f"¡Ya conozco este documento ({stored_name})! Agregándolo a tu escritorio... 🧠")
        else:
            await msg.edit_text(f"Documento nuevo. Subiendo a Gemini... 🚀")
            
            # Save temp file
            temp_path = f"temp_{file_hash}.pdf"
            with open(temp_path, "wb") as f:
                f.write(file_content)
            
            gemini_file = genai.upload_file(path=temp_path, display_name=file_name)
            
            # Wait for processing
            while gemini_file.state.name == "PROCESSING":
                time.sleep(2)
                gemini_file = genai.get_file(gemini_file.name)
            
            if gemini_file.state.name == "FAILED":
                await msg.edit_text("Error: Gemini no pudo procesar el PDF.")
                if os.path.exists(temp_path): os.remove(temp_path)
                return

            gemini_id = gemini_file.name 
            save_file_record(file_hash, document.file_id, gemini_id, file_name)
            
            if os.path.exists(temp_path):
                os.remove(temp_path)

        # 3. Add to Session List
        user_id = update.effective_user.id
        if user_id not in user_sessions:
            user_sessions[user_id] = {'files': []}
        
        # Avoid adding duplicates to the active session list
        if not any(f['hash'] == file_hash for f in user_sessions[user_id]['files']):
            user_sessions[user_id]['files'].append({
                'hash': file_hash,
                'name': file_name,
                'gemini_id': gemini_id
            })

        count = len(user_sessions[user_id]['files'])
        await msg.edit_text(
            f"✅ **{file_name}** agregado.\n"
            f"📂 Tienes **{count}** documentos en tu escritorio.\n"
            "Sube más o hazme una pregunta sobre ellos."
        )

    except Exception as e:
        logging.error(f"Error: {e}")
        await msg.edit_text(f"Error crítico: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    session = user_sessions.get(user_id)
    if not session or not session.get('files'):
        await update.message.reply_text("Tu escritorio está vacío. Sube al menos un PDF primero.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    try:
        logging.info("--- Processing Multi-File Query ---")
        history = get_chat_history(user_id)
        log_interaction(user_id, 'user', text)
        
        # Prepare content list: [file1, file2, ..., prompt]
        request_content = []
        file_names = []
        
        for file_data in session['files']:
            try:
                # Resolve Name
                gemini_id = file_data.get('gemini_id')
                if gemini_id and "https://" in gemini_id and "/files/" in gemini_id:
                     gemini_id = "files/" + gemini_id.split("/files/")[-1]
                
                f_obj = genai.get_file(gemini_id)
                request_content.append(f_obj)
                file_names.append(file_data['name'])
            except Exception as e:
                logging.error(f"Error attaching file {file_data['name']}: {e}")
        
        if not request_content:
            await update.message.reply_text("Error: No pude recuperar los archivos de Gemini. Intenta /clear y resubir.")
            return

        system_instruction = (
            f"Actúa como un **Experto Analista Legal Senior**. Tienes a tu disposición {len(file_names)} documentos: {', '.join(file_names)}.\n"
            "**Tu Misión:** Proveer respuestas profundas, precisas y excelentemente redactadas basándote EXCLUSIVAMENTE en la información de estos documentos.\n\n"
            "**Directrices de Calidad:**\n"
            "1. **Razonamiento Profundo:** No te limites a citar. Analiza la intención, el contexto y las implicaciones de lo que lees.\n"
            "2. **Síntesis Cruzada:** Si la respuesta abarca varios documentos, integra la información de forma fluida. No listes documentos por separado a menos que sea necesario para comparar.\n"
            "3. **Estilo Profesional:** Usa un tono formal, claro y jurídico. Estructura tu respuesta con títulos, viñetas y párrafos bien formados.\n"
            "4. **Honestidad Intelectual:** Si la información no está en los documentos, dilo claramente. No inventes.\n"
            "5. **Cero Metadatos:** Entrega solo la respuesta final, lista para ser usada en un informe oficial."
        )
        
        chat_context = []
        for role, msg in history:
            chat_context.append(f"{'U' if role == 'user' else 'A'}: {msg}")
        
        full_prompt = (
            f"{system_instruction}\n"
            "--- Historial de Conversación ---\n" + "\n".join(chat_context) + "\n"
            "---------------------------------\n"
            f"**CONSULTA DEL CLIENTE:** {text}\n"
            "**RESPUESTA DEL EXPERTO:**"
        )

        request_content.append(full_prompt)

        # Prioritize 'PRO' models for higher intelligence, then 'FLASH' for speed/backup
        model_candidates = [
            'gemini-1.5-pro',          # Best reasoning
            'gemini-1.5-pro-001',      # Stable reasoning
            'gemini-2.0-flash-exp',    # New experimental (smart & fast)
            'gemini-1.5-flash',        # Fallback
            'gemini-1.5-flash-latest'
        ]

        response = None
        used_model = None
        last_error = None

        # Configuration for better quality
        gen_config = genai.GenerationConfig(
            temperature=0.3, # Lower temperature for more precise/analytical facts
            max_output_tokens=4000 # Allow long, detailed responses
        )

        for model_name in model_candidates:
            try:
                model = genai.GenerativeModel(model_name, generation_config=gen_config)
                response = model.generate_content(request_content)
                used_model = model_name
                break
            except Exception as e:
                logging.warning(f"Model {model_name} failed: {e}")
                last_error = e
        
        if not response:
            raise last_error or Exception("No valid models found.")

        # Clean answer, no metadata appended
        answer = response.text 
        
        log_interaction(user_id, 'assistant', answer)
        
        # Helper to send long messages
        async def send_long_message(text):
            # Telegram limit is 4096. We use 4000 to be safe.
            MAX_LENGTH = 4000
            for i in range(0, len(text), MAX_LENGTH):
                chunk = text[i:i+MAX_LENGTH]
                try:
                    await update.message.reply_text(chunk, parse_mode='Markdown')
                except Exception as e:
                    logging.warning(f"Markdown failed for chunk {i}, sending plain text: {e}")
                    await update.message.reply_text(chunk, parse_mode=None)

        try:
             await send_long_message(answer)
        except Exception as e:
            logging.error(f"Send Error: {e}")
            await update.message.reply_text("Error enviando la respuesta completa.")

    except Exception as e:
        logging.error(f"Generation Error: {e}")
        await update.message.reply_text(f"Lo siento, hubo un error técnico procesando tu solicitud: {str(e)}")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in user_sessions:
        del user_sessions[update.effective_user.id]
        await update.message.reply_text("Sesión limpiada.")
    else:
        await update.message.reply_text("Nada que limpiar.")

if __name__ == '__main__':
    init_db()
    if not TELEGRAM_TOKEN: exit("No Token")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('clear', clear))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    
    print("Bot Running...")
    app.run_polling()

