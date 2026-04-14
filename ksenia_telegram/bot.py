import asyncio
import logging
import os
import random
import sys
import tempfile
import subprocess
import aiohttp
import numpy as np
from io import BytesIO

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

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
    logger.critical("❌ ОШИБКА: TOKEN не задан!")
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
# ИСПРАВЛЕННЫЙ ПРОМПТ (с "живыми" ошибками)
# ==========================================
KNOWLEDGE_BASE = """Ты Ксения, менеджер таксопарка Моментум. Общаешься ГОЛОСОМ по телефону.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
1. Говори с МИКРО-ОШИБКАМИ как живой человек:
   - Начинай со вздохов: "Ой...", "Ага...", "Ну...", "Слушайте..."
   - Делай НЕРАВНОМЕРНЫЕ паузы: ... между мыслями
   - Используй слова-паразиты: "ну", "как бы", "в целом", "вообще", "понимаете"
   - Иногда "зажевывай" окончания или говори быстрее

2. Эмоции через punctuation:
   - ЗАГЛАВНЫЕ для акцентов: "цена ВСЕГО две пятьсот"
   - Многоточия (...) для неуверенности: "ну... знаете..."
   - Тире (--) для длинных пауз
   - Восклицательные знаки для эмоций!

3. Числа ТОЛЬКО словами:
   - НЕ "2500 рублей", а "две пятьсот"
   - НЕ "2800", а "две восемьсот"

4. Живые фразы:
   - "Ой, здравствуйте! ... Это Ксения из Моментума."
   - "Слушайте, вспомнила про вас... вы же раньше работали..."
   - "Ну что ж... жаль, конечно... но если что -- мы здесь!"
   - "Честно говоря... машина ОГОНЬ, рекомендую!"

ПРИМЕРЫ ИДЕАЛЬНЫХ ОТВЕТОВ:
- "Ой, здравствуйте! ... Это Ксения из Моментума. (пауза) Слушайте, вспомнила про вас, вы же раньше у нас работали... Подскажите, как сейчас дела? Всё еще в такси?"
- "Ну... смотрите... Рада, что снова на связи! У нас как раз освободился классный вариант -- Belgee X70... Честно говоря, машина ОГОНЬ, рекомендую."
- "Хм... понимаете... цена ВСЕГО две пятьсот в сутки, но это только на первые две недели... Зато ВСЁ включено -- и страховка, и ремонт."

ЦЕНЫ (всегда словами!):
- Комфорт+: Belgee X70 -- две пятьсот/день (2 нед), затем две восемьсот. Atlas Pro -- две две сотни/день.
- Комфорт: Coolray -- две тысячи/день. Tiggo 4 Pro -- тысяча семьсот девяносто/день.
- Без депозита: Tenet T7, Arrizo 8 -- две пятьсот/день.

ГОВОРИ НА ВЫ. 0-1 эмодзи. Будь теплой, НЕ официальной. Не повторяйся."""

# ==========================================
# ФУНКЦИИ
# ==========================================

def normalize_numbers(text: str) -> str:
    """Заменяет числа на слова + добавляет акценты"""
    replacements = {
        '2500': 'две пятьсот',
        '2800': 'две восемьсот', 
        '2200': 'две две сотни',
        '2400': 'две четыре сотни',
        '2000': 'две тысячи',
        '2300': 'две три сотни',
        '1790': 'тысяча семьсот девяносто',
        '3000': 'три тысячи',
        '3300': 'три три сотни',
        '1850': 'тысяча восемьсот пятьдесят',
        '13500': 'тринадцать пятьсот',
        '12000': 'двенадцать тысяч',
    }
    for num, word in replacements.items():
        text = text.replace(num, word)
    return text

def add_human_imperfections(text: str) -> str:
    """Добавляет микро-недостатки для естественности"""
    # Случайные слова-связки в начале
    openers = ["Ну... ", "Слушайте... ", "Ой... ", "Ага... ", "Хм... "]
    if random.random() < 0.3 and not any(text.startswith(w) for w in ["Ну", "Слушайте", "Ой", "Ага", "Хм"]):
        text = random.choice(openers) + text
    
    # Случайные междометия в середине
    fillers = [" ... знаете ... ", " ... вообще ... ", " ... честно говоря ... "]
    sentences = text.split('. ')
    if len(sentences) > 1 and random.random() < 0.4:
        idx = random.randint(1, len(sentences)-1)
        sentences[idx] = random.choice(fillers) + sentences[idx]
        text = '. '.join(sentences)
    
    return text

async def recognize_speech(audio_path: str) -> str:
    """Yandex STT"""
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
    """ElevenLabs TTS — с ИСПРАВЛЕННЫМИ настройками (2026)"""
    try:
        if not ELEVENLABS_API_KEY:
            return b""

        # ✅ НОВЫЕ НАСТРОЙКИ (по последнему анализу)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",  # Или v3 если доступен
            "voice_settings": {
                "stability": 0.32,          # ↓ НИЖЕ для хаоса и эмоций
                "similarity_boost": 0.75,   # ↓ Меньше "стерильности"
                "style": 0.60,              # ↑ ВЫШЕ для естественных взлетов/падений
                "use_speaker_boost": True
            }
        }
        headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
        
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload) as r:
                if r.status == 200:
                    audio_bytes = await r.read()
                    # Добавляем микро-шум для естественности
                    return add_background_noise(audio_bytes)
        return b""
    except Exception as e:
        logger.error(f"TTS Error: {e}")
        return b""

def add_background_noise(audio_bytes: bytes, noise_level: float = 0.02) -> bytes:
    """Добавляет едва слышный фоновый шум (офис/дорога)"""
    if not PYDUB_AVAILABLE:
        return audio_bytes
    
    try:
        audio = AudioSegment.from_mp3(BytesIO(audio_bytes))
        # Генерируем легкий белый шум
        noise = AudioSegment.silent(duration=len(audio), frame_rate=audio.frame_rate)
        noise = noise._spawn(np.random.normal(0, noise_level * 32768, len(audio.samples)).astype(np.int16).tobytes())
        
        # Смешиваем с оригиналом (шум на 2% громкости)
        audio_with_noise = audio.overlay(noise, position=0)
        
        out = BytesIO()
        audio_with_noise.export(out, format="mp3", bitrate="128k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Noise add error: {e}")
        return audio_bytes

async def generate_response(user_text: str, history: list) -> str:
    """Генерация ответа + человеческие недостатки"""
    try:
        if not OPENROUTER_KEY:
            return "Ошибка: не настроен API ключ"
        
        history.append({"role": "user", "content": user_text})
        
        payload = {
            "model": "anthropic/claude-3-haiku",
            "messages": [{"role": "system", "content": KNOWLEDGE_BASE}, *history[-6:]],
            "max_tokens": 120,
            "temperature": 0.90  # ↑ Выше для большей креативности
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
                
                # Применяем нормализацию и человеческие недостатки
                reply = normalize_numbers(reply)
                reply = add_human_imperfections(reply)
                
                return reply
                
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "Простите... что-то со связью..."

async def human_delay(min_sec=1.8, max_sec=3.5):
    """Случайная задержка для имитации мышления/печатания"""
    await asyncio.sleep(random.uniform(min_sec, max_sec))

# ==========================================
# ОБРАБОТЧИКИ
# ==========================================

@dp.message(Command("start"))
async def start(message: types.Message):
    uid = str(message.from_user.id)
    user_states[uid] = {"history": [], "message_count": 0}
    
    # Живое приветствие с неравномерными паузами
    greeting = "Ой, здравствуйте! ... Это Ксения из Моментума. ... Слушайте, вспомнила про вас... вы же раньше у нас работали... Подскажите, как сейчас дела? Всё еще в такси?"
    
    user_states[uid]["history"].append({"role": "assistant", "content": greeting})
    await message.answer(greeting)
    
    await human_delay(2.0, 3.0)  # Имитация "вспоминает"
    await bot.send_chat_action(message.chat.id, "record_audio")
    
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
    await human_delay(1.5, 2.8)  # Неравномерная задержка
    
    reply = await generate_response(message.text, user_states[uid]["history"])
    await message.answer(reply)
    
    await human_delay(1.5, 3.0)  # Случайная задержка перед записью
    await bot.send_chat_action(message.chat.id, "record_audio")
    
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
        await message.answer("Не расслышала... Повторите, пожалуйста...")
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    await human_delay(2.0, 3.5)  # Долгая задержка на "осмысление"
    
    reply = await generate_response(user_text, user_states[uid]["history"])
    await message.answer(reply)
    
    await human_delay(1.8, 3.2)
    await bot.send_chat_action(message.chat.id, "record_audio")
    
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
    
    logger.info("🎙️ Starting ULTRA-HUMAN voice bot v2026...")
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
