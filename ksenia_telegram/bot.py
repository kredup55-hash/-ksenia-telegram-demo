import asyncio
import logging
import os
import random
import sys
import tempfile
import subprocess
import aiohttp
import traceback

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
    logger.critical("❌ ОШИБКА: Переменная TOKEN не задана в Railway Variables!")
    sys.exit(1)

# ✅ Исправлено: берем переменную с именем как в вашем скриншоте Railway
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "").strip()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Rachel").strip()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Хранилище состояний (история диалогов)
user_states = {}

# ==========================================
# БАЗА ЗНАНИЙ (ПРОМПТ)
# ==========================================
KNOWLEDGE_BASE = """Ты Ксения, менеджер таксопарка Моментум. Общаешься ГОЛОСОМ по телефону.

ГЛАВНЫЕ ПРАВИЛА:
1. Говори коротко: 1-2 предложения максимум.
2. Используй "..." для пауз. Будь естественной.
3. Никогда не спрашивай "да?" в конце.
4. Сначала выслушай, потом предлагай.

ЦЕНЫ:
- Комфорт+: Belgee X70 — 2500/день (2 нед), затем 2800. Atlas Pro — 2200/день (2 нед), затем 2800.
- Комфорт: Coolray — 2000/день (2 нед), затем 2300. Tiggo 4 Pro — 1790/день (2 нед), затем 2200.
- Без депозита: Tenet T7, Arrizo 8 — 2500/день (2 нед).
Первый день бесплатно на всех!

ПРИМЕРЫ ОТВЕТОВ:
- "Понимаю... цена кажется высокой, но там же всё включено... Первый день бесплатно, можно попробовать. Как вам?.."
- "Да, бывает... А что именно смущает?.. Может, просто подъедете посмотреть?.."

ГОВОРИ НА ВЫ. Будь тёплой."""

# ==========================================
# ФУНКЦИИ (AI, STT, TTS)
# ==========================================

async def recognize_speech(audio_path: str) -> str:
    """Распознавание речи (Yandex STT)"""
    try:
        if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
            return ""
            
        ogg_path = audio_path.replace(".ogg", "_opus.ogg")
        subprocess.run(
            ["ffmpeg", "-i", audio_path, "-c:a", "libopus", "-b:a", "32k", ogg_path, "-y", "-loglevel", "error"], 
            check=True
        )
        
        with open(ogg_path, "rb") as f:
            data = f.read()
            
        async with aiohttp.ClientSession() as s:
            url = f"https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?folderId={YANDEX_FOLDER_ID}&lang=ru-RU&format=oggopus"
            headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
            async with s.post(url, headers=headers, data=data) as r:
                if r.status == 200:
                    j = await r.json()
                    return j.get("result", "").strip()
        return ""
    except Exception as e:
        logger.error(f"STT Error: {e}")
        return ""

async def synthesize_speech(text: str) -> bytes:
    """Синтез речи (ElevenLabs)"""
    try:
        if not ELEVENLABS_API_KEY:
            return b""

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.40,
                "similarity_boost": 0.85,
                "style": 0.35,
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
    """Генерация ответа (OpenRouter) — с исправленной моделью"""
    try:
        if not OPENROUTER_KEY:
            logger.error("❌ OPENROUTER_KEY is EMPTY!")
            return "Ошибка: не настроен API ключ OpenRouter"
        
        logger.info(f"🔑 Key check: {OPENROUTER_KEY[:10]}...")
        
        history.append({"role": "user", "content": user_text})
        context_messages = history[-6:]
        
        # ✅ Исправлено: используем правильный ID модели для OpenRouter
        model_name = "anthropic/claude-3-haiku"
        
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": KNOWLEDGE_BASE},
                *context_messages
            ],
            "max_tokens": 100,
            "temperature": 0.85
        }
        
        logger.info(f"📤 Sending AI request: model={model_name}, text='{user_text[:30]}...'")
        
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://momentum-bot.railway.app"
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                logger.info(f"📥 API Response status: {r.status}")
                
                # Обработка ошибок
                if r.status == 401:
                    logger.error("❌ HTTP 401: Invalid API Key!")
                    return "Ошибка: неверный ключ API"
                elif r.status == 403:
                    logger.error("❌ HTTP 403: Access Denied")
                    return "Ошибка: доступ запрещен или нет кредитов"
                elif r.status == 429:
                    logger.error("❌ HTTP 429: Rate limit")
                    return "Ошибка: слишком много запросов"
                elif r.status >= 500:
                    logger.error(f"❌ HTTP {r.status}: Server error")
                    return "Ошибка: проблема на сервере"
                elif r.status != 200:
                    error_body = await r.text()
                    logger.error(f"❌ HTTP {r.status}: {error_body}")
                    return f"Ошибка API {r.status}"
                
                try:
                    data = await r.json()
                    if "choices" not in data or not data["choices"]:
                        return "Ошибка: пустой ответ от AI"
                    
                    reply = data["choices"][0]["message"]["content"].strip()
                    logger.info(f"✅ Reply generated: '{reply[:50]}...'")
                    
                    history.append({"role": "assistant", "content": reply})
                    return reply
                    
                except Exception as json_err:
                    logger.error(f"❌ JSON parse error: {json_err}")
                    return "Ошибка: не удалось прочитать ответ"
                
    except asyncio.TimeoutError:
        logger.error("❌ Request timed out")
        return "Простите... долгий ответ..."
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return "Простите... что-то со связью..."

async def human_delay(text_length: int):
    """Имитация задержки 'человека'"""
    delay = min(1.5, max(0.5, text_length / 40)) + random.uniform(0.2, 0.5)
    await asyncio.sleep(delay)

# ==========================================
# ОБРАБОТЧИКИ (HANDLERS)
# ==========================================

@dp.message(Command("start"))
async def start(message: types.Message):
    uid = str(message.from_user.id)
    user_states[uid] = {"history": [], "message_count": 0}
    
    greeting = "Добрый день... Это Ксения из Моментума... Вы раньше у нас работали... подскажите, как сейчас дела?.."
    user_states[uid]["history"].append({"role": "assistant", "content": greeting})
    
    await message.answer(greeting)
    
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
    state = user_states[uid]
    
    logger.info(f"Got text: {message.text}")
    await bot.send_chat_action(message.chat.id, "typing")
    
    reply = await generate_response(message.text, state["history"])
    
    await message.answer(reply)
    
    await human_delay(len(reply))
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
        logger.error(f"Text handler voice error: {e}")

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in user_states:
        user_states[uid] = {"history": [], "message_count": 0}
    state = user_states[uid]
    
    logger.info("Got voice message")
    file = await message.voice.get_file()
    
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        audio_path = tmp.name
    
    user_text = await recognize_speech(audio_path)
    if not user_text:
        await message.answer("Не расслышала... Повторите, пожалуйста.")
        return
    
    logger.info(f"Recognized: {user_text}")
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await generate_response(user_text, state["history"])
    
    await message.answer(reply)
    await human_delay(len(reply))
    
    try:
        audio_bytes = await synthesize_speech(reply)
        if audio_bytes:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out:
                out.write(audio_bytes)
                out.seek(0)
                await message.reply_voice(types.FSInputFile(out.name, filename="ksenia.mp3"))
            os.unlink(out.name)
    except Exception as e:
        logger.error(f"Voice handler voice error: {e}")
    
    os.unlink(audio_path)

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    await asyncio.sleep(3)
    try: await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e: logger.error(f"Webhook cleanup: {e}")
    
    logger.info("🎙️ Starting voice bot polling...")
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
