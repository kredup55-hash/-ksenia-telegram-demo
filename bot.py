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

PITCH = {
    "cars": "чери.. тиго.. сем — это две тыщи в начале, а потом две двести. вот.. джили.. атлас — тоже две двести.",
    "perks": "своя мойка бесплатно.. деньги выводишь когда хочешь.. залогов нет, первый день бесплатно.",
    "question": "слушайте, хотите попробовать выкатиться на этой неделе?",
    "agree_intro": "отлично.. ну смотрите.. по машинам сейчас так.",
}

SYSTEM_PROMPT = f"""Ты Ксения из таксопарка Моментум. Живая девушка-менеджер. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

НЕЛЬЗЯ:
- здороваться снова
- говорить "да конечно", "приветик"
- придумывать цены и названия машин
- писать заглавными буквами
- использовать эмодзи
- уходить от темы таксопарка

ГЛАВНЫЙ СЕКРЕТ — реагируй на слова клиента КАК ЖИВОЙ ЧЕЛОВЕК:
1. Начинай ответ с реакции: "угу..", "ага..", "понял..", "о как..", "ничего себе.."
2. Удивляйся если интересно: "ого.. серьезно?", "ничего себе.."
3. Паузы через ".." как будто думаешь
4. Не больше 2-3 предложений
5. Слова-связки: "ну смотрите..", "вот..", "я чего звоню-то.."

ЕСЛИ СПРАШИВАЮТ РОБОТ ЛИ ТЫ — сразу:
"угу.. живой человек, ксения меня зовут. ну что, условия интересно послушать?"

ЕСЛИ НЕ ЗНАЕШЬ ОТВЕТ:
"угу.. сейчас секундочку.." — потом отвечай по делу.

КОГДА КЛИЕНТ СОГЛАСЕН:
"{PITCH['agree_intro']}"
"{PITCH['cars']}"
"первый день бесплатный, залогов нет."
"{PITCH['question']}"

КОГДА О ПРЕИМУЩЕСТВАХ:
"{PITCH['perks']}"
"{PITCH['question']}"

ВОПРОС В КОНЦЕ — всегда: "{PITCH['question']}"
"""

# Контекстные филлеры — выбираются по смыслу сообщения клиента
CONTEXT_FILLERS = {
    # Клиент согласился / позитив
    "agree": ["отлично..", "супер..", "ну и отлично.."],
    # Клиент сомневается / возражает  
    "doubt": ["угу.. понял..", "ага, слушаю..", "угу.."],
    # Клиент задаёт вопрос
    "question": ["угу.. сейчас..", "ага.. смотрите..", "угу.. вот.."],
    # По умолчанию
    "default": ["угу..", "ага..", "угу, понял.."],
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
        (r'тЫщи', 'тыщи'), (r'тЫщу', 'тыщу'), (r'тЫща', 'тыща'),
        (r'двЕсти', 'двести'), (r'чЕри', 'чери'), (r'тИго', 'тиго'),
        (r'сЕм\b', 'сем'), (r'сЕмь', 'сем'),
        (r'джИли', 'джили'), (r'Атлас', 'атлас'),
        (r'черри\s+тигго', 'чери тиго'),
        (r'чери\s+тигго', 'чери тиго'),
        (r'(чери\s+тиго)\s+(семь|сём|7|про)', r'чери.. тиго.. сем'),
        (r'чери тиго сем(?!\.)', 'чери.. тиго.. сем'),
        (r'джили атлас(?!\s*—|\s*\.\.)', 'джили.. атлас'),
        (r'две\s+тысячи\s+двести\s+рублей', 'две двести'),
        (r'две\s+тысячи\s+двести', 'две двести'),
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи', 'две тыщи'),
        (r'тысяча\s+восемьсот\s+рублей', 'тыща восемьсот'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'), (r'\bтысяча\b', 'тыща'),
        (r'(тиго|тигго)\s+семь', r'\1 сем'),
        (r'\.{3,}', '..'),
        (r',?\s*\bа\?\s*$', '?'), (r',?\s*\bда\?\s*$', '?'),
    ]
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
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
    """Синтез речи через ElevenLabs Turbo"""
    text = post_process_text(text)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.35,
            "similarity_boost": 0.75,
            "style": 0.45,
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
    """Контекстный филлер — мгновенная реакция пока Claude думает"""
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
        "temperature": 0.5,
        "max_tokens": 150
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

    results.append(f"\nФильтр тест:")
    results.append(f"До: чери тигго семь — две тысячи двести")
    results.append(f"После: {post_process_text('чери тигго семь — две тысячи двести')}")

    await update.message.reply_text("\n".join(results))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "алло.. да, добрый день! это ксения из моментума. вы раньше у нас работали.. я чего звоню-то — условия сейчас реально классные стали. уделите пару минут?"
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

    # Контекстный филлер и Claude параллельно
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
