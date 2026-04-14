import asyncio, re, aiohttp, os, json, time, random, sys, logging, io, tempfile, subprocess
from aiogram import Bot, Dispatcher, types
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
    logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная TOKEN не задана!")
    sys.exit(1)

OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "").strip()
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "").strip()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Rachel").strip()

bot = Bot(token=TOKEN)
dp = Dispatcher()
user_states = {}

# ==========================================
# БАЗА ЗНАНИЙ — ГОЛОСОВОЙ БОТ
# ==========================================
KNOWLEDGE_BASE = """Ты Ксения, менеджер таксопарка Моментум. Общаешься ГОЛОСОМ по телефону.

ГЛАВНЫЕ ПРАВИЛА:
1. Говори коротко: 1-2 предложения максимум
2. Используй "..." для пауз: "Понимаю... давайте так..."
3. Будь естественной: "ну...", "честно говоря", "понимаете"
4. Никогда не спрашивай "да?" в конце
5. Сначала выслушай, потом предлагай

ЦЕНЫ:
- Комфорт+: Belgee X70 — 2500/день (2 нед), затем 2800. Atlas Pro — 2200/день (2 нед), затем 2800. Tiggo 7 Pro — 2000/день (2 нед), затем 2400.
- Комфорт: Coolray — 2000/день (2 нед), затем 2300. Tiggo 4 Pro — 1790/день (2 нед), затем 2200.
- Без депозита: Tenet T7, Arrizo 8 — 2500/день (2 нед), бонус 13 500 руб.
Первый день бесплатно на всех!

ПРИМЕРЫ:
- "Понимаю... цена кажется высокой, но там же всё включено... Первый день бесплатно, можно попробовать. Как вам?.."
- "Да, бывает... А что именно смущает?.. Может, просто подъедете посмотреть?.."

ГОВОРИ НА ВЫ. Будь тёплой и естественной."""

# ==========================================
# АУДИО ФУНКЦИИ
# ==========================================
def humanize_audio(audio_bytes: bytes) -> bytes:
    """Просто возвращаем аудио — ElevenLabs даёт хорошее качество"""
    return audio_bytes

async def recognize_speech(audio_path: str) -> str:
    """Yandex STT"""
    try:
        ogg_path = audio_path.replace(".ogg", "_opus.ogg")
        subprocess.run(["ffmpeg", "-i", audio_path, "-c:a", "libopus", "-b:a", "32k", ogg_path, "-y", "-loglevel", "quiet"], check=True)
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
    """ElevenLabs TTS"""
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

async def human_delay(text_length: int):
    delay = min(1.5, max(0.5, text_length / 40)) + random.uniform(0.2, 0.5)
    await asyncio.sleep(delay)

# ==========================================
# AI ОТВЕТЫ
# ==========================================
async def generate_response(user_text: str, history: list) -> str:
    history.append({"role": "user", "content": user_text})
    payload = {
        "model": "anthropic/claude-3-5-sonnet-20240620",
        "messages": [{"role": "system", "content": KNOWLEDGE_BASE}, *history[-6:]],
        "max_tokens": 100,
        "temperature": 0.85
    }
    async with aiohttp.ClientSession() as s:
        async with s.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}, json=payload) as r:
            if r.status == 200:
                data = await r.json()
                reply = data["choices"][0]["message"]["content"].strip()
                history.append({"role": "assistant", "content": reply})
                return reply
    return "Простите... что-то со связью..."

# ==========================================
# ОБРАБОТЧИКИ — ТЕКСТ + ГОЛОС
# ==========================================
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = str(message.from_user.id)
    user_states[uid] = {"history": [], "message_count": 0}
    
    greeting = "Добрый день... Это Ксения из Моментума... Вы раньше у нас работали... подскажите, как сейчас дела?.."
    user_states[uid]["history"].append({"role": "assistant", "content": greeting})
    
    # 1. Отправляем текст
    await message.answer(greeting)
    
    # 2. Отправляем голосовое
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

@dp.message(types.ContentType.VOICE)
async def handle_voice(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in user_states:
        user_states[uid] = {"history": [], "message_count": 0}
    state = user_states[uid]
    
    file = await message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        audio_path = tmp.name
    
    user_text = await recognize_speech(audio_path)
    if not user_text:
        await message.answer("Не расслышала... Повторите, пожалуйста.")
        return
    
    logger.info(f"User said: {user_text}")
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await generate_response(user_text, state["history"])
    logger.info(f"Bot replied: {reply}")
    
    # Отправляем текст + голос
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
        logger.error(f"Voice send error: {e}")
    
    os.unlink(audio_path)

@dp.message()
async def handle_text(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in user_states:
        user_states[uid] = {"history": [], "message_count": 0}
    state = user_states[uid]
    
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await generate_response(message.text, state["history"])
    
    # 1. Отправляем текст
    await message.answer(reply)
    
    # 2. Отправляем голосовое с задержкой
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
        logger.error(f"Voice send error: {e}")

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    await asyncio.sleep(3)
    try: await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e: logger.error(f"Webhook cleanup: {e}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(5)
    logger.info("🎙️ Starting voice bot polling...")
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
