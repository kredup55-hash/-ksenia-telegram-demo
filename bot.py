import logging, aiohttp, tempfile, os, re, io, asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

# СУПЕР-ЖЁСТКИЙ ПРОМПТ (БЕЗ ВЕЖЛИВОСТИ)
SYSTEM_PROMPT = """Ты Ксения из Моментума. ТЫ ЖЕНЩИНА.
ТЫ УЖЕ ПОЗДОРОВАЛАСЬ. 

ЖЁСТКИЕ ПРАВИЛА:
1. НИКОГДА не пиши "да, конечно", "слушаю вас", "я во внимании", "хорошо".
2. Если видишь вопрос "какие цены" или "условия" - СРАЗУ кидай блок с ценами.
3. В конце вопроса ВСЕГДА ставь три точки и вопрос: "...?"

ТЕКСТ ДЛЯ ОЗВУЧКИ (строго так):
отлично. смотрите. у нас по машинам сейчас так. chery tiggo семь про - две тысячи двести рублей в день. geely atlas - две тысячи двести. первый день бесплатный и без залогов. интересно было бы попробовать...?
"""

def clean_text(text):
    # Физическое удаление слов-паразитов
    patterns = [
        r"да, конечно[,.]?\s*",
        r"слушаю вас[,.]?\s*",
        r"я во внимании[,.]?\s*",
        r"хорошо[,.]?\s*",
        r"приветик[,.]?\s*"
    ]
    for pat in patterns:
        text = re.sub(pat, '', text, flags=re.IGNORECASE).strip()
    return text

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
    # Заменяем русские названия на латиницу для идеального произношения
    text = text.replace("чери", "chery")
    text = text.replace("джили", "geely")
    text = text.replace("тигго", "tiggo")
    text = text.replace("атлас", "atlas")
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.48,
            "similarity_boost": 0.80,
            "style": 0.25,
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
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    payload = {
        "model": "google/gemini-2.0-flash-001",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "temperature": 0.2
    }
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ):', '', reply, flags=re.IGNORECASE).strip()
                    reply = clean_text(reply) # ХИРУРГИЧЕСКОЕ УДАЛЕНИЕ
                    history.append({"role": "assistant", "content": reply})
                    return reply
    except Exception as e:
        logger.error(f"AI Error: {e}")
    return "отлично. смотрите. chery tiggo семь про - две тысячи двести рублей в день. geely atlas - две тысячи двести. первый день бесплатный. интересно...?"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "здрасьте, это ксения из моментума. вы раньше у нас работали, я звоню потому что сейчас условия реально классные стали. уделите пару минут...?"
    conversations[uid].append({"role": "assistant", "content": first})
    await update.message.reply_text(f"Ксения: {first}")
    audio = await synthesize_speech(first)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio); tmp = f.name
        with open(tmp, "rb") as af: await update.message.reply_audio(af)
        os.unlink(tmp)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations: conversations[uid] = []
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    reply = await generate_response(update.message.text, conversations[uid])
    await update.message.reply_text(f"Ксения: {reply}")
    audio = await synthesize_speech(reply)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio); tmp = f.name
        with open(tmp, "rb") as af: await update.message.reply_audio(af)
        os.unlink(tmp)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
