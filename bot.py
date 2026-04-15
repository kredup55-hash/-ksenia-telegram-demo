import logging, aiohttp, tempfile, os, subprocess, re, io, asyncio
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ключи и настройки (берутся из переменных Railway)
TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

# МАКСИМАЛЬНО ЖИВАЯ КСЕНИЯ
SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. ТЫ ЖЕНЩИНА.
Твоя задача: отвечать кратко, как по телефону. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ в начале.

ПРАВИЛА ПОДАЧИ:
1. Никаких "Привет", "Я Ксения", "Рада слышать". Сразу к сути.
2. Говори только в ЖЕНСКОМ роде: "записала", "посмотрела", "узнала".
3. ФОНЕТИКА ЦЕН (ПИШИ ТОЛЬКО ТАК):
   - черритигго-семёрка: двЕ-тЫсячи (первые две недели), потом двЕ-четЫреста.
   - джили-атлас: двЕ-двЕсти.
   - бэлджи-икс-семьдесят: двЕ-пятьсОт.
   - черритигго-четвёрка: тЫсячу-семьсОт-девянОсто.
4. Вопрос всегда только ОДИН и в самом конце фразы.

Пример: "Смотрите, по ценам сейчас так. За черритигго-семёрку двЕ-тЫсячи в первые две недели, потом двЕ-четЫреста. Глянем вживую?"
"""

def process_audio_quality(mp3_bytes: bytes) -> bytes:
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        silence = AudioSegment.silent(duration=800)
        combined = audio + silence
        out = io.BytesIO()
        combined.export(out, format="mp3", bitrate="192k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Audio error: {e}")
        return mp3_bytes

async def synthesize_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.28,        # Живой, эмоциональный голос
            "similarity_boost": 0.92, 
            "style": 0.65,            # Характер и хитринка
            "use_speaker_boost": True
        },
        "optimize_streaming_latency": 1
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, headers=headers) as r:
            if r.status == 200:
                raw = await r.read()
                return process_audio_quality(raw)
    return b""

async def generate_response(user_text, history):
    history.append({"role": "user", "content": user_text})
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://railway.app",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "google/gemini-2.0-flash-001",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "temperature": 0.7
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    # Убираем возможные приписки от нейронки
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ):', '', reply, flags=re.IGNORECASE).strip()
                    history.append({"role": "assistant", "content": reply})
                    return reply
    except Exception as e:
        logger.error(f"AI Error: {e}")
    return "Простите, связь барахлит."

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
    first = "Здрасьте, это Ксения из Моментума. Вы раньше у нас работали, я звоню потому что сейчас условия реально классные стали. Уделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await update.message.reply_text(f"Ксения: {first}")
    await send_voice(update, first)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations: conversations[uid] = []
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    reply = await generate_response(update.message.text, conversations[uid])
    
    await update.message.reply_text(f"Ксения: {reply}")
    await send_voice(update, reply)

def main():
    request = HTTPXRequest(connection_pool_size=10, read_timeout=60)
    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
