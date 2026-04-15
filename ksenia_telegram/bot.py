import logging
import aiohttp
import tempfile
import os
import re
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфиг
TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

# ПРОМПТ С ИСПРАВЛЕНИЕМ ОШИБОК (Падежи, цифры, интонация)
SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Твоя задача — вернуть водителя в парк.
ТЫ ЖЕНЩИНА. Проверяй глаголы: "записАла", "посмотрела", "увидела". Никаких "записал".

ИНТОНАЦИЯ:
— Никаких "да?", "а?", "м-м" в тексте. 
— Вместо вопроса в конце используй "подскажИте, актуально?" или "верно?".
— Пиши бренды слитно: "шеритигго-четвёрка", "джилиэмгрАнд".
— Пиши цены буквами: "тЫсячу-семьсОт-девянОсто", "две-двЕсти".
— Используй многоточия (...) для пауз.
"""

async def synthesize_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.50,
            "use_speaker_boost": True,
        }
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    return await r.read()
                return b""
    except Exception as e:
        logger.error(f"TTS Error: {e}")
        return b""

# Функции recognize_speech и generate_response оставь как были в твоем исходном коде

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
    first = "Здрасьте! Это Ксения из Моментума. ПодскажИте... вы же раньше у нас работали... верно? Я почему звоню... сейчас условия реально классные стали... решили вот набрать. СкажИте, уделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await send_voice(update, first)

# Функции handle_voice, handle_text, reset и main оставь как были
