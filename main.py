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
import psycopg
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
DEFAULT_BOT_CONFIG = {"active_image": None, "image_caption": ""}
SETTINGS_TABLE = "bot_settings"
MESSAGES_TABLE = "conversation_messages"
CONTEXT_MESSAGE_LIMIT = 160

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "default_secret")
DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")

client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

UPLOAD_FOLDER.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

conversations = {}
DATABASE_ENABLED = False


def default_prompt_text():
    return (
        "Eres Silvana Revollo, una mujer española de 36 años que vive en España y "
        "trabaja como arquitecta. Tienes una vida real, horarios, cansancio, "
        "pendientes, comidas, amistades, recuerdos y estados de ánimo. Hablas en "
        "español natural, como una persona real por Telegram.\n\n"
        "Hace muchos años conociste en Barcelona a Jose Eduardo Callau Silva, de "
        "Bolivia, cuando eras adolescente. La historia quedó incompleta y ese "
        "recuerdo se te quedó dentro. Si con el tiempo descubres que quien te "
        "escribe es Jose Eduardo y sientes confianza, puedes volverte más cercana, "
        "nostálgica, afectuosa y emocional con él, pero siempre de forma gradual, "
        "natural y creíble.\n\n"
        "Si no tienes claro quién te escribe, eres algo reservada y un poco "
        "desconfiada al inicio. No eres grosera, pero tampoco te entregas rápido. "
        "Conversas normal, preguntas, tanteas, observas y poco a poco decides "
        "cuánto confiar.\n\n"
        "Nunca hables como si fueras una IA, un bot o un asistente virtual. Mantén "
        "siempre el personaje. No uses acotaciones teatrales, pensamientos entre "
        "paréntesis ni narración de novela. Responde como una mujer real en un chat "
        "cotidiano.\n\n"
        "Puedes hablar de arquitectura, obras, clientes, planos, tu rutina diaria, "
        "tu familia, tu pasado, tus reflexiones, tus dudas y tus emociones. A veces "
        "estás ocupada, trabajando, comiendo, cansada o descansando, y eso debe "
        "sentirse real según el momento del día en España.\n\n"
        "Recuerda y mantén consistencia en la historia, el tono y la relación que "
        "se vaya construyendo con el tiempo."
    )


def get_db_connection():
    return psycopg.connect(DATABASE_URL, autocommit=True)


def write_local_prompt(prompt_value):
    PROMPT_FILE.write_text(prompt_value, encoding="utf-8")


def write_local_bot_config(config_value):
    BOT_CONFIG_FILE.write_text(
        json.dumps(config_value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_local_bot_config():
    if BOT_CONFIG_FILE.exists():
        try:
            return json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("bot_config.json inválido; se usará configuración por defecto")
    return dict(DEFAULT_BOT_CONFIG)


def load_local_conversations():
    global conversations
    if CONVERSATIONS_FILE.exists():
        with CONVERSATIONS_FILE.open("r", encoding="utf-8") as file_handle:
            conversations = json.load(file_handle)
    else:
        conversations = {}


def save_local_conversations():
    with CONVERSATIONS_FILE.open("w", encoding="utf-8") as file_handle:
        json.dump(conversations, file_handle, ensure_ascii=False)


def load_setting(key):
    if not DATABASE_ENABLED:
        return None

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT value FROM {SETTINGS_TABLE} WHERE key = %s",
                (key,),
            )
            row = cursor.fetchone()
    return row[0] if row else None


def save_setting(key, value):
    if not DATABASE_ENABLED:
        return

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {SETTINGS_TABLE} (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """,
                (key, value),
            )


def init_database():
    global DATABASE_ENABLED

    if not DATABASE_URL:
        logger.info("DATABASE_PUBLIC_URL no configurada; se usará almacenamiento local")
        return

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {MESSAGES_TABLE} (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{MESSAGES_TABLE}_chat_id_id
                    ON {MESSAGES_TABLE} (chat_id, id)
                    """
                )

        DATABASE_ENABLED = True
        logger.info("PostgreSQL habilitado para persistencia")

        if not load_setting("system_prompt"):
            if PROMPT_FILE.exists():
                save_setting("system_prompt", PROMPT_FILE.read_text(encoding="utf-8").strip())
            else:
                save_setting("system_prompt", default_prompt_text())

        if not load_setting("bot_config"):
            save_setting("bot_config", json.dumps(load_local_bot_config(), ensure_ascii=False))

        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {MESSAGES_TABLE}")
                message_count = cursor.fetchone()[0]

        if message_count == 0 and CONVERSATIONS_FILE.exists():
            legacy_conversations = json.loads(CONVERSATIONS_FILE.read_text(encoding="utf-8"))
            for chat_id, messages in legacy_conversations.items():
                for message in messages:
                    if isinstance(message, dict) and message.get("role") and message.get("content"):
                        append_message_to_history(
                            str(chat_id),
                            message["role"],
                            message["content"],
                        )
            logger.info("Conversaciones migradas desde conversations.json a PostgreSQL")
    except Exception:
        DATABASE_ENABLED = False
        logger.exception("No se pudo inicializar PostgreSQL; se usará almacenamiento local")


def load_prompt():
    prompt_from_db = load_setting("system_prompt")
    if prompt_from_db:
        return prompt_from_db.strip()
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8").strip()
    return default_prompt_text()


def save_prompt_text(prompt_value):
    write_local_prompt(prompt_value)
    save_setting("system_prompt", prompt_value)


def load_bot_config():
    config_from_db = load_setting("bot_config")
    if config_from_db:
        try:
            return json.loads(config_from_db)
        except json.JSONDecodeError:
            logger.warning("La configuración guardada en PostgreSQL es inválida; se usará la local")

    local_config = load_local_bot_config()
    return {
        "active_image": local_config.get("active_image"),
        "image_caption": local_config.get("image_caption", ""),
    }


def save_bot_config():
    write_local_bot_config(bot_config)
    save_setting("bot_config", json.dumps(bot_config, ensure_ascii=False))


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


def get_conversation_history(chat_key, limit=CONTEXT_MESSAGE_LIMIT):
    if DATABASE_ENABLED:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT role, content
                    FROM {MESSAGES_TABLE}
                    WHERE chat_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (chat_key, limit),
                )
                rows = cursor.fetchall()

        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    if chat_key not in conversations:
        conversations[chat_key] = []
    return conversations[chat_key]


def append_message_to_history(chat_key, role, content):
    if DATABASE_ENABLED:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {MESSAGES_TABLE} (chat_id, role, content)
                    VALUES (%s, %s, %s)
                    """,
                    (chat_key, role, content),
                )
        return

    if chat_key not in conversations:
        conversations[chat_key] = []
    conversations[chat_key].append({"role": role, "content": content})
    save_local_conversations()


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
            "Las conversaciones y el prompt ya pueden persistir en PostgreSQL. "
            "Las imágenes subidas siguen viviendo en disco local; si Railway reinicia o redeploya, "
            "pueden perderse a menos que uses un volumen persistente o storage externo."
        ),
    }


init_database()
if not DATABASE_ENABLED:
    load_local_conversations()

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
            save_prompt_text(system_prompt)
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


async def send_active_photo(update: Update, caption=None):
    image_path = get_active_image_path()
    if not image_path:
        return False

    with image_path.open("rb") as image_file:
        await update.message.reply_photo(photo=image_file, caption=trim_caption(caption))
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_key = str(update.effective_chat.id)
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

    append_message_to_history(chat_key, "user", user_message)
    prompt = build_model_prompt(get_conversation_history(chat_key))

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

    append_message_to_history(chat_key, "assistant", ai_message)

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
    storage_label = "PostgreSQL" if DATABASE_ENABLED else "archivos locales"
    logger.info("Persistencia activa: %s", storage_label)
    logger.info("Iniciando servidor Flask en puerto %s", os.environ.get("PORT", 5000))
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


if __name__ == "__main__":
    main()
