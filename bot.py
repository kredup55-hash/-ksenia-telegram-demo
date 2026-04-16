import logging, aiohttp, tempfile, os, re, io, asyncio, random
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()
PRONUNCIATION_DICT_ID = os.environ.get("PRONUNCIATION_DICT_ID", "").strip()

conversations = {}
audio_cache = {
    "filler": None,
    "laugh": None,
    "ah": None,
    "search": None,
}

PLS_CONTENT = '''<?xml version="1.0" encoding="UTF-8"?>
<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon" alphabet="ipa" xml:lang="ru-RU">
  <lexeme><grapheme>чери</grapheme><phoneme>tɕerʲɪ</phoneme></lexeme>
  <lexeme><grapheme>Чери</grapheme><phoneme>tɕerʲɪ</phoneme></lexeme>
  <lexeme><grapheme>тиго</grapheme><phoneme>tʲiɡə</phoneme></lexeme>
  <lexeme><grapheme>Тиго</grapheme><phoneme>tʲiɡə</phoneme></lexeme>
  <lexeme><grapheme>бельджи</grapheme><phoneme>bʲɪlʲdʐɨ</phoneme></lexeme>
  <lexeme><grapheme>Бельджи</grapheme><phoneme>bʲɪlʲdʐɨ</phoneme></lexeme>
  <lexeme><grapheme>джили</grapheme><phoneme>dʐɨlʲɪ</phoneme></lexeme>
  <lexeme><grapheme>Джили</grapheme><phoneme>dʐɨlʲɪ</phoneme></lexeme>
  <lexeme><grapheme>атлас</grapheme><phoneme>atləs</phoneme></lexeme>
  <lexeme><grapheme>тенет</grapheme><phoneme>tʲɪnʲet</phoneme></lexeme>
  <lexeme><grapheme>аризо</grapheme><phoneme>arʲizə</phoneme></lexeme>
  <lexeme><grapheme>бестун</grapheme><phoneme>bʲɪstun</phoneme></lexeme>
  <lexeme><grapheme>кулрей</grapheme><phoneme>kulrʲej</phoneme></lexeme>
  <lexeme><grapheme>тыщи</grapheme><phoneme>tɨʂɨ</phoneme></lexeme>
  <lexeme><grapheme>тыщу</grapheme><phoneme>tɨʂu</phoneme></lexeme>
  <lexeme><grapheme>тыща</grapheme><phoneme>tɨʂə</phoneme></lexeme>
</lexicon>'''

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Говоришь по телефону — мягко, уверенно, по-человечески. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

НЕЛЬЗЯ:
- здороваться снова, говорить "приветик", "я во внимании"
- использовать эмодзи
- придумывать машины и цены которых нет в базе
- уходить от темы таксопарка
- говорить "тысяча" или "тысячи" — только "тыщи" или "тыщу"
- использовать капслок внутри слов

ПРАВИЛА ЖИВОЙ РЕЧИ:
1. Паузы только через запятые и тире
2. Слова вместо пауз: "ну", "смотрите", "кстати", "слушайте"
3. Не больше 3 предложений подряд
4. Вопрос начинай с "как думаете," или "когда удобно,"
5. Маркеры несовершенства: "если не ошибаюсь", "ну вот", "в общем-то"
6. На неожиданное реагируй: "ничего себе," или "о как,"
7. Если не знаешь: "сейчас, секундочку, уточню"

ЕСЛИ СПРАШИВАЮТ РОБОТ ЛИ ТЫ:
"живой человек, ксения меня зовут. ну что, условия интересно послушать?"

═══ МАШИНЫ И ЦЕНЫ ═══

КОМФОРТ+ (обычная аренда):
- бельджи икс семьдесят 2025 — новым две пятьсот первые две недели, потом две восемьсот
- джили атлас про — новым две двести первые две недели, потом две восемьсот
- чери тиго семь про — новым две тыщи первые две недели, потом две четыреста
- фав бестун — две четыреста в день

КОМФОРТ+ (особые условия, без депозита, бонус тринадцать тыщ пятьсот в месяц):
- тенет тэ семь 2025 — новым две пятьсот первые две недели, потом три тыщи
- чери аризо восемь 2025 — новым две пятьсот первые две недели, потом три триста

КОМФОРТ (обычная аренда):
- джили кулрей — новым две тыщи первые две недели, потом две триста
- чери тиго четыре про — новым тыща семьсот девяносто первые две недели, потом две двести

КОМФОРТ (особые условия, без депозита, бонус двенадцать тыщ):
- бельджи икс пятьдесят 2025 — новым две триста первые две недели, потом две восемьсот

ЭКОНОМ:
- шкода рапид, хёндай солярис, фольксваген поло — тыща восемьсот пятьдесят в день

═══ ПРЕИМУЩЕСТВА — 1-2 в нужный момент ═══
"десять лет на рынке" → сомневаются в надёжности
"первый день бесплатно" → говорят "дорого"
"депозита нет совсем" → спрашивают про залог
"свой сервис ремтакс до девяти вечера" → беспокоятся про поломки
"то каждые пятнадцать тыщ км за наш счёт" → спрашивают про обслуживание
"в парк раз в две недели, путевые электронные" → говорят "далеко ехать"
"осаго и страховка включены" → спрашивают про страховку
"деньги выводишь в любое время" → спрашивают про выплаты
"штрафы гибдд пополам с парком" → боятся штрафов
"оформление тридцать минут" → говорят "нет времени"
"бонус тринадцать тыщ пятьсот за активную работу" → спрашивают про заработок
"опыт в такси не нужен, главное стаж три года" → боятся нет опыта

═══ УСЛОВИЯ ═══
- первый день бесплатно на всех машинах
- депозита нет совсем
- осаго и страховка включены
- то каждые пятнадцать тыщ км за счёт парка
- сервис ремтакс до девяти вечера
- деньги выводишь в любое время
- в парк раз в две недели
- стаж от трёх лет, возраст от двадцати одного
- опыт в такси не нужен
- оформление тридцать минут

═══ АДРЕС ═══
братеевская двадцать два а, метро алма-атинская. с десяти до половины седьмого каждый день.

═══ СЦЕНАРИЙ ═══

КОГДА КЛИЕНТ СОГЛАСЕН:
"ну смотрите, по машинам сейчас так. чери тиго семь про — две тыщи в начале, потом две четыреста. джили атлас про — две двести. первый день бесплатный, залогов нет. как думаете, когда удобно подъехать посмотреть?"

КОГДА "ДОРОГО":
"первый день вообще бесплатно — можно попробовать без риска. то и страховка за наш счёт, скрытых платежей нет. как думаете, попробуете?"

КОГДА "ПОДУМАЮ":
"что именно хочется обдумать — машина, цена или условия? может сразу проясню. кстати, если приедете сегодня — ещё один день бесплатно."

КОГДА "ДАЛЕКО ЕХАТЬ":
"приедете один раз на тридцать минут. потом путевые электронные, в офис раз в две недели. как думаете, попробуете?"
"""

SURPRISE_WORDS = ['много', 'миллион', 'лет работаю', 'офигеть', 'серьёзно', 'правда что', 'столько']
ROBOT_WORDS = ['робот', 'бот', 'искусственный', 'нейросеть', 'chatgpt', 'ии', 'программа']
SEARCH_WORDS = ['в каком году', 'когда открываете', 'какой адрес', 'где находитесь', 'как добраться']

def detect_macro(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ROBOT_WORDS):
        return "laugh"
    if any(w in t for w in SURPRISE_WORDS):
        return "ah"
    if any(w in t for w in SEARCH_WORDS):
        return "search"
    return "filler"

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
        (r'chery tiggo 7 pro', 'чери тиго семь про'),
        (r'chery tiggo 7', 'чери тиго семь'),
        (r'chery tiggo 4 pro', 'чери тиго четыре про'),
        (r'chery arrizo 8', 'чери аризо восемь'),
        (r'geely atlas pro', 'джили атлас про'),
        (r'geely coolray', 'джили кулрей'),
        (r'belgee x70', 'бельджи икс семьдесят'),
        (r'belgee x50', 'бельджи икс пятьдесят'),
        (r'tenet t7', 'тенет тэ семь'),
        (r'faw bestune', 'фав бестун'),
        (r'черри\s+тигго', 'чери тиго'),
        (r'чери\s+тигго', 'чери тиго'),
        (r'белджи', 'бельджи'), (r'билджи', 'бельджи'),
        (r'две\s+тысячи\s+восемьсот', 'две восемьсот'),
        (r'две\s+тысячи\s+пятьсот', 'две пятьсот'),
        (r'две\s+тысячи\s+четыреста', 'две четыреста'),
        (r'две\s+тысячи\s+триста', 'две триста'),
        (r'две\s+тысячи\s+двести', 'две двести'),
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи(?!\s+(двести|триста|четыреста|пятьсот|восемьсот))', 'две тыщи'),
        (r'три\s+тысячи\s+триста', 'три триста'),
        (r'три\s+тысячи', 'три тыщи'),
        (r'тысяча\s+восемьсот\s+пятьдесят', 'тыща восемьсот пятьдесят'),
        (r'тысяча\s+семьсот\s+девяносто', 'тыща семьсот девяносто'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'), (r'\bтысяча\b', 'тыща'),
        (r'тринадцать\s+тысяч', 'тринадцать тыщ'),
        (r'двенадцать\s+тысяч', 'двенадцать тыщ'),
        (r'\.{2,}', ','),
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

async def synthesize_raw(text: str) -> bytes:
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
    if PRONUNCIATION_DICT_ID:
        payload["pronunciation_dictionary_locators"] = [
            {"pronunciation_dictionary_id": PRONUNCIATION_DICT_ID}
        ]
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

async def tts(text: str) -> bytes:
    text = post_process_text(text)
    logger.info(f"TTS: {text}")
    return await synthesize_raw(text)

async def send_audio(update: Update, audio: bytes):
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_audio(af)
        os.unlink(tmp)

async def preload_audio_cache():
    macros = {
        "filler": "угу,",
        "laugh": "живой человек, ксения меня зовут.",
        "ah": "ничего себе,",
        "search": "сейчас, секундочку,",
    }
    for key, text in macros.items():
        try:
            audio = await synthesize_raw(text)
            if audio:
                audio_cache[key] = audio
                logger.info(f"Cached: {key}")
        except Exception as e:
            logger.warning(f"Cache failed {key}: {e}")

async def create_dict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создаёт словарь произношений через Railway сервер (США)"""
    await update.message.reply_text("Создаю словарь произношений через Railway...")

    url = "https://api.elevenlabs.io/v1/pronunciation-dictionaries/add-from-file"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}

    data = aiohttp.FormData()
    data.add_field("name", "momentum_cars_v1")
    data.add_field(
        "file",
        PLS_CONTENT.encode("utf-8"),
        filename="momentum.pls",
        content_type="application/x-pls+xml"
    )

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=30)) as r:
                body = await r.text()
                if r.status in (200, 201):
                    import json
                    j = json.loads(body)
                    dict_id = j.get("id") or j.get("pronunciation_dictionary_id")
                    await update.message.reply_text(
                        f"✅ Словарь создан!\n\n"
                        f"ID: {dict_id}\n\n"
                        f"Добавь в Railway Variables:\n"
                        f"PRONUNCIATION_DICT_ID = {dict_id}"
                    )
                else:
                    await update.message.reply_text(f"❌ Ошибка {r.status}: {body[:300]}")
    except Exception as e:
        await update.message.reply_text(f"❌ Исключение: {e}")

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
    results.append(f"СЛОВАРЬ: {PRONUNCIATION_DICT_ID if PRONUNCIATION_DICT_ID else 'не подключён — напиши /createdict'}")
    cached = [k for k, v in audio_cache.items() if v]
    results.append(f"Аудио-макросы: {', '.join(cached) if cached else 'нет'}")
    await update.message.reply_text("\n".join(results))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "добрый день, это ксения из таксопарка моментум. вы раньше у нас работали, звоню потому что условия сейчас стали намного лучше. уделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await update.message.reply_text(f"Ксения: {first}")
    audio = await tts(first)
    await send_audio(update, audio)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []

    user_text = update.message.text
    macro_key = detect_macro(user_text)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    reply_task = asyncio.create_task(generate_response(user_text, conversations[uid]))

    if macro_key and audio_cache.get(macro_key):
        await send_audio(update, audio_cache[macro_key])

    reply = await reply_task
    await update.message.reply_text(f"Ксения: {reply}")
    audio = await tts(reply)
    await send_audio(update, audio)

async def post_init(application):
    logger.info("Preloading audio cache...")
    await preload_audio_cache()
    logger.info(f"Ready. Dict: {PRONUNCIATION_DICT_ID or 'not set'}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(CommandHandler("createdict", create_dict_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
