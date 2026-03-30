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
    conversations[chat_id].append({"role": "user", "content": user_message})
    
    # System prompt
    system_prompt = "Eres Pepita, una mujer de 30 años de España. Inventa el resto de tu personalidad, historia y respuestas. Responde de manera natural y en español."
    
    prompt = system_prompt + "\n\n"
    for msg in conversations[chat_id]:
        role = "Usuario" if msg['role'] == 'user' else "Pepita"
        prompt += f"{role}: {msg['content']}\n"
    prompt += f"Usuario: {user_message}\nPepita:"
    
    response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
    ai_message = response.candidates[0].content.parts[0].text
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