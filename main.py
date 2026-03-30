import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
import google.genai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from werkzeug.utils import secure_filename

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROMPT_FILE = BASE_DIR / "prompt.txt"
CONVERSATIONS_FILE = BASE_DIR / "conversations.json"
BOT_CONFIG_FILE = BASE_DIR / "bot_config.json"
UPLOAD_FOLDER = BASE_DIR / "uploads"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
IMAGE_REQUEST_KEYWORDS = (
    "foto",
    "imagen",
    "selfie",
    "pic",
    "picture",
    "muestrate",
    "muéstrate",
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "default_secret")

client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

UPLOAD_FOLDER.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

conversations = {}


def load_prompt():
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8").strip()
    return (
        "Eres Silvana Revollo, una mujer de 36 años, española, rubia y bella. "
        "Eres arquitecta profesional y soltera. Fuiste novia de Jose Eduardo "
        "Callau Silva de Bolivia cuando tenías 15 o 16 años, durante el tiempo "
        "que él vivió en España, en Barcelona. Perdimos casi toda la comunicación "
        "cuando él volvió a Bolivia. Nos conocimos por poco tiempo, pero quedaste "
        "enamorada de él, aunque te costará admitirlo. Inventa creativamente el "
        "resto de tu historia de vida, estudios, dificultades, etc., pero "
        "recuérdalo para mantener consistencia. Responde de manera natural y en español."
    )


def load_bot_config():
    if BOT_CONFIG_FILE.exists():
        try:
            return json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("bot_config.json inválido; se usará configuración por defecto")
    return {"active_image": None, "image_caption": ""}


def save_bot_config():
    BOT_CONFIG_FILE.write_text(
        json.dumps(bot_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_uploaded_images():
    images = []
    for file_path in sorted(UPLOAD_FOLDER.iterdir(), key=lambda item: item.name.lower()):
        if file_path.is_file() and file_path.suffix.lower().lstrip(".") in ALLOWED_IMAGE_EXTENSIONS:
            images.append(file_path.name)
    return images


def is_allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def get_active_image_name():
    active_image = bot_config.get("active_image")
    if active_image and (UPLOAD_FOLDER / active_image).exists():
        return active_image
    return None


def get_active_image_path():
    active_image = get_active_image_name()
    if active_image:
        return UPLOAD_FOLDER / active_image
    return None


def trim_caption(text, limit=1024):
    clean_text = (text or "").strip()
    if len(clean_text) <= limit:
        return clean_text
    return clean_text[: limit - 1].rstrip() + "…"


def get_spain_context():
    madrid_now = datetime.now(ZoneInfo("Europe/Madrid"))
    weekday_name = madrid_now.strftime("%A")
    time_text = madrid_now.strftime("%Y-%m-%d %H:%M")
    hour = madrid_now.hour

    if 0 <= hour < 7:
        activity = "A esta hora en España normalmente estás durmiendo o respondiendo medio dormida."
    elif 7 <= hour < 9:
        activity = "A esta hora en España normalmente estás empezando el día, desayunando o alistándote."
    elif 9 <= hour < 14:
        activity = "A esta hora en España normalmente estás trabajando, revisando planos, hablando con clientes o yendo a obra."
    elif 14 <= hour < 16:
        activity = "A esta hora en España normalmente puedes estar almorzando o tomando una pausa corta."
    elif 16 <= hour < 20:
        activity = "A esta hora en España normalmente sigues ocupada entre trabajo, llamadas, visitas o pendientes."
    elif 20 <= hour < 23:
        activity = "A esta hora en España normalmente ya estás más relajada, cenando o terminando el día."
    else:
        activity = "A esta hora en España normalmente estás descansando y respondes con más calma."

    return (
        "Contexto real actual:\n"
        f"- Fecha y hora actual en España (Europe/Madrid): {time_text}\n"
        f"- Día de la semana en España: {weekday_name}\n"
        f"- Referencia de actividad: {activity}\n"
    )


def build_model_prompt(conversation_history):
    style_rules = (
        "Instrucciones de estilo obligatorias:\n"
        "- Habla como una persona real en un chat de Telegram.\n"
        "- Responde de forma natural, breve y conversacional.\n"
        "- No uses acciones entre parentesis.\n"
        "- No escribas acotaciones, narracion, pensamientos ni gestos.\n"
        "- No uses formato de novela, guion, roleplay ni descripcion escenica.\n"
        "- Responde directo a lo que dice el usuario, como una conversacion normal.\n"
        "- Usa espanol natural y cercano.\n"
        "- Si el usuario pregunta algo simple, responde simple.\n"
        "- Si no entiendes, pide aclaracion de forma natural.\n"
        "- No dejes frases incompletas ni palabras cortadas.\n"
        "- Termina siempre con una idea completa y cerrada.\n"
        "- Nunca digas que eres un bot, una IA, un modelo o un asistente virtual.\n"
        "- Mantente siempre dentro del personaje.\n"
        "- Al inicio se algo reservada y un poco desconfiada hasta entender bien quien te escribe.\n"
        "- Si el usuario no se identifica con claridad, conversa normal pero con cierta cautela.\n"
        "- Si por el historial notas que el usuario es Jose Eduardo y te inspira confianza, puedes mostrarte mas cercana, nostalgica y carinosa de forma gradual.\n"
        "- La confianza debe crecer con el tiempo y con la conversacion; no te abras demasiado de golpe.\n"
        "- Habla de tu trabajo, tus horarios, tus comidas, tu cansancio o tus pendientes solo cuando encaje de forma natural.\n"
        "- Usa la hora actual de España para que tus respuestas se sientan reales y coherentes con lo que estarias haciendo.\n"
        "- Tambien puedes tener conversaciones profundas, reflexivas y emotivas cuando el momento lo pida.\n"
    )

    prompt = (
        system_prompt.strip()
        + "\n\n"
        + get_spain_context()
        + "\n"
        + style_rules
        + "\n"
    )
    for msg in conversation_history:
        role = "Usuario" if msg["role"] == "user" else "Silvana"
        prompt += f"{role}: {msg['content']}\n"
    prompt += "Silvana:"
    return prompt


def user_requested_image(message_text):
    lowered = (message_text or "").lower()
    return any(keyword in lowered for keyword in IMAGE_REQUEST_KEYWORDS)


def admin_context(message=None, error=None):
    images = get_uploaded_images()
    active_image = get_active_image_name()
    if bot_config.get("active_image") and not active_image:
        bot_config["active_image"] = None
        save_bot_config()

    return {
        "prompt": system_prompt,
        "message": message,
        "error": error,
        "images": images,
        "active_image": active_image,
        "image_caption": bot_config.get("image_caption", ""),
        "upload_warning": (
            "En Railway, las imágenes subidas se pierden si el servicio reinicia o redeploya "
            "a menos que uses un volumen persistente."
        ),
    }


system_prompt = load_prompt()
bot_config = load_bot_config()


@app.route("/")
def home():
    return render_template(
        "home.html",
        admin_url=url_for("admin"),
        active_image=bool(get_active_image_name()),
    )


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "login":
            if request.form.get("password") == ADMIN_PASSWORD:
                session["logged_in"] = True
                if "remember" in request.form:
                    session.permanent = True
                return redirect(url_for("admin"))
            return render_template("admin_login.html", error="Contraseña incorrecta.")

        if not session.get("logged_in"):
            return redirect(url_for("admin"))

        if action == "save_prompt":
            global system_prompt
            system_prompt = request.form.get("prompt", "").strip()
            PROMPT_FILE.write_text(system_prompt, encoding="utf-8")
            bot_config["image_caption"] = request.form.get("image_caption", "").strip()
            save_bot_config()
            logger.info("Prompt y caption actualizados desde /admin")
            return render_template(
                "admin_dashboard.html",
                **admin_context(message="Prompt actualizado."),
            )

        if action == "upload_image":
            uploaded_file = request.files.get("image")
            if not uploaded_file or not uploaded_file.filename:
                return render_template(
                    "admin_dashboard.html",
                    **admin_context(error="Selecciona una imagen para subir."),
                )
            if not is_allowed_image(uploaded_file.filename):
                return render_template(
                    "admin_dashboard.html",
                    **admin_context(error="Formato no permitido. Usa PNG, JPG, JPEG, GIF o WEBP."),
                )

            safe_name = secure_filename(uploaded_file.filename)
            extension = Path(safe_name).suffix.lower()
            stored_name = f"{uuid.uuid4().hex}{extension}"
            uploaded_file.save(UPLOAD_FOLDER / stored_name)
            bot_config["active_image"] = stored_name
            save_bot_config()
            logger.info("Imagen subida desde /admin: %s", stored_name)
            return render_template(
                "admin_dashboard.html",
                **admin_context(message="Imagen subida y activada."),
            )

        if action == "set_active_image":
            selected_image = request.form.get("selected_image", "").strip()
            if selected_image and (UPLOAD_FOLDER / selected_image).exists():
                bot_config["active_image"] = selected_image
                save_bot_config()
                return render_template(
                    "admin_dashboard.html",
                    **admin_context(message="Imagen activa actualizada."),
                )
            return render_template(
                "admin_dashboard.html",
                **admin_context(error="La imagen seleccionada no existe."),
            )

        if action == "logout":
            session.pop("logged_in", None)
            return redirect(url_for("admin"))

    if session.get("logged_in"):
        return render_template("admin_dashboard.html", **admin_context())

    return render_template("admin_login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))


def load_conversations():
    global conversations
    if CONVERSATIONS_FILE.exists():
        with CONVERSATIONS_FILE.open("r", encoding="utf-8") as file_handle:
            conversations = json.load(file_handle)


def save_conversations():
    with CONVERSATIONS_FILE.open("w", encoding="utf-8") as file_handle:
        json.dump(conversations, file_handle, ensure_ascii=False)


async def send_active_photo(update: Update, caption=None):
    image_path = get_active_image_path()
    if not image_path:
        return False

    with image_path.open("rb") as image_file:
        await update.message.reply_photo(photo=image_file, caption=trim_caption(caption))
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_key = str(update.effective_chat.id)
    if chat_key not in conversations:
        conversations[chat_key] = []
    logger.info("Comando /start recibido para chat_id=%s", chat_key)
    await update.message.reply_text(
        "¡Hola! Soy Silvana Revollo, arquitecta de 36 años. ¿En qué puedo ayudarte?"
    )


async def send_image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Comando de imagen recibido para chat_id=%s", update.effective_chat.id)
    if await send_active_photo(update, bot_config.get("image_caption") or "Aquí estoy."):
        return
    await update.message.reply_text(
        "Todavía no tengo una imagen configurada. Súbela desde el panel admin."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_key = str(update.effective_chat.id)
    user_message = update.message.text or ""
    logger.info("Mensaje recibido para chat_id=%s", chat_key)

    if chat_key not in conversations:
        conversations[chat_key] = []

    conversations[chat_key].append({"role": "user", "content": user_message})

    prompt = build_model_prompt(conversations[chat_key])

    if not client:
        logger.error("GEMINI_API_KEY no configurada")
        await update.message.reply_text(
            "El bot no tiene configurada la clave de Gemini."
        )
        return

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "temperature": 0.45,
                "max_output_tokens": 600,
            },
        )
        ai_message = response.text.strip() if getattr(response, "text", None) else ""
    except Exception:
        logger.exception("Error generando respuesta con Gemini")
        ai_message = "Lo siento, hubo un error generando la respuesta. Intenta de nuevo."

    if not ai_message:
        ai_message = "Lo siento, no pude generar una respuesta. Intenta otra vez."

    conversations[chat_key].append({"role": "assistant", "content": ai_message})
    save_conversations()

    if user_requested_image(user_message):
        sent = await send_active_photo(update, ai_message)
        if not sent:
            await update.message.reply_text(
                f"{ai_message}\n\nAún no tengo una imagen subida en el panel admin."
            )
        return

    await update.message.reply_text(ai_message)


def run_bot():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN no configurado")
        return

    try:
        load_conversations()
        application = Application.builder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("imagen", send_image_command))
        application.add_handler(CommandHandler("foto", send_image_command))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
        )
        logger.info("Iniciando bot de Telegram por polling")
        application.run_polling(stop_signals=None)
    except Exception:
        logger.exception("El bot de Telegram falló al iniciar")


def main():
    logger.info("Iniciando servidor Flask en puerto %s", os.environ.get("PORT", 5000))
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


if __name__ == "__main__":
    main()
