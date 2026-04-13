import asyncio
import logging
import aiohttp
import tempfile
import os
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
SALUTE_AUTH_KEY = os.environ.get("SALUTE_AUTH_KEY", "").strip()

conversations: dict = {}
salute_token: dict = {"access_token": None, "expires_at": 0}

SYSTEM_PROMPT = """Ты — Ксения, менеджер таксопарка Моментум. Звонишь бывшим водителям чтобы вернуть их в парк.

Говори ОЧЕНЬ коротко — максимум 1-2 коротких предложения. Это телефонный звонок.
Говори живо, тепло, по-человечески. Не давить, не навязывать.
Сначала выясни почему ушёл, потом предложи решение.
Цены: от 1790 р/день, первый день бесплатно. Из другого парка — 3 дня бесплатно."""


async def get_salute_token() -> str:
    """Получить или обновить токен SaluteSpeech"""
    import time
    if salute_token["access_token"] and time.time() < salute_token["expires_at"] - 60:
        return salute_token["access_token"]

    import uuid
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    headers = {
        "Authorization": f"Basic {SALUTE_AUTH_KEY}",
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = "scope=SALUTE_SPEECH_PERS"

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data, ssl=False) as resp:
            if resp.status == 200:
                result = await resp.json()
                salute_token["access_token"] = result["access_token"]
                salute_token["expires_at"] = result["expires_at"] / 1000
                return salute_token["access_token"]
            else:
                text = await resp.text()
                logger.error(f"SaluteSpeech auth error {resp.status}: {text}")
                return ""


async def recognize_speech(audio_bytes: bytes) -> str:
    """Yandex SpeechKit STT"""
    try:
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as f_in:
            f_in.write(audio_bytes)
            oga_path = f_in.name
        ogg_path = oga_path.replace(".oga", ".ogg")
        subprocess.run(
            ["ffmpeg", "-i", oga_path, "-c:a", "libopus", ogg_path, "-y", "-loglevel", "quiet"],
            check=True
        )
        with open(ogg_path, "rb") as f:
            ogg_bytes = f.read()
        os.unlink(oga_path)
        os.unlink(ogg_path)

        url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        params = {"folderId": YANDEX_FOLDER_ID, "lang": "ru-RU", "format": "oggopus"}
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params, headers=headers, data=ogg_bytes) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", "")
                return ""
    except Exception as e:
        logger.error(f"STT: {e}")
        return ""


async def generate_response(user_text: str, history: list) -> str:
    """Claude через OpenRouter"""
    history.append({"role": "user", "content": user_text})
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://momentum-bot.railway.app",
    }
    payload = {
        "model": "anthropic/claude-sonnet-4-5",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history],
        "max_tokens": 100,
        "temperature": 0.9
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"]
                    history.append({"role": "assistant", "content": reply})
                    return reply
                return "Простите, повторите пожалуйста."
    except Exception as e:
        logger.error(f"OpenRouter: {e}")
        return "Простите, повторите пожалуйста."


async def synthesize_speech(text: str) -> bytes:
    """SaluteSpeech TTS — живой русский голос"""
    token = await get_salute_token()
    if not token:
        return b""

    url = "https://smartspeech.sber.ru/rest/v1/text:synthesize"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/text",
    }
    params = {
        "voice": "Nec_24000",  # Женский голос Наталья
        "format": "wav16",
        "language": "ru-RU",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, params=params, data=text.encode("utf-8"), ssl=False) as resp:
                if resp.status == 200:
                    return await resp.read()
                text_err = await resp.text()
                logger.error(f"SaluteSpeech TTS {resp.status}: {text_err}")
                return b""
    except Exception as e:
        logger.error(f"SaluteSpeech TTS: {e}")
        return b""


async def send_voice_reply(update: Update, text: str):
    await update.message.reply_text(f"🎙 *Ксения:* {text}", parse_mode="Markdown")
    audio = await synthesize_speech(text)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_voice(af)
        os.unlink(tmp)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations[user_id] = []
    first = "Алло, добрый день! Это Ксения из таксопарка Моментум. Удобно пару минут поговорить?"
    conversations[user_id].append({"role": "assistant", "content": first})
    await send_voice_reply(update, first)
    await update.message.reply_text("🎤 Отвечайте голосом или текстом")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in conversations:
        conversations[user_id] = []
    await update.message.reply_text("🔄 Слушаю...")
    file = await context.bot.get_file(update.message.voice.file_id)
    audio_bytes = await file.download_as_bytearray()
    user_text = await recognize_speech(bytes(audio_bytes))
    if not user_text:
        await update.message.reply_text("❌ Не разобрала. Напишите текстом.")
        return
    await update.message.reply_text(f"👤 *Вы:* {user_text}", parse_mode="Markdown")
    reply = await generate_response(user_text, conversations[user_id])
    await send_voice_reply(update, reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in conversations:
        conversations[user_id] = []
    reply = await generate_response(update.message.text, conversations[user_id])
    await send_voice_reply(update, reply)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations[update.effective_user.id] = []
    await update.message.reply_text("🔄 /start — начать заново.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Ксения запущена!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
