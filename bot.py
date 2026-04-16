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

SYSTEM_PROMPT = """Ты Ксения из таксопарка Моментум. ТЫ ЖЕНЩИНА.
ТЫ УЖЕ ПОЗДОРОВАЛАСЬ. Говори мягко и уверенно, как живой человек.

НЕЛЬЗЯ:
- здороваться снова, говорить "приветик"
- придумывать цены и названия машин
- использовать эмодзи
- уходить от темы таксопарка
- говорить "угу", "ага" в начале ответа

ПРАВИЛА РЕЧИ:
1. Паузы — только запятые и тире. Никаких многоточий.
2. Слова вместо пауз: "ну", "смотрите", "кстати", "в целом", "слушайте"
3. Цены: "две тыщи", "две двести"
4. Машины: "чери тиго семь," (с запятой после), "джили атлас"
5. Не больше 3 предложений подряд

ХАК ДЛЯ ВОПРОСА — начинай с "как думаете," или "а скажите,":
"как думаете, может попробуете выйти на этой неделе?"

ЕСЛИ СПРАШИВАЮТ РОБОТ ЛИ ТЫ:
"живой человек, ксения меня зовут. ну что, условия интересно послушать?"

КОГДА КЛИЕНТ СОГЛАСЕН:
"ну смотрите, по машинам сейчас так. чери тиго семь, — это две тыщи в начале, а потом две двести. джили атлас — тоже две двести. кстати, первый день у нас вообще бесплатно, и никаких залогов нет. как думаете, может попробуете выйти на этой неделе?"

КОГДА О ПРЕИМУЩЕСТВАХ:
"ну, в целом — своя мойка, деньги выводишь когда хочешь, залогов нет, первый день бесплатно. как думаете, интересно?"

ВОПРОС В КОНЦЕ — всегда начинай с "как думаете," или "а скажите,"
"""

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
        (r'тЫщи', 'тыщи'), (r'двЕсти', 'двести'),
        (r'чЕри', 'чери'), (r'тИго', 'тиго'),
        (r'сЕм\b', 'семь'), (r'джИли', 'джили'), (r'Атлас', 'атлас'),
        (r'черри\s+тигго', 'чери тиго'),
        (r'чери\s+тигго', 'чери тиго'),
        (r'\.{2,}', ','),  # многоточия → запятая
        (r'две\s+тысячи\s+двести\s+рублей', 'две двести'),
        (r'две\s+тысячи\s+двести', 'две двести'),
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи(?!\s+двести)', 'две тыщи'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'), (r'\bтысяча\b', 'тыща'),
        (r',\s*,', ','),
        (r' {2,}', ' '),
    ]
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
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

async def tts(text: str) -> bytes:
    text = post_process_text(text)
    logger.info(f"TTS: {text}")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.50,
            "similarity_boost": 0.80,
            "style": 0.30,
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
                    logger.error(f"OpenRouter {r.status}")
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
    el_payload = {"text": "тест", "model_id": "eleven_turbo_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}}
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
    # Чистое естественное приветствие — без "алло да", без "я чего звоню"
    first = "добрый день, это ксения из моментума. вы раньше у нас работали, звоню потому что условия сейчас стали намного лучше. уделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await update.message.reply_text(f"Ксения: {first}")
    audio = await tts(first)
    await send_audio(update, audio)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    reply = await generate_response(update.message.text, conversations[uid])
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
