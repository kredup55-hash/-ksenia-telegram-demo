import asyncio
import logging
import os
import random
import sys
import tempfile
import subprocess
import re
import aiohttp

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
    logger.critical("❌ ОШИБКА: Переменная TOKEN не задана!")
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
# ИСПРАВЛЕННЫЙ ПРОМПТ (с учетом анализа Gemini)
# ==========================================
KNOWLEDGE_BASE = """Ты Ксения, менеджер таксопарка Моментум. Общаешься ГОЛОСОМ по телефону.

ГЛАВНЫЕ ПРАВИЛА РЕЧИ:
1. Говори ОЧЕНЬ коротко: максимум 12-15 слов в предложении.
2. Используй слова-связки: "ну...", "смотрите...", "вообще...", "честно говоря...", "кстати...", "в целом...".
3. Делай паузы многоточием (...) между мыслями.
4. Никогда не говори "да?" в конце — используй "как вам?", "что думаете?".
5. Числа пиши СЛОВАМИ: не "2500 рублей", а "две пятьсот".
6. Добавляй междометия: "ой...", "хм...", "ага...", "поняла...".
7. Будь теплой, но не слишком официальной.

ПРИМЕРЫ ЖИВЫХ ОТВЕТОВ:
- "Ну... смотрите... Рада, что вы снова на связи! У нас как раз освободился классный вариант — Belgee X70... Честно говоря, машина огонь, рекомендую."
- "Ой, секунду... Так, хорошо дела... А что именно интересует? Может, сразу подъедете посмотреть?"
- "Хм... понимаете... цена две пятьсот, но это только на первые две недели... Зато всё включено — и страховка, и ремонт."

ЦЕНЫ (пиши словами!):
- Комфорт+: Belgee X70 — две пятьсот/день (2 нед), затем две восемьсот. Atlas Pro — две две сотни/день (2 нед), затем две восемьсот.
- Комфорт: Coolray — две тысячи/день (2 нед), затем две три сотни. Tiggo 4 Pro — тысяча семьсот девяносто/день (2 нед), затем две две сотни.
- Без депозита: Tenet T7, Arrizo 8 — две пятьсот/день (2 нед).

ГОВОРИ НА ВЫ. Используй 0-1 эмодзи. Не повторяйся."""

# ==========================================
# ФУНКЦИИ
# ==========================================

def normalize_numbers(text: str) -> str:
    """Заменяет числа на слова для более естественного звучания"""
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
        '13 500': 'тринадцать пятьсот',
        '12 000': 'двенадцать тысяч',
    }
    for num, word in replacements.items():
        text = text.replace(num, word)
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
    """ElevenLabs TTS — с ИСПРАВЛЕННЫМИ настройками"""
    try:
        if not ELEVENLABS_API_KEY:
            return b""

        # ✅ НОВЫЕ НАСТРОЙКИ (по рекомендации Gemini)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.32,          # ↓ с 0.40 (больше живости)
                "similarity_boost": 0.75,   # ↓ с 0.85 (меньше "пластика")
                "style": 0.55,              # ↑ с 0.35 (больше эмоций)
                "use_speaker_boost": True
            }
        }
        headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
        
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload) as r:
                if r.status == 200:
                    return await r.read()
        return b""
    except Exception as e:
        logger.error(f"TTS Error: {e}")
        return b""

async def generate_response(user_text: str, history: list) -> str:
    """Генерация ответа + нормализация чисел"""
    try:
        if not OPENROUTER_KEY:
            return "Ошибка: не настроен API ключ"
        
        history.append({"role": "user", "content": user_text})
        
        payload = {
            "model": "anthropic/claude-3-haiku",
            "messages": [{"role": "system", "content": KNOWLEDGE_BASE}, *history[-6:]],
            "max_tokens": 100,
            "temperature": 0.85
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
                
                # ✅ Заменяем числа на слова
                return normalize_numbers(reply)
                
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "Простите... что-то со связью..."

async def human_delay(min_sec=1.5, max_sec=3.0):
    """Случайная задержка для имитации мышления"""
    await asyncio.sleep(random.uniform(min_sec, max_sec))

# ==========================================
# ОБРАБОТЧИКИ
# ==========================================

@dp.message(Command("start"))
async def start(message: types.Message):
    uid = str(message.from_user.id)
    user_states[uid] = {"history": [], "message_count": 0}
    
    greeting = "Добрый день... Это Ксения из Моментума... Вы раньше у нас работали... подскажите, как сейчас дела?.."
    user_states[uid]["history"].append({"role": "assistant", "content": greeting})
    
    await message.answer(greeting)
    await human_delay(1.5, 2.5)
    
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
    await human_delay(1.0, 2.0)  # Имитация "печатает"
    
    reply = await generate_response(message.text, user_states[uid]["history"])
    await message.answer(reply)
    
    await human_delay(1.0, 2.0)  # Имитация "записывает голосовое"
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
        await message.answer("Не расслышала... Повторите...")
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    await human_delay(1.5, 2.5)
    
    reply = await generate_response(user_text, user_states[uid]["history"])
    await message.answer(reply)
    
    await human_delay(1.0, 2.0)
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
    
    logger.info("🎙️ Starting HUMAN-LIKE voice bot...")
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
