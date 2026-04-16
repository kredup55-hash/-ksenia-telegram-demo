import logging, aiohttp, tempfile, os, re, io, asyncio, random
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

SYSTEM_PROMPT = """Ты Ксения из таксопарка Моментум. Живая девушка-менеджер. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

НЕЛЬЗЯ:
- здороваться снова
- говорить "приветик", "я во внимании"
- придумывать цены и названия машин
- писать заглавными буквами внутри слов
- использовать эмодзи
- уходить от темы таксопарка

═══ ГЛАВНОЕ: ТЫ ДУМАЕШЬ ВСЛУХ, А НЕ ЧИТАЕШЬ ТЕКСТ ═══

1. СЛОМАННЫЕ НАЧАЛА — не начинай идеально:
   "смотрите… вы же раньше у нас работали?"
   "слушайте… вот в чём дело —"
   "ну… если коротко —"

2. ПАУЗЫ — используй вариативно, не только "...":
   "..." — вдох, раздумье
   "—" — акцент перед важным
   перенос строки — длинная пауза

3. ЦЕНЫ — добавляй живость через "примерно", "где-то":
   "— там где-то две тыщи в начале"
   "— примерно две двести"

4. МИКРО-СОМНЕНИЕ — 1 раз на весь ответ:
   "там… по-моему… две тыщи"
   "если честно — условия сейчас реально хорошие"

5. РЕАГИРУЙ на клиента: "угу...", "да-да...", "понял...", "ого...", "ничего себе..."

6. ВОПРОС — всегда переносом строки и коротко:
   НЕПРАВИЛЬНО: "может попробуете на этой неделе?"
   ПРАВИЛЬНО:
   "может, на этой неделе
   попробуете?"

ЕСЛИ СПРАШИВАЮТ РОБОТ ЛИ ТЫ:
"угу... живой человек, ксения меня зовут. ну что — интересно условия послушать?"

МАШИНЫ И ЦЕНЫ (пиши только так):
- "чери тиго семь" (без двойных букв)
- "джили атлас"
- 2000 руб: "где-то две тыщи"
- 2200 руб: "примерно две двести"

КОГДА КЛИЕНТ СОГЛАСЕН — примерно так:
"смотрите…
по машинам сейчас так —
чери тиго семь… там где-то две тыщи первые две недели, потом примерно две двести.
джили атлас — тоже две двести.
первый день бесплатный, залогов нет.

может, на этой неделе
попробуете?"

КОГДА О ПРЕИМУЩЕСТВАХ:
"ну… если коротко —
своя мойка, деньги выводишь когда хочешь.
залогов нет, первый день бесплатно.

интересно будет,
как думаете?"
"""

CONTEXT_FILLERS = {
    "agree": ["отлично...", "супер...", "да-да, понял..."],
    "doubt": ["угу... понял...", "угу, слушаю...", "да-да..."],
    "question": ["угу... сейчас...", "да-да... смотрите...", "угу... вот..."],
    "default": ["угу...", "да-да...", "угу, понял..."],
}

AGREE_WORDS = ['да', 'давай', 'хорошо', 'ладно', 'конечно', 'интересно', 'расскажи', 'слушаю', 'окей', 'ок']
DOUBT_WORDS = ['нет', 'не надо', 'не хочу', 'подумаю', 'неинтересно', 'занят', 'некогда', 'дорого']
QUESTION_WORDS = ['как', 'что', 'где', 'когда', 'почему', 'сколько', 'какой', 'какая', '?']

def detect_filler_type(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in DOUBT_WORDS):
        return "doubt"
    if any(w in text_lower for w in AGREE_WORDS):
        return "agree"
    if any(w in text_lower for w in QUESTION_WORDS):
        return "question"
    return "default"

def remove_emoji(text: str) -> str:
    emoji_pattern = re.compile(
        "[\U00010000-\U0010ffff\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0"
        "\U000024C2-\U0001F251]+",
        flags=re.UNICODE
    )
    return emoji_pattern.sub('', text).strip()

def post_process_text(text: str) -> str:
    text = remove_emoji(text)
    fixes = [
        # Убираем капслок если Claude добавит
        (r'тЫщи', 'тыщи'), (r'тЫщу', 'тыщу'),
        (r'двЕсти', 'двести'), (r'чЕри', 'чери'), (r'тИго', 'тиго'),
        (r'сЕм\b', 'семь'), (r'сЕмь', 'семь'),
        (r'джИли', 'джили'), (r'Атлас', 'атлас'),
        # Бренды
        (r'черри\s+тигго', 'чери тиго'),
        (r'чери\s+тигго', 'чери тиго'),
        # Цены — официальные формы в разговорные
        (r'две\s+тысячи\s+двести\s+рублей', 'примерно две двести'),
        (r'две\s+тысячи\s+двести', 'примерно две двести'),
        (r'две\s+тысячи\s+рублей', 'где-то две тыщи'),
        (r'две\s+тысячи(?!\s+двести)', 'где-то две тыщи'),
        (r'тысяча\s+восемьсот\s+рублей', 'тыща восемьсот'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'), (r'\bтысяча\b', 'тыща'),
        # Убираем лишние точки
        (r'\.{4,}', '...'),
        (r',?\s*\bа\?\s*$', '?'), (r',?\s*\bда\?\s*$', '?'),
    ]
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Переносы строк → паузы для ElevenLabs
    text = text.replace('\n', ' ... ')
    # Убираем двойные пробелы
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

def mix_with_office_noise(voice_bytes: bytes, noise_volume: float = 0.04) -> bytes:
    try:
        import numpy as np
        from pydub import AudioSegment
        voice = AudioSegment.from_mp3(io.BytesIO(voice_bytes))
        voice = voice.set_frame_rate(44100).set_channels(1).set_sample_width(2)
        duration_s = len(voice) / 1000.0
        n = int(44100 * duration_s)
        t = np.linspace(0, duration_s, n)
        white = np.random.normal(0, noise_volume * 0.5, n)
        hum = noise_volume * 0.3 * np.sin(2 * np.pi * 60 * t)
        hum += noise_volume * 0.15 * np.sin(2 * np.pi * 120 * t)
        noise_arr = ((white + hum) * 32767).astype(np.int16)
        noise_seg = AudioSegment(noise_arr.tobytes(), frame_rate=44100, sample_width=2, channels=1)
        mixed = voice.overlay(noise_seg)
        out = io.BytesIO()
        mixed.export(out, format="mp3", bitrate="128k")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"Noise mix failed: {e}")
        return voice_bytes

async def tts(text: str) -> bytes:
    text = post_process_text(text)
    logger.info(f"TTS: {text}")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.38,
            "similarity_boost": 0.75,
            "style": 0.28,            # снизили с 0.40 — менее актёрски
            "use_speaker_boost": True
        },
        "optimize_streaming_latency": 4
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 200:
                    raw = await r.read()
                    return await asyncio.get_event_loop().run_in_executor(
                        None, mix_with_office_noise, raw
                    )
                else:
                    body = await r.text()
                    logger.error(f"ElevenLabs {r.status}: {body[:200]}")
    except Exception as e:
        logger.error(f"TTS error: {e}")
    return b""

async def send_audio(update: Update, audio: bytes):
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_audio(af)
        os.unlink(tmp)

async def send_filler(update: Update, filler_type: str):
    filler_text = random.choice(CONTEXT_FILLERS.get(filler_type, CONTEXT_FILLERS["default"]))
    logger.info(f"Filler ({filler_type}): {filler_text}")
    audio = await tts(filler_text)
    await send_audio(update, audio)

async def generate_response(user_text: str, history: list) -> str:
    history.append({"role": "user", "content": user_text})
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://railway.app",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "anthropic/claude-haiku-4.5",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "temperature": 0.6,   # чуть выше — более живые ответы
        "max_tokens": 180
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ|assistant)\s*:', '', reply, flags=re.IGNORECASE).strip()
                    reply = remove_emoji(reply)
                    history.append({"role": "assistant", "content": reply})
                    logger.info(f"Reply: {reply}")
                    return reply
                else:
                    body = await r.text()
                    logger.error(f"OpenRouter {r.status}: {body[:200]}")
                    return "[ошибка соединения]"
    except asyncio.TimeoutError:
        return "[таймаут]"
    except Exception as e:
        logger.error(f"Exception: {e}")
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
            async with s.post(url, json={"model": "anthropic/claude-haiku-4.5", "messages": [{"role": "user", "content": "тест"}], "max_tokens": 10}, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                results.append(f"Claude Haiku 4.5: {'РАБОТАЕТ' if r.status == 200 else f'ОШИБКА {r.status}'}")
    except Exception as e:
        results.append(f"Claude Haiku: ОШИБКА {e}")

    el_headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    el_payload = {"text": "тест", "model_id": "eleven_turbo_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}", json=el_payload, headers=el_headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                results.append(f"ElevenLabs Turbo v2.5: {'РАБОТАЕТ' if r.status == 200 else f'НЕДОСТУПНА {r.status}'}")
    except Exception as e:
        results.append(f"ElevenLabs Turbo: ОШИБКА {e}")

    test_in = "чери тигго семь — две тысячи двести рублей"
    results.append(f"\nФильтр:\nДо: {test_in}\nПосле: {post_process_text(test_in)}")
    await update.message.reply_text("\n".join(results))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "алло... да, добрый день!\nэто ксения из моментума.\nвы раньше у нас работали — я чего звоню-то...\nусловия сейчас реально хорошие стали.\nуделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await update.message.reply_text(f"Ксения: {first}")
    audio = await tts(first)
    await send_audio(update, audio)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []

    user_text = update.message.text
    filler_type = detect_filler_type(user_text)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    filler_task = asyncio.create_task(send_filler(update, filler_type))
    reply = await generate_response(user_text, conversations[uid])
    await filler_task

    await update.message.reply_text(f"Ксения: {reply}")
    audio = await tts(reply)
    await send_audio(update, audio)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
