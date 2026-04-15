import logging, aiohttp, tempfile, os, re, io, asyncio
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

# Промпт без изменений, но мы всё равно подстрахуемся кодом
SYSTEM_PROMPT = """Ты Ксения из таксопарка Моментум. ТЫ ЖЕНЩИНА.
Ты УЖЕ ПОЗДОРОВАЛАСЬ. 
НЕ ИСПОЛЬЗУЙ слова привет, приветик, здравствуйте.
Начинай ответ сразу с дела.

ТВОЙ СЦЕНАРИЙ:
Если клиент согласен, сразу фразу: "отлично, смотрите, у нас по машинам сейчас так."
Условия: чери тигго семь две тыщи первые две недели. джили атлас две двести. залогов нет и первый день бесплатно. 
В конце: "интересно было бы попробовать?"
"""

# НОВАЯ ФУНКЦИЯ: ХИРУРГИЧЕСКОЕ УДАЛЕНИЕ "ПРИВЕТИКОВ"
def clean_text(text):
    # Удаляем любые вариации приветствий в начале строки
    patterns = [
        r"^приветик[.!]?\s*",
        r"^привет[.!]?\s*",
        r"^здравствуйте[.!]?\s*",
        r"^да, конечно[.!]?\s*",
        r"^я вся во внимании[.!]?\s*",
        r"^ксения:\s*" 
    ]
    for pat in patterns:
        text = re.sub(pat, '', text, flags=re.IGNORECASE).strip()
    
    # Если после обрезки начинается с маленькой буквы - делаем заглавную для красоты текста
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
        
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
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2", 
        "voice_settings": {
            "stability": 0.60,        # Стабильность 0.60 - голос не "завивается" на цифрах
            "similarity_boost": 0.75, 
            "style": 0.15,            # Стиль 0.15 - почти робот, но цифры читает идеально
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
        "temperature": 0.1  # Минимальный креатив
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    
                    # ПРИМЕНЯЕМ ЧИСТКУ ТЕКСТА (УБИРАЕМ ПРИВЕТИКИ)
                    reply = clean_text(reply)
                    
                    history.append({"role": "assistant", "content": reply})
                    return reply
    except Exception as e:
        logger.error(f"AI Error: {e}")
    return "простите, связь барахлит."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "здрасьте, это ксения из моментума. вы раньше у нас работали, я звоню потому что сейчас условия реально классные стали. уделите пару минут?"
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
    
    # Отправляем текст уже после очистки
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
