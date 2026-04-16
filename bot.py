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
- использовать эмодзи (😄 🙂 👍 и любые другие — ЗАПРЕЩЕНО)
- уходить от темы таксопарка

СЕКРЕТ ЖИВОЙ РЕЧИ:
1. Используй слова-связки: "ну смотрите..", "слушайте..", "вот..", "так вот.."
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

def remove_emoji(text: str) -> str:
    """Убирает все эмодзи из текста"""
    emoji_pattern = re.compile(
        "[\U00010000-\U0010ffff"
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE
    )
    return emoji_pattern.sub('', text).strip()

def post_process_text(text: str) -> str:
    """Фонетический фильтр перед ElevenLabs"""

    # Сначала убираем эмодзи
    text = remove_emoji(text)

    fixes = [
        # Убираем капслок в середине слов
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
        (r'джили атлас(?!\s*—|\s*\.\.)', 'джили.. атлас'),
        # Цены
        (r'две\s+тысячи\s+двести\s+рублей', 'две двести'),
        (r'две\s+тысячи\s+двести', 'две двести'),
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи', 'две тыщи'),
        (r'тысяча\s+восемьсот\s+рублей', 'тыща восемьсот'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'),
        (r'\bтысяча\b', 'тыща'),
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

async def synthesize_speech(text, model="eleven_turbo_v2_5"):
    text = post_process_text(text)
    logger.info(f"TTS text: {text}")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": 0.35,
            "similarity_boost": 0.75,
            "style": 0.45,
            "use_speaker_boost": True
        },
        "optimize_streaming_latency": 1
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    return await r.read()
                elif r.status in (400, 422) and model == "eleven_turbo_v2_5":
                    logger.warning("Turbo недоступна, переключаюсь на multilingual_v2")
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
        "temperature": 0.5,
        "max_tokens": 200
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ|assistant)\s*:', '', reply, flags=re.IGNORECASE).strip()
                    reply = remove_emoji(reply)
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

    # Проверяем turbo модель
    el_headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    el_payload = {"text": "тест", "model_id": "eleven_turbo_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}", json=el_payload, headers=el_headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    results.append("ElevenLabs Turbo v2.5: РАБОТАЕТ")
                else:
                    results.append(f"ElevenLabs Turbo v2.5: НЕДОСТУПНА ({r.status}) — используем multilingual_v2")
    except Exception as e:
        results.append(f"ElevenLabs Turbo: ОШИБКА {e}")

    # Тест фильтра
    test_in = "чери тигго семь стоит две тысячи двести рублей 😄"
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
