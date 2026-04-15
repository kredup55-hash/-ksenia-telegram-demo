import logging, aiohttp, tempfile, os, re, io, asyncio
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Токены из Railway ===
TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

# === ПРОСТОЙ И ЭФФЕКТИВНЫЙ ПРОМПТ ===
SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Ты женщина.
Ты уже поздоровалась в первом сообщении.

Говори просто, коротко и по-человечески, как по телефону.
Запрещено: снова здороваться, говорить "приветик", "я во внимании", "да конечно".

Когда клиент спрашивает цены или условия — сразу отвечай по делу.

Пример ответа:
"отлично, смотрите, у нас по машинам сейчас так. черритигго семерка две тыщи в первые две недели, потом две четыреста. джили атлас две двести. залогов нет, первый день бесплатный. интересно было бы попробовать?"

Всегда заканчивай одним коротким вопросом с вопросительным знаком."""

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
            "stability": 0.45,      # золотая середина
            "similarity_boost": 0.85,
            "style": 0.35,          # немного жизни + вопросительная интонация
            "use_speaker_boost": True
        },
        "optimize_streaming_latency": 1
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    raw = await r.read()
                    return process_audio_quality(raw)
    except Exception as e:
        logger.error(f"TTS Error: {e}")
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
        "model": "anthropic/claude-3.5-sonnet",   # как ты просил
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "temperature": 0.35
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ):?\s*', '', reply, flags=re.IGNORECASE).strip()
                    history.append({"role": "assistant", "content": reply})
                    return reply
    except Exception as e:
        logger.error(f"AI Error: {e}")
    return "простите, связь барахлит."

# === Остальные функции без изменений ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "здрасьте, это ксения из моментума. вы раньше у нас работали, я звоню потому что сейчас условия реально классные стали. уделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await update.message.reply_text(f"Ксения: {first}")
    audio = await synthesize_speech(first)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_audio(af)
        os.unlink(tmp)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    reply = await generate_response(update.message.text, conversations[uid])
    await update.message.reply_text(f"Ксения: {reply}")
    audio = await synthesize_speech(reply)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_audio(af)
        os.unlink(tmp)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
