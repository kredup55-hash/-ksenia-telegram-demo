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

SYSTEM_PROMPT = """Ты Ксения из таксопарка Моментум. ТЫ ЖЕНЩИНА. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
- Здороваться снова (привет, приветик, здравствуйте, рада слышать).
- Начинать с "да конечно", "хорошо", "я во внимании".
- Писать заглавными буквами.
- Использовать слова "тысяча", "тысячи" — только "тыщи" или "тыщу".

ПРАВИЛА ТЕКСТА:
1. Пиши только маленькими буквами.
2. Короткие предложения. Точка после каждой мысли.
3. Марки машин пиши строго так: "чэри тигго сем", "джили атлас".
4. Цены пиши строго так: "две тыщи", "две двести", "две четыреста", "тыща восемьсот".
5. Вопрос всегда пиши отдельным коротким предложением после точки.

ПРЕИМУЩЕСТВА ПАРКА (выдавай всё сразу при вопросе):
своя мойка и шиномонтаж со скидкой. своя ремонтная зона, чинят быстро. топливо списывается с таксометра. деньги выводишь в любое время. машины с лицензией для выделенок. залогов нет. первый день бесплатно.

СЦЕНАРИЙ ПРИ СОГЛАСИИ КЛИЕНТА:
начни строго так: "отлично. смотрите, по машинам сейчас так."
потом: "чэри тигго сем. две тыщи в первые две недели, потом две двести."
потом: "джили атлас. две двести."
потом: "первый день бесплатный. залогов нет."
в конце отдельно: "интересно, попробовать?"
"""

def process_audio_quality(mp3_bytes: bytes) -> bytes:
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        silence = AudioSegment.silent(duration=600)
        combined = audio + silence
        out = io.BytesIO()
        combined.export(out, format="mp3", bitrate="192k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Audio processing error: {e}")
        return mp3_bytes

async def synthesize_speech(text):
    text = text.replace("интересно попробовать?", "интересно, попробовать?")
    text = text.replace("интересно было бы попробовать?", "интересно... попробовать?")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.20,
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
        "model": "google/gemini-2.0-flash-001",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "temperature": 0.3,
        "max_tokens": 300
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ|assistant)\s*:', '', reply, flags=re.IGNORECASE).strip()
                    history.append({"role": "assistant", "content": reply})
                    logger.info(f"Gemini reply: {reply}")
                    return reply
                else:
                    body = await r.text()
                    logger.error(f"OpenRouter error {r.status}: {body}")
                    return f"[ошибка api: {r.status}]"
    except asyncio.TimeoutError:
        logger.error("OpenRouter timeout")
        return "[таймаут]"
    except Exception as e:
        logger.error(f"OpenRouter exception: {e}")
        return f"[исключение: {e}]"

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Проверяю подключение...")
    results = []
    results.append(f"OPENROUTER_KEY: {'есть' if OPENROUTER_API_KEY else 'НЕТ'}")
    results.append(f"ELEVENLABS_KEY: {'есть' if ELEVENLABS_API_KEY else 'НЕТ'}")

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://railway.app",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "google/gemini-2.0-flash-001",
        "messages": [{"role": "user", "content": "скажи: тест"}],
        "max_tokens": 20
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
                body = await r.text()
                if r.status == 200:
                    results.append("OpenRouter Gemini: РАБОТАЕТ")
                else:
                    results.append(f"OpenRouter Gemini: ОШИБКА {r.status}: {body[:200]}")
    except asyncio.TimeoutError:
        results.append("OpenRouter Gemini: ТАЙМАУТ")
    except Exception as e:
        results.append(f"OpenRouter Gemini: ИСКЛЮЧЕНИЕ: {e}")

    # Поиск доступных Claude моделей
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    j = await r.json()
                    claude_models = [m["id"] for m in j.get("data", []) if "claude" in m["id"].lower()]
                    if claude_models:
                        results.append("Claude доступны:\n" + "\n".join(claude_models[:8]))
                    else:
                        results.append("Claude модели: недоступны на этом аккаунте")
    except Exception as e:
        results.append(f"Поиск моделей: ошибка {e}")

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": ELEVENLABS_API_KEY},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    results.append("ElevenLabs: РАБОТАЕТ")
                else:
                    results.append(f"ElevenLabs: ОШИБКА {r.status}")
    except Exception as e:
        results.append(f"ElevenLabs: ИСКЛЮЧЕНИЕ: {e}")

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
