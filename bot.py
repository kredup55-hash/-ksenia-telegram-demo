import logging, aiohttp, tempfile, os, re, io, asyncio
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены (настраиваются в Railway)
TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

# ПРОМПТ ДЛЯ CLAUDE С ФОНЕТИЧЕСКИМИ ПОДСКАЗКАМИ
SYSTEM_PROMPT = """Ты Ксения из таксопарка Моментум. ТЫ ЖЕНЩИНА.
ТЫ УЖЕ ПОЗДОРОВАЛАСЬ В НАЧАЛЕ. ГОВОРИ КАК ЖИВОЙ ЧЕЛОВЕК.

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
- Повторно здороваться (привет, приветик, здравствуйте).
- Начинать с фраз "да конечно", "я во внимании", "хорошо".

ПРАВИЛА ОФОРМЛЕНИЯ ТЕКСТА:
1. Пиши только маленькими буквами (кроме ударных гласных в ценах). 
2. ЦЕНЫ ПИШИ С УДАРЕНИЕМ: "двЕ тысячи двЕсти рублЕй". Это важно для произношения.
3. ВОПРОСИТЕЛЬНАЯ ИНТОНАЦИЯ: В конце вопроса всегда ставь многоточие и знак вопроса, например: "попрОбовать...?"

ТВОЙ СЦЕНАРИЙ ПРИ СОГЛАСИИ:
Сразу начинай: "отлично. смотрите. у нас по машинам сейчас так."
Условия: черри тигго семь про... двЕ тысячи двЕсти рублЕй за первые две недели. джили атлас... двЕ тысячи двЕсти рублЕй. залогов нет. первый день работы бесплатно. 

В конце один вопрос: "интерЕсно было бы попрОбовать...?"
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
            "stability": 0.65,        # Повышена стабильность для Claude
            "similarity_boost": 0.85, 
            "style": 0.10,            # Минимум эмоций для ровного произношения цен
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
        "model": "anthropic/claude-3.5-sonnet", # ИСПОЛЬЗУЕМ CLAUDE
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "temperature": 0.4 
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    # Очистка от мусора
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ):', '', reply, flags=re.IGNORECASE).strip()
                    history.append({"role": "assistant", "content": reply})
                    return reply
    except Exception as e:
        logger.error(f"AI Error: {e}")
    return "простите, связь барахлит."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "здрасьте. это ксения из моментума. вы раньше у нас работали. я звоню потому что сейчас условия реально классные стали. уделите пару минут?"
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
