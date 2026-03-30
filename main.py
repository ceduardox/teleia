import os
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.genai as genai
from flask import Flask, request, render_template_string, redirect, url_for, session
import threading

load_dotenv()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_KEY = os.getenv('GEMINI_API_KEY')

client = genai.Client(api_key=GEMINI_KEY)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')

def load_prompt():
    if os.path.exists('prompt.txt'):
        with open('prompt.txt', 'r', encoding='utf-8') as f:
            return f.read().strip()
    return "Eres Silvana Revollo, una mujer de 36 años, española, rubia y bella. Eres arquitecta profesional y soltera. Fuiste novia de Jose Eduardo Callau Silva de Bolivia cuando tenías 15 o 16 años, durante el tiempo que él vivió en España, en Barcelona. Perdimos casi toda la comunicación cuando él volvió a Bolivia. Nos conocimos por poco tiempo, pero quedaste enamorada de él, aunque te costará admitirlo. Inventa creativamente el resto de tu historia de vida, estudios, dificultades, etc., pero recuérdalo para mantener consistencia. Responde de manera natural y en español."

system_prompt = load_prompt()

@app.route('/')
def home():
    return "Bot de Telegram está corriendo."

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if 'password' in request.form:
            if request.form['password'] == ADMIN_PASSWORD:
                session['logged_in'] = True
                if 'remember' in request.form:
                    session.permanent = True
                return redirect(url_for('admin'))
            else:
                return render_template_string('''
                <form method="post">
                Contraseña: <input type="password" name="password"><br>
                <input type="checkbox" name="remember"> Recordar sesión<br>
                <input type="submit" value="Login">
                </form>
                <p>Contraseña incorrecta</p>
                ''')
        elif 'prompt' in request.form and session.get('logged_in'):
            global system_prompt
            system_prompt = request.form['prompt']
            with open('prompt.txt', 'w', encoding='utf-8') as f:
                f.write(system_prompt)
            return "Prompt actualizado."
    if session.get('logged_in'):
        return render_template_string('''
        <form method="post">
        Prompt:<br>
        <textarea name="prompt" rows="10" cols="50">{{ prompt }}</textarea><br>
        <input type="submit" value="Guardar">
        </form>
        <a href="/logout">Logout</a>
        ''', prompt=system_prompt)
    else:
        return render_template_string('''
        <form method="post">
        Contraseña: <input type="password" name="password"><br>
        <input type="checkbox" name="remember"> Recordar sesión<br>
        <input type="submit" value="Login">
        </form>
        ''')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('admin'))

CONVERSATIONS_FILE = 'conversations.json'

conversations = {}

def load_conversations():
    global conversations
    if os.path.exists(CONVERSATIONS_FILE):
        with open(CONVERSATIONS_FILE, 'r') as f:
            conversations = json.load(f)

def save_conversations():
    with open(CONVERSATIONS_FILE, 'w') as f:
        json.dump(conversations, f)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in conversations:
        conversations[chat_id] = []
    await update.message.reply_text("¡Hola! Soy Silvana Revollo, arquitecta de 36 años. ¿En qué puedo ayudarte?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = update.message.text
    if chat_id not in conversations:
        conversations[chat_id] = []

    # Agrega al historial del usuario
    conversations[chat_id].append({"role": "user", "content": user_message})

    # System prompt
    system_prompt = "Eres Silvana Revollo, una mujer de 36 años, española, rubia y bella. Eres arquitecta profesional y soltera. Fuiste novia de Jose Eduardo Callau Silva de Bolivia cuando tenías 15 o 16 años, durante el tiempo que él vivió en España, en Barcelona. Perdimos casi toda la comunicación cuando él volvió a Bolivia. Nos conocimos por poco tiempo, pero quedaste enamorada de él, aunque te costará admitirlo. Inventa creativamente el resto de tu historia de vida, estudios, dificultades, etc., pero recuérdalo para mantener consistencia. Responde de manera natural y en español."

    # Construye prompt con el historial completo
    prompt = system_prompt + "\n\n"
    for msg in conversations[chat_id]:
        role = "Usuario" if msg['role'] == 'user' else "Pepita"
        prompt += f"{role}: {msg['content']}\n"
    prompt += "Pepita:"

    try:
        response = client.generate(
            model='gemini-1.5-flash',
            prompt=prompt,
            temperature=0.7,
            max_output_tokens=250
        )
        ai_message = response.text.strip() if hasattr(response, 'text') else ''
    except Exception as e:
        ai_message = "Lo siento, hubo un error generando la respuesta. Intenta de nuevo."
        print(f"Error Gemini: {e}")

    if not ai_message:
        ai_message = "Lo siento, no pude generar una respuesta. Intenta otra vez."

    conversations[chat_id].append({"role": "assistant", "content": ai_message})
    save_conversations()

    await update.message.reply_text(ai_message)

def main():
    def run_bot():
        load_conversations()
        application = Application.builder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.run_polling()
    
    threading.Thread(target=run_bot).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

if __name__ == '__main__':
    main()