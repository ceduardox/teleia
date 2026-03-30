import os
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.genai as genai

load_dotenv()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_KEY = os.getenv('GEMINI_API_KEY')

client = genai.Client(api_key=GEMINI_KEY)

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
    await update.message.reply_text("¡Hola! Soy Pepita, de 30 años de España. ¿Qué quieres charlar?")

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
    load_conversations()
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()