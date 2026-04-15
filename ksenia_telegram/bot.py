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

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация (берем из окружения)
TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

# ОБНОВЛЕННЫЙ СИСТЕМНЫЙ ПРОМПТ
SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Твоя задача — вернуть водителя в парк.

ТВОЙ ОБРАЗ:
— Ты реальная девушка, профессионал, но общаешься легко. 
— Исключи робо-фразы: "да?", "а?", "м-м", "ну-у". 
— Вместо "да?" в конце предложения используй "верно?" или "подскажИте?".

ГЛАВНОЕ — ЖЕНСКИЙ РОД:
Проверяй глаголы: "записАла", "посмотрела", "увидела". Никаких "записал".

ФОНЕТИКА И АКЦЕНТЫ (ПИШИ ТАК В ОТВЕТАХ):
— Бренды слитно через дефис: "шеритИгго-четвёрка", "джилиэмгрАнд", "бэлджи-икс-семьдесят".
— Цены (ВАЖНО): "тЫсячу-семьсОт-девянОсто", "две-двЕсти", "две-восЕмьсот", "тринадцать-пятьсот".
— Аббревиатуры: "асАга", "тэ-о".

РИТМ:
Используй многоточия (...) для естественных пауз. Вместо "Чего звоню" пиши "Почему звоню" или "Я по какому вопросу".

ПРИМЕРЫ ЖИВЫХ ФРАЗ:
— "Здрасьте! Это Ксения из Моментума. ПодскажИте... вы же раньше у нас работали... верно?"
— "Смотрите, почему звоню... сейчас условия реально классные стали. ЗалОгов нет, первый день бесплатно... а ОСАГО и ТЭ-О за наш счёт."
— "По ценам сейчас так... есть варианты за тЫсячу-семьсОт-девянОсто... или за две-двЕсти в день."
— "Хорошо, я вас записАла. Вам удобнее сегодня подъехать... или завтра?"
"""

def add_silence_padding(mp3_bytes: bytes, silence_ms: int = 500) -> bytes:
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))

        # Наложение шума (Room Tone) — убирает "мертвую" тишину
        sample_rate = audio.frame_rate
        duration_sec = len(audio) / 1000.0
        num_samples = int(sample_rate * duration_sec)
        noise_amplitude = 32768 * 0.005 # Чуть тише, чтобы не мешало
        noise = (np.random.normal(0, noise_amplitude, num_samples)).astype(np.int16)
        noise_segment = AudioSegment(noise.tobytes(), frame_rate=sample_rate, sample_width=2, channels=1)
        
        if audio.channels == 2:
            noise_segment = noise_segment.set_channels(2)
        
        audio = audio.overlay(noise_segment)

        # Хвост тишины, чтобы фраза не обрывалась на полуслове
        silence = AudioSegment.silent(duration=silence_ms)
        padded = audio + silence
        
        output = io.BytesIO()
        padded.export(output, format="mp3", bitrate="192k")
        return output.getvalue()
    except Exception as e:
        logger.error(f"Padding error: {e}")
        return mp3_bytes

# ... (функции recognize_speech и generate_response остаются без изменений) ...

async def synthesize_speech(text):
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
            "stability": 0.35, # Немного подняли для четкости
            "similarity_boost": 0.80,
            "style": 0.60,
            "use_speaker_boost": True,
        },
        "output_format": "mp3_44100_192",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    raw = await r.read()
                    # Добавляем 500мс тишины в конец
                    return add_silence_padding(raw, silence_ms=500)
                err = await r.text()
                logger.error(f"TTS {r.status}: {err}")
        return b""
    except Exception as e:
        logger.error(f"TTS: {e}")
        return b""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    # Переработанная первая фраза
    first = "Здрасьте! Это Ксения из Моментума. ПодскажИте... вы же раньше у нас работали... верно? Я почему звоню... сейчас условия обновились, решили вот набрать. СкажИте, уделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await send_voice(update, first)

# ... (остальные функции handle_voice, handle_text, main остаются без изменений) ...
