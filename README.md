# Telegram AI Chat Bot

Este proyecto crea un bot de Telegram que permite chatear con un personaje generado por IA usando la API de Google Gemini.

## Características

- Rol-playing con un personaje personalizado (ejemplo: Pepita, 30 años de España)
- Conversaciones persistentes por usuario
- Respuestas en español

## Requisitos

- Python 3.8+
- Cuenta de Telegram para crear el bot
- API key de Google Gemini

## Instalación

1. Clona o descarga este repositorio.

2. Instala las dependencias:
   ```
   pip install -r requirements.txt
   ```

3. Crea un bot en Telegram:
   - Ve a @BotFather en Telegram
   - Envía `/newbot` y sigue las instrucciones
   - Copia el token del bot

4. Obtén tu API key de Google Gemini:
   - Ve a https://makersuite.google.com/app/apikey
   - Crea una nueva API key

5. Edita el archivo `.env` y reemplaza los placeholders con tus claves:
   ```
   TELEGRAM_BOT_TOKEN=tu_token_aqui
   GEMINI_API_KEY=tu_api_key_aqui
   ```

## Uso

Ejecuta el bot:
```
python main.py
```

En Telegram, busca tu bot y envía `/start` para comenzar.

Luego, chatea normalmente. El bot responderá como Pepita.

## Personalización

Para cambiar el personaje, edita el `system_prompt` en `main.py`.

Para usar OpenAI en lugar de Gemini, instala `openai` y modifica el código.

## Notas

- Las conversaciones se guardan en `conversations.json` para persistencia.
- Asegúrate de no compartir tus claves API.