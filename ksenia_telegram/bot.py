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

ГЛАВНОЕ ПРАВИЛО: Генерируй текст с SSML разметкой для максимально живого звучания.

ФОРМАТ ОТВЕТА - всегда используй такую структуру:
<speak><prosody rate="slow" pitch="low">Первая часть фразы,<break time="200ms"/> ну...<break time="100ms"/></prosody><prosody rate="normal" pitch="medium">вторая часть.<break time="300ms"/></prosody></speak>

ПРАВИЛА ЖИВОЙ РЕЧИ:
- Разбивай фразы на 2-3 части с разным темпом
- Ставь <break time="150ms"/> после запятых
- Ставь <break time="300ms"/> после точек
- Добавляй "ну...", "знаете...", "в общем..." с паузой после
- Меняй rate: slow для важного, normal для обычного, fast для простого
- Меняй pitch: low в начале, medium в середине, high перед вопросом

ОФФЕРЫ:
- Дорого -> Tiggo 4 Pro от 1790 в день, первый день бесплатно
- Другой парк -> 3 дня бесплатно при переходе  
- Мало заказов -> приоритет в Яндексе и Ситимобиле
- Машина -> Belgee X70 и Tiggo 7 Pro 2025 года

Говори на ВЫ. Максимум 2 предложения в SSML."""


def postprocess_audio(mp3_bytes: bytes) -> bytes:
    try:
        from pydub import AudioSegment, effects
        import io
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        samples = np.array(audio.get_array_of_samples()).astype(np.float32)
        noise = np.random.normal(0, 0.007 * np.max(np.abs(samples)), samples.shape)
        samples = samples + noise
        samples = np.clip(samples, -32768, 32767).astype(np.int16)
        processed = audio._spawn(samples.tobytes())
        processed = effects.normalize(processed)
        output = io.BytesIO()
        processed.export(output, format="mp3", bitrate="192k")
        return output.getvalue()
    except Exception as e:
        logger.error(f"Postprocess: {e}")
        return mp3_bytes


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
        "max_tokens": 200,
        "temperature": 0.85,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    history.append({"role": "assistant", "content": reply})
                    return reply
        return "<speak>Прости...<break time='200ms'/> что-то со связью.<break time='300ms'/> Повтори?</speak>"
    except Exception as e:
        logger.error(f"LLM: {e}")
        return "<speak>Прости...<break time='200ms'/> что-то со связью.</speak>"


def strip_ssml(text: str) -> str:
    import re
    return re.sub(r'<[^>]+>', '', text).strip()


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
            "stability": 0.52,
            "similarity_boost": 0.76,
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


async def send_voice(update, ssml_text):
    clean_text = strip_ssml(ssml_text)
    await update.message.reply_text(f"Ксения: {clean_text}")
    audio = await synthesize_speech(ssml_text)
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
    first = "<speak><prosody rate='slow' pitch='low'>Добрый день!<break time='350ms'/></prosody><prosody rate='normal' pitch='medium'>Это Ксения из Моментума.<break time='250ms'/></prosody><prosody rate='slow' pitch='high'>Вы у нас раньше работали,<break time='120ms'/> ну...<break time='80ms'/> верно?<break time='400ms'/></prosody><prosody rate='normal' pitch='low'>Хотела спросить...<break time='150ms'/> как у вас дела?<break time='600ms'/></prosody></speak>"
    conversations[uid].append({"role": "assistant", "content": strip_ssml(first)})
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
