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
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()

conversations: dict = {}

SYSTEM_PROMPT = """Ты — Ксения, менеджер таксопарка Моментум. Звонишь бывшим водителям чтобы вернуть их в парк.

Твои принципы:
- Говоришь как живой человек, тепло и по-дружески
- НЕ давишь и не навязываешь
- Сначала выясняешь почему ушёл, потом предлагаешь решение
- Знаешь реальные цены: от 1790 р/день (Tiggo 4 Pro), первый день бесплатно
- Если водитель из другого парка — 3 дня бесплатно

Отвечай коротко — 1-2 предложения максимум. Это телефонный разговор."""


async def recognize_speech(audio_bytes: bytes) -> str:
    """Yandex SpeechKit STT — конвертируем OGA в OGG"""
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
        params = {
            "folderId": YANDEX_FOLDER_ID,
            "lang": "ru-RU",
            "format": "oggopus",
        }
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params, headers=headers, data=ogg_bytes) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", "")
                else:
                    text = await resp.text()
                    logger.error(f"STT error {resp.status}: {text}")
                    return ""
    except Exception as e:
        logger.error(f"STT exception: {e}")
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
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history
        ],
        "max_tokens": 200,
        "temperature": 0.8
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"]
                    history.append({"role": "assistant", "content": reply})
                    return reply
                else:
                    text = await resp.text()
                    logger.error(f"OpenRouter error {resp.status}: {text}")
                    return "Простите, что-то пошло не так."
    except Exception as e:
        logger.error(f"OpenRouter exception: {e}")
        return "Простите, что-то пошло не так."


async def synthesize_speech(text: str) -> bytes:
    """ElevenLabs TTS"""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,
            "use_speaker_boost": True
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    text_err = await resp.text()
                    logger.error(f"TTS error {resp.status}: {text_err}")
                    return b""
    except Exception as e:
        logger.error(f"TTS exception: {e}")
        return b""


async def send_voice_reply(update: Update, text: str):
    """Отправить текст + голос"""
    await update.message.reply_text(f"🎙 *Ксения:* {text}", parse_mode="Markdown")
    audio = await synthesize_speech(text)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp_path = f.name
        with open(tmp_path, "rb") as af:
            await update.message.reply_voice(af)
        os.unlink(tmp_path)
    else:
        await update.message.reply_text("_(голос недоступен — ElevenLabs требует платный план с Railway IP)_", parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations[user_id] = []
    first = "Алло, добрый день! Это Ксения из таксопарка Моментум. Удобно пару минут поговорить?"
    conversations[user_id].append({"role": "assistant", "content": first})
    await send_voice_reply(update, first)
    await update.message.reply_text("🎤 Отвечайте голосовым сообщением или текстом")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in conversations:
        conversations[user_id] = []

    await update.message.reply_text("🔄 Слушаю...")

    file = await context.bot.get_file(update.message.voice.file_id)
    audio_bytes = await file.download_as_bytearray()

    user_text = await recognize_speech(bytes(audio_bytes))
    if not user_text:
        await update.message.reply_text("❌ Не смогла разобрать. Напишите текстом.")
        return

    await update.message.reply_text(f"👤 *Вы:* {user_text}", parse_mode="Markdown")
    reply = await generate_response(user_text, conversations[user_id])
    await send_voice_reply(update, reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in conversations:
        conversations[user_id] = []

    user_text = update.message.text
    reply = await generate_response(user_text, conversations[user_id])
    await send_voice_reply(update, reply)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations[update.effective_user.id] = []
    await update.message.reply_text("🔄 Диалог сброшен. /start — начать заново.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Ксения Telegram бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
