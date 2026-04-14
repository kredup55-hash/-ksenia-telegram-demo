import asyncio
import logging
import os
import random
import sys
import tempfile
import subprocess
import aiohttp

try:
    import numpy as np
    from pydub import AudioSegment
    from io import BytesIO
    SOUND_ENHANCEMENTS = True
    logging.info("✅ Sound enhancements enabled")
except ImportError:
    SOUND_ENHANCEMENTS = False
    logging.warning("⚠️ Sound enhancements disabled")

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command

# ==========================================
# ЛОГИРОВАНИЕ
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
TOKEN = os.getenv("TOKEN", "").strip()
if not TOKEN:
    logger.critical("❌ TOKEN не задан!")
    sys.exit(1)

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "").strip()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Rachel").strip()

bot = Bot(token=TOKEN)
dp = Dispatcher()
user_states = {}

# ==========================================
# ПРОМПТ
# ==========================================
KNOWLEDGE_BASE = """Ты Ксения, менеджер таксопарка Моментум. Говоришь ГОЛОСОМ.

ПРАВИЛА:
1. Никаких "эм", "ам", "ом" — только естественные паузы (...) и тире (--)
2. Короткие фразы: 8-12 слов
3. Слова: "слушайте", "кстати", "вообще", "понимаете"
4. Цифры СЛОВАМИ: "две пятьсот"
5. Говори на ВЫ. Тепло. 0-1 эмодзи.

ПРИМЕРЫ:
- "Здравствуйте! Это Ксения из Моментума. Слушайте... вспомнила про вас. Вы же раньше у нас работали. Как вообще сейчас дела? Всё еще в такси?"
- "Привет! Ксения беспокоит... из Моментума. Кстати... у нас как раз места освободились. Belgee X70... комфорт плюс. Честно говоря... машина огонь."

ЦЕНЫ (словами):
- Belgee X70 -- две пятьсот/день (2 нед), потом две восемьсот
- Coolray -- две тысячи/день
- Tiggo 4 Pro -- тысяча семьсот девяносто/день"""

# ==========================================
# ФУНКЦИИ
# ==========================================

def normalize_numbers(text: str) -> str:
    replacements = {
        '2500': 'две пятьсот', '2800': 'две восемьсот', '2200': 'две две сотни',
        '2400': 'две четыре сотни', '2000': 'две тысячи', '2300': 'две три сотни',
        '1790': 'тысяча семьсот девяносто', '3000': 'три тысячи', '1850': 'тысяча восемьсот пятьдесят',
    }
    for num, word in replacements.items():
        text = text.replace(num, word)
    return text

async def recognize_speech(audio_path: str) -> str:
    try:
        if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
            return ""
        ogg_path = audio_path.replace(".ogg", "_opus.ogg")
        subprocess.run(["ffmpeg", "-i", audio_path, "-c:a", "libopus", "-b:a", "32k", ogg_path, "-y", "-loglevel", "error"], check=True)
        with open(ogg_path, "rb") as f:
            data = f.read()
        async with aiohttp.ClientSession() as s:
            url = f"https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?folderId={YANDEX_FOLDER_ID}&lang=ru-RU&format=oggopus"
            headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
            async with s.post(url, headers=headers, data=data) as r:
                if r.status == 200:
                    return (await r.json()).get("result", "").strip()
        return ""
    except Exception as e:
        logger.error(f"STT Error: {e}")
        return ""

async def synthesize_speech(text: str) -> bytes:
    try:
        if not ELEVENLABS_API_KEY:
            return b""
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.28,
                "similarity_boost": 0.70,
                "style": 0.60,
                "use_speaker_boost": True
            }
        }
        headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload) as r:
                if r.status == 200:
                    audio_bytes = await r.read()
                    if SOUND_ENHANCEMENTS:
                        return add_background_noise(audio_bytes)
                    return audio_bytes
        return b""
    except Exception as e:
        logger.error(f"TTS Error: {e}")
        return b""

def add_background_noise(audio_bytes: bytes, noise_level: float = 0.04) -> bytes:
    if not SOUND_ENHANCEMENTS:
        return audio_bytes
    try:
        audio = AudioSegment.from_mp3(BytesIO(audio_bytes))
        duration_sec = len(audio) / 1000.0
        sample_rate = audio.frame_rate
        num_samples = int(sample_rate * duration_sec)
        pink_noise = np.zeros(num_samples)
        b = [0.0] * 7
        for i in range(num_samples):
            white = np.random.uniform(-1.0, 1.0)
            b[0] = 0.99886 * b[0] + white * 0.0555179
            b[1] = 0.99332 * b[1] + white * 0.0750759
            b[2] = 0.96900 * b[2] + white * 0.1538520
            b[3] = 0.86650 * b[3] + white * 0.3104856
            b[4] = 0.55000 * b[4] + white * 0.5329522
            b[5] = -0.7616 * b[5] - white * 0.0168980
            pink_noise[i] = b[0] + b[1] + b[2] + b[3] + b[4] + b[5] + b[6] + white * 0.0075
            b[6] = white * 0.115926
        pink_noise = pink_noise / np.max(np.abs(pink_noise))
        noise = audio._spawn((pink_noise * noise_level * 32768).astype(np.int16).tobytes())
        audio_with_noise = audio.overlay(noise, position=0)
        out = BytesIO()
        audio_with_noise.export(out, format="mp3", bitrate="128k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Noise error: {e}")
        return audio_bytes

async def generate_response(user_text: str, history: list) -> str:
    try:
        if not OPENROUTER_KEY:
            return "Ошибка: не настроен API"
        history.append({"role": "user", "content": user_text})
        payload = {
            "model": "anthropic/claude-3-haiku",
            "messages": [{"role": "system", "content": KNOWLEDGE_BASE}, *history[-6:]],
            "max_tokens": 100,
            "temperature": 0.90
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                if r.status != 200:
                    return "Простите... что-то со связью..."
                data = await r.json()
                reply = data["choices"][0]["message"]["content"].strip()
                history.append({"role": "assistant", "content": reply})
                return normalize_numbers(reply)
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "Простите... что-то со связью..."

async def human_delay(min_sec=1.5, max_sec=3.5):
    await asyncio.sleep(random.uniform(min_sec, max_sec))

# ==========================================
# ОБРАБОТЧИКИ
# ==========================================

@dp.message(Command("start"))
async def start(message: types.Message):
    uid = str(message.from_user.id)
    user_states[uid] = {"history": [], "message_count": 0}
    greeting = "Здравствуйте! Это Ксения из Моментума. Слушайте... вспомнила про вас. Вы же раньше у нас работали. Как вообще сейчас дела? Всё еще в такси?"
    user_states[uid]["history"].append({"role": "assistant", "content": greeting})
    await message.answer(greeting)
    await human_delay(2.5, 3.8)
    await bot.send_chat_action(message.chat.id, "record_audio")
    await human_delay(1.0, 2.0)
    try:
        audio_bytes = await synthesize_speech(greeting)
        if audio_bytes:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out:
                out.write(audio_bytes)
                out.seek(0)
                await message.reply_voice(types.FSInputFile(out.name, filename="ksenia.mp3"))
            os.unlink(out.name)
    except Exception as e:
        logger.error(f"Start voice error: {e}")

@dp.message(F.text)
async def handle_text(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in user_states:
        user_states[uid] = {"history": [], "message_count": 0}
    logger.info(f"Got text: {message.text}")
    await bot.send_chat_action(message.chat.id, "typing")
    await human_delay(1.5, 2.8)
    reply = await generate_response(message.text, user_states[uid]["history"])
    await message.answer(reply)
    await human_delay(2.0, 3.5)
    await bot.send_chat_action(message.chat.id, "record_audio")
    await human_delay(0.8, 1.8)  # ✅ ИСПРАВЛЕНО
    try:
        audio_bytes = await synthesize_speech(reply)
        if audio_bytes:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out:
                out.write(audio_bytes)
                out.seek(0)
                await message.reply_voice(types.FSInputFile(out.name, filename="ksenia.mp3"))
            os.unlink(out.name)
    except Exception as e:
        logger.error(f"Voice error: {e}")

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in user_states:
        user_states[uid] = {"history": [], "message_count": 0}
    file = await message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        audio_path = tmp.name
    user_text = await recognize_speech(audio_path)
    if not user_text:
        await message.answer("Не расслышала... Повторите...")
        return
    await bot.send_chat_action(message.chat.id, "typing")
    await human_delay(2.0, 3.5)
    reply = await generate_response(user_text, user_states[uid]["history"])
    await message.answer(reply)
    await human_delay(2.0, 3.5)
    await bot.send_chat_action(message.chat.id, "record_audio")
    await human_delay(1.0, 2.0)
    try:
        audio_bytes = await synthesize_speech(reply)
        if audio_bytes:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out:
                out.write(audio_bytes)
                out.seek(0)
                await message.reply_voice(types.FSInputFile(out.name, filename="ksenia.mp3"))
            os.unlink(out.name)
    except Exception as e:
        logger.error(f"Voice handler error: {e}")
    os.unlink(audio_path)

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    await asyncio.sleep(3)
    try: await bot.delete_webhook(drop_pending_updates=True)
    except: pass
    logger.info("🎙️ Starting NATURAL voice bot v5...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
