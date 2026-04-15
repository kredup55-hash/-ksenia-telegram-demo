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

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Говоришь по телефону, живо и по-человечески. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

НЕЛЬЗЯ:
- здороваться снова
- говорить "да конечно", "хорошо", "я во внимании", "приветик"
- писать заглавными буквами
- делать длинные перечисления

СТИЛЬ РЕЧИ:
- только строчные буквы
- короткие фразы как в живом разговоре
- между ценами делай паузу через точку
- не больше 3 предложений подряд
- вопрос в конце — всегда отдельной строкой

МАРКИ МАШИН: чэри тигго сем, джили атлас
ЦЕНЫ: две тыщи, две двести, тыща восемьсот

ГЛАВНЫЕ ПЛЮСЫ (выбери 2-3 самых важных для клиента):
своя мойка бесплатно. деньги выводишь когда хочешь. залогов нет и первый день бесплатно.

КОГДА КЛИЕНТ СОГЛАСЕН СЛУШАТЬ — скажи примерно так:
"отлично. по машинам сейчас так — чэри тигго сем, две тыщи первые две недели. джили атлас — две двести. первый день бесплатный, залогов нет."
потом пауза и отдельно:
"интересно попробовать?"
"""

def add_pauses_for_tts(text: str) -> str:
    # Добавляем паузы между ценами для естественного произношения
    text = re.sub(r'\.\s+', '. ', text)
    # Вопрос в конце — добавляем паузу перед ним
    text = re.sub(r'([^?!.])(\s*интересно)', r'\1. \2', text)
    return text.strip()

def process_audio_quality(mp3_bytes: bytes) -> bytes:
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        silence = AudioSegment.silent(duration=500)
        combined = audio + silence
        out = io.BytesIO()
        combined.export(out, format="mp3", bitrate="192k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Audio processing error: {e}")
        return mp3_bytes

async def synthesize_speech(text):
    text = add_pauses_for_tts(text)
    logger.info(f"TTS text: {text}")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.47,       # живой голос, не робот
            "similarity_boost": 0.80,
            "style": 0.20,           # немного характера
            "use_speaker_boost": True
        },
        "optimize_streaming_latency": 1
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    raw = await r.read()
                    return process_audio_quality(raw)
                else:
                    body = await r.text()
                    logger.error(f"ElevenLabs error {r.status}: {body}")
    except Exception as e:
        logger.error(f"ElevenLabs exception: {e}")
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
        "model": "anthropic/claude-sonnet-4.6",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "temperature": 0.4,   # чуть выше — живее речь
        "max_tokens": 200     # ограничиваем чтобы не говорила много
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ|assistant)\s*:', '', reply, flags=re.IGNORECASE).strip()
                    history.append({"role": "assistant", "content": reply})
                    logger.info(f"Claude reply: {reply}")
                    return reply
                else:
                    body = await r.text()
                    logger.error(f"OpenRouter error {r.status}: {body}")
                    return f"[ошибка: {r.status}]"
    except asyncio.TimeoutError:
        logger.error("OpenRouter timeout")
        return "[таймаут]"
    except Exception as e:
        logger.error(f"OpenRouter exception: {e}")
        return f"[ошибка соединения]"

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Проверяю...")
    results = []
    results.append(f"OPENROUTER_KEY: {'есть' if OPENROUTER_API_KEY else 'НЕТ'}")
    results.append(f"ELEVENLABS_KEY: {'есть' if ELEVENLABS_API_KEY else 'НЕТ'}")

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "HTTP-Referer": "https://railway.app", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json={"model": "anthropic/claude-sonnet-4.6", "messages": [{"role": "user", "content": "тест"}], "max_tokens": 10}, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
                results.append(f"Claude Sonnet 4.6: {'РАБОТАЕТ' if r.status == 200 else f'ОШИБКА {r.status}'}")
    except Exception as e:
        results.append(f"Claude: ОШИБКА {e}")

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": ELEVENLABS_API_KEY}, timeout=aiohttp.ClientTimeout(total=10)) as r:
                results.append(f"ElevenLabs: {'РАБОТАЕТ' if r.status == 200 else f'ОШИБКА {r.status}'}")
    except Exception as e:
        results.append(f"ElevenLabs: ОШИБКА {e}")

    await update.message.reply_text("\n".join(results))

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
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
