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

# Готовые фонетически правильные фразы
PITCH = {
    "cars": "чери.. тиго.. сем — это две тыщи в начале, а потом две двести. вот.. джили.. атлас — тоже две двести.",
    "perks": "своя мойка бесплатно.. деньги выводишь когда хочешь.. залогов нет, первый день бесплатно.",
    "question": "слушайте, хотите попробовать выкатиться на этой неделе?",
    "agree_intro": "отлично.. ну смотрите.. по машинам сейчас так.",
}

SYSTEM_PROMPT = f"""Ты Ксения из таксопарка Моментум. Живая девушка-менеджер, говоришь расслабленно и по-человечески. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

НЕЛЬЗЯ:
- здороваться снова
- говорить "да конечно", "хорошо", "приветик"
- придумывать свои варианты цен и названий машин
- писать заглавными буквами

СЕКРЕТ ЖИВОЙ РЕЧИ:
1. Используй слова-связки: "ну смотрите...", "слушайте...", "вот...", "так вот..."
2. Делай паузы через ".." — не говори на одном дыхании
3. Не больше 2-3 предложений подряд

КОГДА КЛИЕНТ СОГЛАСЕН — используй ТОЧНО эти фразы:
"{PITCH['agree_intro']}"
"{PITCH['cars']}"
"первый день бесплатный, залогов нет."
"{PITCH['question']}"

КОГДА СПРАШИВАЮТ О ПРЕИМУЩЕСТВАХ:
"{PITCH['perks']}"
потом: "{PITCH['question']}"

ВОПРОС В КОНЦЕ — всегда: "{PITCH['question']}"
"""

def post_process_text(text: str) -> str:
    """Фонетический фильтр перед ElevenLabs — убирает всё что ломает голос"""

    fixes = [
        # Убираем капслок в середине слов (тЫщи → тыщи, чЕри → чери)
        (r'тЫщи', 'тыщи'), (r'тЫщу', 'тыщу'), (r'тЫща', 'тыща'),
        (r'двЕсти', 'двести'),
        (r'чЕри', 'чери'), (r'тИго', 'тиго'),
        (r'сЕм\b', 'сем'), (r'сЕмь', 'сем'),
        (r'джИли', 'джили'), (r'Атлас', 'атлас'),
        # Бренды
        (r'черри\s+тигго', 'чери тиго'),
        (r'чери\s+тигго', 'чери тиго'),
        (r'(чери\s+тиго)\s+(семь|сём|7|про)', r'чери.. тиго.. сем'),
        (r'чери тиго сем(?!\.)', 'чери.. тиго.. сем'),
        (r'джили атлас(?!\s*—)', 'джили.. атлас'),
        # Цены
        (r'две\s+тысячи\s+двести\s+рублей', 'две двести'),
        (r'две\s+тысячи\s+двести', 'две двести'),
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи', 'две тыщи'),
        (r'тысяча\s+восемьсот\s+рублей', 'тыща восемьсот'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'),
        (r'\bтысяча\b', 'тыща'),
        # Слово "семь" после тиго
        (r'(тиго|тигго)\s+семь', r'\1 сем'),
        # Убираем тройные многоточия
        (r'\.{3,}', '..'),
        # Лишние частицы в конце вопроса
        (r',?\s*\bа\?\s*$', '?'),
        (r',?\s*\bда\?\s*$', '?'),
    ]
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

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

async def synthesize_speech(text, model="eleven_turbo_v2_5"):
    text = post_process_text(text)
    logger.info(f"TTS text: {text}")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": 0.35,        # Ниже = больше живых интонационных качелей
            "similarity_boost": 0.75,
            "style": 0.45,            # Выше = эмоции и вопросительная интонация
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
                elif r.status == 400 and model == "eleven_turbo_v2_5":
                    # Turbo недоступна — откатываемся на multilingual
                    logger.warning("Turbo model unavailable, falling back to multilingual_v2")
                    return await synthesize_speech(text, model="eleven_multilingual_v2")
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
        "temperature": 0.5,   # Чуть выше для живости
        "max_tokens": 200
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
        return "[ошибка соединения]"

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

    # Тест фильтра
    test_in = "чери тигго семь стоит две тысячи двести рублей"
    test_out = post_process_text(test_in)
    results.append(f"\nФильтр:\nДо: {test_in}\nПосле: {test_out}")

    await update.message.reply_text("\n".join(results))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "здрасьте.. это ксения из моментума. вы раньше у нас работали.. я звоню потому что сейчас условия реально классные стали. уделите пару минут?"
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
