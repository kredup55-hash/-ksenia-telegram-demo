import logging
import aiohttp
import tempfile
import os
import subprocess
import re
import io
import numpy as np
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум.
ТЫ ЖЕНЩИНА. Всегда проверяй окончания: "записала", "посмотрела".

ПРАВИЛА ОЗВУЧКИ:
1. Никаких "да?", "а?", "м-м" — это звучит как робот.
2. Вместо "да?" используй "верно?" или "подскажите...".
3. ЦЕНЫ ТОЛЬКО СЛОВАМИ: "две-двести", "тысяча-семьсот-девяносто".
4. Многоточия (...) — это паузы для вдоха.
"""

def process_audio_quality(mp3_bytes: bytes) -> bytes:
    """Добавляет тишину в конец и легкий шум линии для естественности"""
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        
        # Добавляем 500мс тишины, чтобы фраза не обрывалась
        silence = AudioSegment.silent(duration=500)
        
        # Добавляем едва слышный шум (убирает эффект "пустой комнаты")
        sample_rate = audio.frame_rate
        num_samples = int(sample_rate * (len(audio)/1000.0))
        noise = (np.random.normal(0, 32768 * 0.005, num_samples)).astype(np.int16)
        noise_seg = AudioSegment(noise.tobytes(), frame_rate=sample_rate, sample_width=2, channels=1)
        
        combined = audio.overlay(noise_seg) + silence
        
        out = io.BytesIO()
        combined.export(out, format="mp3", bitrate="192k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Audio processing failed (check ffmpeg): {e}")
        return mp3_bytes

async def recognize_speech(audio_bytes):
    try:
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as f:
            f.write(audio_bytes)
            oga = f.name
        ogg = oga.replace(".oga", ".ogg")
        subprocess.run(["ffmpeg", "-i", oga, "-c:a", "libopus", ogg, "-y", "-loglevel", "quiet"], check=True)
        with open(ogg, "rb") as f: data = f.read()
        os.unlink(oga); os.unlink(ogg)
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
        logger.error(f"STT: {e}"); return ""

async def generate_response(user_text, history):
    history.append({"role": "user", "content": user_text})
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "anthropic/claude-sonnet-4-5",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "max_tokens": 300,
        "temperature": 0.8,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = re.sub(r'<[^>]+>', '', j["choices"][0]["message"]["content"]).strip()
                    history.append({"role": "assistant", "content": reply})
                    return reply
        return "Простите, связь прервалась."
    except Exception as e:
        logger.error(f"LLM: {e}"); return "Ошибка связи."

async def synthesize_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.8, "style": 0.6}
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    raw_audio = await r.read()
                    return process_audio_quality(raw_audio) # Улучшаем звук
        return b""
    except Exception as e:
        logger.error(f"TTS: {e}"); return b""

async def send_voice(update, text):
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
    first = "Здрасьте! Это Ксения из Моментума. ПодскажИте... вы же раньше у нас работали... верно? Я почему звоню... сейчас условия обновились, решили набрать. СкажИте... уделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await send_voice(update, first)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations: conversations[uid] = []
    f = await context.bot.get_file(update.message.voice.file_id)
    text = await recognize_speech(bytes(await f.download_as_bytearray()))
    if not text: return
    reply = await generate_response(text, conversations[uid])
    await send_voice(update, reply)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations: conversations[uid] = []
    reply = await generate_response(update.message.text, conversations[uid])
    await send_voice(update, reply)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
