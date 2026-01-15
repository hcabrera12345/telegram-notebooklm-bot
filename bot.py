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

# --- Database Management ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Table to track uploaded files
    # Note: 'gemini_id' column will store the name like 'files/xxxx'
    c.execute('''CREATE TABLE IF NOT EXISTS files
                 (file_hash TEXT PRIMARY KEY,
                  telegram_file_id TEXT,
                  gemini_id TEXT,
                  file_name TEXT,
                  upload_date TEXT)''')
    
    # Table to track conversation history
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

def log_interaction(user_id, file_hash, role, message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO history (user_id, file_hash, role, message, timestamp) VALUES (?, ?, ?, ?, ?)",
              (user_id, file_hash, role, message, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_chat_history(user_id, file_hash, limit=10):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT role, message FROM history WHERE user_id = ? AND file_hash = ? ORDER BY id DESC LIMIT ?", 
              (user_id, file_hash, limit))
    rows = c.fetchall()
    conn.close()
    return rows[::-1]

# Global user session
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola! Soy tu asistente legal avanzado.\n\n"
        "1. **Envíame un PDF** (ley, decreto, etc).\n"
        "2. **Analizaré** su contenido y lo guardaré en mi memoria.\n"
        "3. **Pregúntame** lo que quieras. Recordaré nuestra conversación."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GOOGLE_API_KEY:
        await update.message.reply_text("Error: Falta GOOGLE_API_KEY.")
        return

    msg = await update.message.reply_text("Procesando documento... ⚙️")
    
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
            await msg.edit_text(f"¡Ya conozco este documento ({stored_name})! Cargando de memoria... 🧠")
        else:
            await msg.edit_text(f"Documento nuevo. Subiendo a Gemini... 🚀")
            
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

            # Store the NAME (files/xxxx), not the URI, for easier retrieval
            gemini_id = gemini_file.name 
            save_file_record(file_hash, document.file_id, gemini_id, file_name)
            
            if os.path.exists(temp_path):
                os.remove(temp_path)

        # 3. Set Session
        user_sessions[update.effective_user.id] = {
            'file_hash': file_hash,
            'file_name': file_name,
            'gemini_id': gemini_id
        }

        await msg.edit_text(
            f"✅ **{file_name}** listo.\n"
            "Hazme preguntas sobre él."
        )

    except Exception as e:
        logging.error(f"Error: {e}")
        await msg.edit_text(f"Error crítico: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if user_id not in user_sessions:
        await update.message.reply_text("Primero envíame un PDF para trabajar.")
        return

    session = user_sessions[user_id]
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    try:
        history = get_chat_history(user_id, session['file_hash'])
        log_interaction(user_id, session['file_hash'], 'user', text)
        
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Retrieve the File object using the ID/Name
        # If we have an old record with full URI, extraction might fail, 
        # so this is a fix going forward. Ideally user clears old files.
        try:
            gemini_id = session.get('gemini_id') or session.get('gemini_uri') # Fallback if key differs
            # Simple cleanup if it was a full URI (quick fix for migration)
            if gemini_id and "https://" in gemini_id:
                # Attempt to extract files/xxxx part
                # URI: .../v1beta/files/xxxxx
                if "/files/" in gemini_id:
                    gemini_id = "files/" + gemini_id.split("/files/")[-1]
            
            file_ref = genai.get_file(gemini_id)
        except Exception as file_err:
             logging.error(f"File Ref Error: {file_err}")
             await update.message.reply_text("Error recuperando el archivo de Gemini. Prueba /clear y sube de nuevo.")
             return

        system_instruction = (
            f"Eres un experto analista legal. Documento: '{session['file_name']}'. "
            "Responde basándote en el documento."
        )
        
        chat_context = []
        for role, msg in history:
            chat_context.append(f"{'U' if role == 'user' else 'A'}: {msg}")
        
        full_prompt = (
            f"{system_instruction}\n"
            "Historial:\n" + "\n".join(chat_context) + "\n"
            f"Pregunta: {text}\n"
        )

        response = model.generate_content([file_ref, full_prompt])
        answer = response.text
        
        log_interaction(user_id, session['file_hash'], 'assistant', answer)
        await update.message.reply_text(answer, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Generation Error: {e}")
        await update.message.reply_text("Error generando respuesta. Intenta reformular.")

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

