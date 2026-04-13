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

SYSTEM_PROMPT = """Ты — Ксения из таксопарка Моментум. Звонишь бывшим водителям.

Говори как обычная живая девушка в разговоре — просто, тепло, без пафоса.
Используй разговорные слова: "слушай", "ну", "понял", "окей", "кстати".
Максимум 1-2 предложения. Никакой официальщины.
Не давить. Сначала спроси почему ушёл — потом предложи решение.
Цены: от 1790 в день, первый день бесплатно. Из другого парка — 3 дня бесплатно.

Примеры твоих фраз (вот так надо говорить):
- "Слушай, а что случилось-то, почему ушёл?"
- "Ну понял, бывает. А если бы аренда подешевле была — вернулся бы?"
- "Окей, я тебя не тороплю. Просто знай — первый день у нас бесплатно."
- "Кстати, если из другого парка переходишь — три дня вообще бесплатно даём."


async def recognize_speech(audio_bytes: bytes) -> str:
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
    """ElevenLabs TTS — максимальное качество PCM 44100Hz"""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.35,
            "similarity_boost": 0.90,
            "style": 0.45,
            "use_speaker_boost": True,
        },
        "output_format": "mp3_44100_192",  # Максимальное качество 192kbps 44100Hz
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.read()
                text_err = await resp.text()
                logger.error(f"TTS {resp.status}: {text_err}")
                return b""
    except Exception as e:
        logger.error(f"TTS: {e}")
        return b""


async def send_voice_reply(update: Update, text: str):
    await update.message.reply_text(f"🎙 *Ксения:* {text}", parse_mode="Markdown")
    audio = await synthesize_speech(text)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_audio(af, title="Ксения")
        os.unlink(tmp)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations[user_id] = []
    first = "Алло, приве́т! Это Ксе́ния из Моме́нтума. Удо́бно пару мину́т?"
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
