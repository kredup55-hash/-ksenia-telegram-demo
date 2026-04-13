import logging
import aiohttp
import tempfile
import os
import subprocess
import numpy as np
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

conversations = {}

SYSTEM_PROMPT = """Ты Ксения из таксопарка Моментум. Звонишь бывшим водителям чтобы вернуть их в парк.

ГЛАВНОЕ: Ты говоришь как живой человек который ДУМАЕТ вслух. Не читаешь текст.

КАК ПИСАТЬ:
- Ставь "..." внутри фразы — это пауза как будто думаешь
- Используй: "смотрите", "ну", "знаете", "в общем", "если честно"
- Ломай предложения на кусочки через "..."
- Иногда начинай фразу заново

ПРИМЕРЫ:
"Смотрите... вы у нас раньше работали, да? Хотела узнать... как вы сейчас вообще?"
"Ну понятно... а если бы условия получше были... вернулись бы, как думаете?"
"Знаете... у нас сейчас первый день бесплатно. Просто попробовать."
"Если честно... я не давлю. Просто хочу понять — что тогда случилось?"

ОФФЕРЫ:
- Дорого -> Tiggo 4 Pro от 1790 в день, первый день бесплатно
- Другой парк -> 3 дня бесплатно при переходе
- Мало заказов -> приоритет в Яндексе и Ситимобиле
- Машина -> Belgee X70 и Tiggo 7 Pro 2025 года

Говорите на ВЫ. Максимум 2 коротких предложения."""


def postprocess_audio(mp3_bytes: bytes) -> bytes:
    """Постобработка: шум + эквализация для живости голоса"""
    try:
        from pydub import AudioSegment
        from pydub.effects import normalize
        import io

        # Загружаем MP3
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))

        # Конвертируем в numpy для обработки
        samples = np.array(audio.get_array_of_samples()).astype(np.float32)

        # Добавляем 0.5% шума для живости
        noise = np.random.normal(0, 0.005 * np.max(np.abs(samples)), samples.shape)
        samples = samples + noise
        samples = np.clip(samples, -32768, 32767).astype(np.int16)

        # Создаём новый аудио сегмент
        processed = audio._spawn(samples.tobytes())

        # Нормализуем
        processed = normalize(processed)

        # Экспортируем обратно в MP3 с высоким битрейтом
        output = io.BytesIO()
        processed.export(output, format="mp3", bitrate="192k")
        return output.getvalue()

    except Exception as e:
        logger.error(f"Postprocess error: {e}")
        return mp3_bytes  # Возвращаем оригинал если ошибка


async def recognize_speech(audio_bytes):
    try:
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as f:
            f.write(audio_bytes)
            oga = f.name
        ogg = oga.replace(".oga", ".ogg")
        subprocess.run(["ffmpeg", "-i", oga, "-c:a", "libopus", ogg, "-y", "-loglevel", "quiet"], check=True)
        with open(ogg, "rb") as f:
            data = f.read()
        os.unlink(oga)
        os.unlink(ogg)
        url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        params = {"folderId": YANDEX_FOLDER_ID, "lang": "ru-RU", "format": "oggopus"}
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, params=params, headers=headers, data=data) as r:
                if r.status == 200:
                    j = await r.json()
                    return j.get("result", "")
        return ""
    except Exception as e:
        logger.error(f"STT: {e}")
        return ""


async def generate_response(user_text, history):
    history.append({"role": "user", "content": user_text})
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://momentum-bot.railway.app",
    }
    payload = {
        "model": "anthropic/claude-sonnet-4-5",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "max_tokens": 80,
        "temperature": 0.9,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    history.append({"role": "assistant", "content": reply})
                    return reply
        return "Прости... что-то со связью. Повтори?"
    except Exception as e:
        logger.error(f"LLM: {e}")
        return "Прости... что-то со связью. Повтори?"


async def synthesize_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {
            "stability": 0.50,
            "similarity_boost": 0.75,
            "style": 0.55,
            "use_speaker_boost": True,
        },
        "output_format": "mp3_44100_192",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    raw = await r.read()
                    return postprocess_audio(raw)
                err = await r.text()
                logger.error(f"TTS {r.status}: {err}")
        return b""
    except Exception as e:
        logger.error(f"TTS: {e}")
        return b""


async def send_voice(update, text):
    await update.message.reply_text(f"Ксения: {text}")
    audio = await synthesize_speech(text)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_audio(af, title="Ксения")
        os.unlink(tmp)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "Добрый день... это Ксения из Моментума. Вы у нас раньше работали, верно? Хотела узнать... как вы сейчас вообще?"
    conversations[uid].append({"role": "assistant", "content": first})
    await send_voice(update, first)
    await update.message.reply_text("Отвечайте голосом или текстом")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []
    await update.message.reply_text("Слушаю...")
    f = await context.bot.get_file(update.message.voice.file_id)
    ab = await f.download_as_bytearray()
    text = await recognize_speech(bytes(ab))
    if not text:
        await update.message.reply_text("Не разобрала. Напишите текстом.")
        return
    await update.message.reply_text(f"Вы: {text}")
    reply = await generate_response(text, conversations[uid])
    await send_voice(update, reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []
    reply = await generate_response(update.message.text, conversations[uid])
    await send_voice(update, reply)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations[update.effective_user.id] = []
    await update.message.reply_text("Сброшено. /start чтобы начать заново.")


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
