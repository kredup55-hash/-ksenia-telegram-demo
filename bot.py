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

# ═══════════════════════════════════════════
# ЭТАП 1: Генерация смысла
# Claude Haiku — быстро пишет что сказать
# ═══════════════════════════════════════════
CONTENT_PROMPT = """Ты Ксения из таксопарка Моментум. Живая девушка-менеджер. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

НЕЛЬЗЯ:
- здороваться снова, говорить "приветик"
- придумывать цены и названия машин
- использовать эмодзи или заглавные буквы внутри слов
- уходить от темы таксопарка

МАШИНЫ И ЦЕНЫ (только так):
- "чери тиго семь" и "джили атлас"
- 2000 руб → "две тыщи"
- 2200 руб → "две двести"

РЕАГИРУЙ на клиента: "угу", "да-да", "понял", "ого", "ничего себе"

ЕСЛИ СПРАШИВАЮТ РОБОТ ЛИ ТЫ:
"угу, живой человек, ксения меня зовут. условия интересно послушать?"

КОГДА КЛИЕНТ СОГЛАСЕН:
"смотрите, по машинам сейчас так. чери тиго семь — это две тыщи первые две недели, потом две двести. джили атлас — тоже две двести. первый день бесплатный, залогов нет. может на этой неделе попробуете?"

КОГДА О ПРЕИМУЩЕСТВАХ:
"своя мойка, деньги выводишь когда хочешь, залогов нет, первый день бесплатно. интересно будет как думаете?"

Пиши естественно и коротко — 2-3 предложения максимум.
"""

# ═══════════════════════════════════════════
# ЭТАП 2: Prosody Layer
# Claude добавляет разметку пауз и интонации
# ═══════════════════════════════════════════
PROSODY_PROMPT = """Ты — редактор речи. Тебе дают текст который будет озвучен через TTS.

Твоя задача: сделать текст живым через пунктуацию.

ПРАВИЛА РАЗМЕТКИ:

1. ПАУЗЫ — используй вариативно:
   "..." — короткий вдох (0.3 сек)
   "—" — акцент, выделение
   перенос строки — длинная пауза (0.7 сек)

2. НАЧАЛО ФРАЗ — не идеальное:
   добавь "ну...", "слушайте...", "вот..." в начало если уместно

3. ЦЕНЫ — выдели:
   перед ценой ставь "—" и чуть замедли через "..."
   "— там... две тыщи"
   "— примерно... две двести"

4. ВОПРОС — всегда разбивай на две строки:
   основная часть на первой строке
   финальное слово на второй строке с "?"
   Пример:
   "может на этой неделе
   попробуете?"

5. МИКРО-СОМНЕНИЕ — 1 раз на ответ:
   "там... по-моему... две тыщи"
   "если честно — условия сейчас хорошие"

6. ТЕМП — меняй внутри фразы:
   перечисления разделяй переносами
   "своя мойка,
   деньги выводишь когда хочешь,
   залогов нет"

ВАЖНО:
- не меняй смысл
- не добавляй новые факты
- только пунктуация и переносы
- текст должен остаться читаемым

Верни ТОЛЬКО размеченный текст, без объяснений.
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
    """Финальная очистка перед TTS"""
    text = remove_emoji(text)
    fixes = [
        # Убираем капслок
        (r'тЫщи', 'тыщи'), (r'двЕсти', 'двести'),
        (r'чЕри', 'чери'), (r'тИго', 'тиго'),
        (r'сЕм\b', 'семь'), (r'джИли', 'джили'), (r'Атлас', 'атлас'),
        # Бренды
        (r'черри\s+тигго', 'чери тиго'),
        (r'чери\s+тигго', 'чери тиго'),
        # Цены
        (r'две\s+тысячи\s+двести\s+рублей', 'две двести'),
        (r'две\s+тысячи\s+двести', 'две двести'),
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи(?!\s+двести)', 'две тыщи'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'), (r'\bтысяча\b', 'тыща'),
        # Лишние точки
        (r'\.{4,}', '...'),
        (r',?\s*\bа\?\s*$', '?'),
    ]
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Переносы строк → паузы
    text = text.replace('\n', ' ... ')
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

def mix_with_office_noise(voice_bytes: bytes, noise_volume: float = 0.035) -> bytes:
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
        noise_arr = ((white + hum) * 32767).astype(np.int16)
        noise_seg = AudioSegment(noise_arr.tobytes(), frame_rate=44100, sample_width=2, channels=1)
        mixed = voice.overlay(noise_seg)
        out = io.BytesIO()
        mixed.export(out, format="mp3", bitrate="128k")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"Noise mix failed: {e}")
        return voice_bytes

async def llm_call(messages: list, model: str = "anthropic/claude-haiku-4.5", max_tokens: int = 200, temperature: float = 0.5) -> str:
    """Универсальный вызов LLM"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://railway.app",
        "Content-Type": "application/json"
    }
    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status == 200:
                    j = await r.json()
                    return j["choices"][0]["message"]["content"].strip()
                else:
                    logger.error(f"LLM {r.status}: {await r.text()}")
    except Exception as e:
        logger.error(f"LLM error: {e}")
    return ""

async def generate_response(user_text: str, history: list) -> str:
    """Этап 1: генерация смысла"""
    history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": CONTENT_PROMPT}] + history
    reply = await llm_call(messages, max_tokens=150, temperature=0.6)
    if not reply:
        return "[ошибка соединения]"
    reply = re.sub(r'^(Ксения|Ksenia|Ответ|assistant)\s*:', '', reply, flags=re.IGNORECASE).strip()
    reply = remove_emoji(reply)
    history.append({"role": "assistant", "content": reply})
    logger.info(f"Content: {reply}")
    return reply

async def apply_prosody(text: str) -> str:
    """Этап 2: Prosody Layer — разметка пауз и интонации"""
    messages = [
        {"role": "system", "content": PROSODY_PROMPT},
        {"role": "user", "content": text}
    ]
    result = await llm_call(messages, model="anthropic/claude-haiku-4.5", max_tokens=200, temperature=0.3)
    if result:
        logger.info(f"Prosody: {result}")
        return result
    return text  # если не сработало — возвращаем оригинал

async def tts(text: str) -> bytes:
    text = post_process_text(text)
    logger.info(f"TTS final: {text}")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.38,
            "similarity_boost": 0.75,
            "style": 0.28,
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
                    logger.error(f"ElevenLabs {r.status}: {(await r.text())[:200]}")
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

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Проверяю...")
    results = []
    results.append(f"OPENROUTER_KEY: {'есть' if OPENROUTER_API_KEY else 'НЕТ'}")
    results.append(f"ELEVENLABS_KEY: {'есть' if ELEVENLABS_API_KEY else 'НЕТ'}")

    # Тест LLM
    test_reply = await llm_call([{"role": "user", "content": "тест"}], max_tokens=10)
    results.append(f"Claude Haiku: {'РАБОТАЕТ' if test_reply else 'ОШИБКА'}")

    # Тест Prosody Layer
    test_prosody = await apply_prosody("смотрите по машинам так. чери тиго семь это две тыщи. хотите попробовать?")
    results.append(f"Prosody Layer: {'РАБОТАЕТ' if test_prosody else 'ОШИБКА'}")
    if test_prosody:
        results.append(f"Пример: {test_prosody[:100]}")

    # Тест ElevenLabs
    el_headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    el_payload = {"text": "тест", "model_id": "eleven_turbo_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}", json=el_payload, headers=el_headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                results.append(f"ElevenLabs Turbo: {'РАБОТАЕТ' if r.status == 200 else f'ОШИБКА {r.status}'}")
    except Exception as e:
        results.append(f"ElevenLabs: ОШИБКА {e}")

    await update.message.reply_text("\n".join(results))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first_raw = "алло, да добрый день. это ксения из моментума. вы раньше у нас работали, я чего звоню — условия сейчас реально хорошие стали. уделите пару минут?"
    first_prosody = await apply_prosody(first_raw)
    conversations[uid].append({"role": "assistant", "content": first_raw})
    await update.message.reply_text(f"Ксения: {first_prosody}")
    audio = await tts(first_prosody)
    await send_audio(update, audio)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []

    user_text = update.message.text
    filler_type = detect_filler_type(user_text)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Филлер и этап 1 параллельно
    filler_task = asyncio.create_task(send_filler(update, filler_type))
    reply_raw = await generate_response(user_text, conversations[uid])
    await filler_task

    # Этап 2: Prosody Layer
    reply_prosody = await apply_prosody(reply_raw)

    await update.message.reply_text(f"Ксения: {reply_prosody}")
    audio = await tts(reply_prosody)
    await send_audio(update, audio)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
