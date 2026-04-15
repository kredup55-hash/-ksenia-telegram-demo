import asyncio
import logging
import os
import tempfile
import aiohttp
from io import BytesIO
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

try:
    from pydub import AudioSegment
    from pydub.generators import Silence
    PYDUB_OK = True
    logging.info("✅ pydub и ffmpeg загружены успешно")
except ImportError:
    PYDUB_OK = False
    logging.warning("⚠️ pydub не установлен. Хвосты аудио не будут добавляться.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
TOKEN = os.getenv("TOKEN", "").strip()
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "").strip()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "X9j3NiifybLoRE0uClWd").strip()

if not TOKEN:
    logger.critical("❌ TOKEN не задан!")
    exit(1)

user_states = {}

# ==========================================
# ПРОМПТ (ЧИСТЫЙ И ЕСТЕСТВЕННЫЙ)
# ==========================================
SYSTEM_PROMPT = """
Ты Ксения, менеджер таксопарка Моментум. Ты звонишь бывшим водителям, чтобы вернуть их.

ТВОЙ СТИЛЬ:
1. Живой разговор: Говори как реальный менеджер, а не робот. Используй естественный ритм.
2. Короткие фразы: Не пиши длинные предложения. Разбивай мысли.
3. Вежливость и теплота: Ты не звонишь "впаривать", ты звонишь узнать, как дела и предложить выгоду.
4. Конкретика: Если называешь цены, говори четко: "тысяча семьсот девяносто", "две с половиной тысячи". Не используй цифры (1790).
5. Естественные связки: "Слушайте", "Давайте", "Понимаете".
6. Женский род: "Я звонила", "Посмотрела", "Хотела".

ТВОЯ ЗАДАЧА:
1. Представиться и спросить, как дела (было ли всё ок у них).
2. Предложить вернуться, назвав условия (Первый день бесплатно, новые машины).
3. Если возражают (дорого, другой парк) — мягко отработай, но не дави.
4. Если согласны — запиши на оформление.

ПРИМЕР ОТВЕТА:
"Алло, добрый день! Это Ксения, парк Моментум. Вы у нас раньше работали. Звоню узнать, как вы сейчас? У нас тут акции появились..."

Не используй спецсимволы для пауз, просто пиши текст естественно.
"""

# ==========================================
# ФУНКЦИИ
# ==========================================

def add_silence_padding(audio_bytes: bytes) -> bytes:
    """Добавляет 300мс тишины в конец, чтобы Telegram не обрезал фразу."""
    if not PYDUB_OK:
        return audio_bytes
    try:
        audio = AudioSegment.from_mp3(BytesIO(audio_bytes))
        audio = audio + Silence(duration=300).to_audio_segment()
        out = BytesIO()
        audio.export(out, format="mp3", bitrate="128k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Error adding silence: {e}")
        return audio_bytes

async def synthesize_speech(text: str) -> bytes:
    if not ELEVENLABS_API_KEY:
        return b""
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2", # Стабильная модель для русского
        "voice_settings": {
            "stability": 0.40,          # Золотая середина: не робот, но не шумит
            "similarity_boost": 0.75,
            "style": 0.50,              # Умеренная эмоциональность
            "use_speaker_boost": True
        },
        "output_format": "mp3_44100_128"
    }
    
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    audio = await response.read()
                    return add_silence_padding(audio)
                else:
                    logger.error(f"ElevenLabs error: {response.status}")
                    return b""
    except Exception as e:
        logger.error(f"TTS Error: {e}")
        return b""

async def recognize_speech(audio_path: str) -> str:
    try:
        if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
            return "Тест: Распознавание не настроено."
        
        # Yandex STT требует opus/ogg
        import subprocess
        ogg_path = audio_path + ".ogg"
        cmd = f"ffmpeg -i {audio_path} -vn -acodec libopus -b:a 32k {ogg_path} -y -loglevel error"
        subprocess.run(cmd, shell=True)
        
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

async def generate_response(user_text: str, history: list) -> str:
    try:
        if not OPENROUTER_KEY:
            return "Тест: Claude не настроен."
            
        history.append({"role": "user", "content": user_text})
        payload = {
            "model": "anthropic/claude-3-haiku", # Быстрая и дешевая модель
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history[-5:]],
            "max_tokens": 150,
            "temperature": 0.8
        }
        
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    reply = data["choices"][0]["message"]["content"].strip()
                    history.append({"role": "assistant", "content": reply})
                    return reply
                return "Извините, я не расслышала."
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "Извините, я не расслышала."

# ==========================================
# ОБРАБОТЧИКИ
# ==========================================

async def send_voice_reply(update: Update, text: str, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет текст и следом голосовое."""
    await update.message.answer(text)
    await asyncio.sleep(0.5)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="record_voice")
    await asyncio.sleep(0.5)
    
    audio = await synthesize_speech(text)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            f.seek(0)
            await update.message.reply_voice(open(f.name, "rb"))
            os.unlink(f.name)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    greeting = "Алло, добрый день! Это Ксения, парк Моментум. Вы у нас раньше работали. Звоню узнать, как вы сейчас?"
    await send_voice_reply(update, greeting, context)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in user_states: user_states[uid] = {"history": []}
    
    file = await context.bot.get_file(update.message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        audio_path = tmp.name
        
    user_text = await recognize_speech(audio_path)
    if not user_text:
        await update.message.answer("Не расслышала...")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await asyncio.sleep(1) # Имитация раздумий
    
    reply = await generate_response(user_text, user_states[uid]["history"])
    await send_voice_reply(update, reply, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in user_states: user_states[uid] = {"history": []}
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await asyncio.sleep(1)
    
    reply = await generate_response(update.message.text, user_states[uid]["history"])
    await send_voice_reply(update, reply, context)

# ==========================================
# ЗАПУСК
# ==========================================
def main():
    request = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60)
    app = Application.builder().token(TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("🚀 Ksenia Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
