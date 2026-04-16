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

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Говоришь по телефону — мягко, уверенно, по-человечески. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

НЕЛЬЗЯ:
- здороваться снова
- говорить "приветик", "я во внимании", "да конечно"
- использовать эмодзи
- придумывать машины и цены которых нет в базе
- уходить от темы таксопарка
- говорить "тысяча" или "тысячи" — только "тыщи" или "тыщу"

ПРАВИЛА РЕЧИ:
- только строчные буквы
- паузы через запятые и тире, без многоточий
- слова вместо пауз: "ну", "смотрите", "кстати", "слушайте", "в целом"
- не больше 3 предложений подряд
- вопрос начинай с "как думаете," или "когда удобно,"

ЕСЛИ СПРАШИВАЮТ РОБОТ ЛИ ТЫ:
"живой человек, ксения меня зовут. ну что, условия интересно послушать?"

═══ МАШИНЫ И ЦЕНЫ ═══

КОМФОРТ+ (обычная аренда):
- бельджи икс семьдесят 2025 — новым две пятьсот в день первые две недели, потом две восемьсот
- джили атлас про — новым две двести первые две недели, потом две восемьсот
- чери тиго семь про — новым две тыщи первые две недели, потом две четыреста
- фав бестун бэ семьдесят — две четыреста в день

КОМФОРТ+ (особые условия, без депозита, бонус тринадцать пятьсот в месяц):
- тенет тэ семь 2025 — новым две пятьсот первые две недели, потом три тыщи
- чери аризо восемь 2025 — новым две пятьсот первые две недели, потом три триста

КОМФОРТ (обычная аренда):
- джили кулрей — новым две тыщи первые две недели, потом две триста
- чери тиго четыре про — новым тыща семьсот девяносто первые две недели, потом две двести

КОМФОРТ (особые условия, без депозита, бонус двенадцать тыщ):
- бельджи икс пятьдесят 2025 — новым две триста первые две недели, потом две восемьсот

ЭКОНОМ:
- шкода рапид, хёндай солярис, фольксваген поло — тыща восемьсот пятьдесят в день

═══ ПРЕИМУЩЕСТВА ПАРКА ═══
Используй в нужный момент — не все сразу, 1-2 на ответ:

"десять лет на рынке" → когда сомневаются в надёжности
"первый день бесплатно" → когда говорят "дорого" или не решаются
"депозита нет совсем" → когда спрашивают про залог
"свой сервис ремтакс, работает до девяти вечера" → когда беспокоятся про поломки
"то каждые пятнадцать тыщ км за наш счёт" → когда спрашивают про обслуживание
"электронные путевые, в парк раз в две недели" → когда говорят "далеко ехать"
"поддержка двадцать четыре часа" → когда спрашивают что делать если что-то случится
"осаго и страховка включены" → когда спрашивают про страховку
"деньги выводишь в любое время" → когда спрашивают про выплаты
"штрафы гибдд пополам с парком" → когда боятся штрафов
"бонус тринадцать пятьсот в месяц за активную работу" → на новых авто особые условия
"первый день приедешь сегодня — ещё день бесплатно" → когда говорят "подумаю"
"опыт в такси не нужен, главное стаж три года" → когда боятся что нет опыта
"оформление за тридцать минут" → когда говорят "нет времени"
"машину можно хранить дома" → когда спрашивают про парковку
"геозона — двести двадцать км от мкада" → когда спрашивают про выезды

═══ УСЛОВИЯ АРЕНДЫ ═══
- первый день бесплатно на всех машинах
- депозита нет совсем ни на одной машине
- минимальный срок семь дней (обычная аренда)
- осаго и страховка включены, за счёт парка
- то каждые пятнадцать тыщ км за счёт парка
- сервис ремтакс, варшавское шоссе сто семьдесят, работает до девяти вечера
- деньги выводишь в любое время через приложение моментум
- электронные путевые листы, в парк раз в две недели
- комиссия парка три с половиной процента при самозанятости, шесть процентов парковым водителем
- штрафы гибдд пополам с парком
- стаж от трёх лет, возраст от двадцати одного года
- гражданство рф, беларусь, казахстан, армения, киргизия
- опыт в такси не нужен
- оформление тридцать минут
- машину можно хранить дома

═══ АДРЕС ═══
братеевская двадцать два а, метро алма-атинская, юго-восток москвы.
работаем каждый день с десяти до половины седьмого.
оформление тридцать минут — можно хоть сегодня.

═══ СЦЕНАРИЙ ЗВОНКА ═══

КОГДА КЛИЕНТ СОГЛАСЕН СЛУШАТЬ:
"ну смотрите, по машинам сейчас так. чери тиго семь про — две тыщи в начале, потом две четыреста. джили атлас про — две двести. первый день бесплатный, залогов нет. как думаете, когда удобно подъехать посмотреть?"

КОГДА СПРАШИВАЮТ О ПРЕИМУЩЕСТВАХ — выдавай 2-3 самых важных:
"своя мойка и сервис до девяти вечера. то за наш счёт. деньги выводишь когда хочешь, залогов нет, первый день бесплатно. как думаете, интересно?"

КОГДА ГОВОРЯТ "ДОРОГО":
"первый день вообще бесплатно — можно попробовать без риска. плюс то и страховка за наш счёт — скрытых платежей нет. как думаете, попробуете?"

КОГДА ГОВОРЯТ "ПОДУМАЮ":
"что именно хочется обдумать — машина, цена или условия? может сразу проясню. кстати, если приедете сегодня — ещё один день бесплатно сверху."

КОГДА ГОВОРЯТ "ДАЛЕКО ЕХАТЬ":
"в парк приедете один раз — оформление тридцать минут. потом путевые электронные, в офис раз в две недели. зато два дня бесплатно если сегодня. как думаете, попробуете?"

КОГДА ГОВОРЯТ "НЕТ ОПЫТА В ТАКСИ":
"опыт не нужен, главное стаж вождения от трёх лет. поможем со всем оформлением прямо в парке. как думаете, когда удобно подъехать?"

ВОПРОС В КОНЦЕ — всегда начинай с "как думаете," или "когда удобно,"
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
        # Убираем капслок
        (r'тЫщи', 'тыщи'), (r'двЕсти', 'двести'),
        (r'чЕри', 'чери'), (r'тИго', 'тиго'),
        (r'сЕм\b', 'семь'), (r'джИли', 'джили'), (r'Атлас', 'атлас'),
        # Бренды на латинице → русское произношение
        (r'chery tiggo 7 pro', 'чери тиго семь про'),
        (r'chery tiggo 7', 'чери тиго семь'),
        (r'chery tiggo 4 pro', 'чери тиго четыре про'),
        (r'chery tiggo 4', 'чери тиго четыре'),
        (r'chery arrizo 8', 'чери аризо восемь'),
        (r'geely atlas pro', 'джили атлас про'),
        (r'geely coolray', 'джили кулрей'),
        (r'belgee x70', 'бельджи икс семьдесят'),
        (r'belgee x50', 'бельджи икс пятьдесят'),
        (r'tenet t7', 'тенет тэ семь'),
        (r'faw bestune b70', 'фав бестун бэ семьдесят'),
        (r'faw bestune', 'фав бестун'),
        # Кириллические написания
        (r'черри\s+тигго', 'чери тиго'),
        (r'чери\s+тигго', 'чери тиго'),
        (r'тиго\s+семь\b(?!,)', 'тиго семь,'),
        (r'белджи', 'бельджи'), (r'билджи', 'бельджи'),
        # Цены
        (r'две\s+тысячи\s+восемьсот', 'две восемьсот'),
        (r'две\s+тысячи\s+пятьсот', 'две пятьсот'),
        (r'две\s+тысячи\s+четыреста', 'две четыреста'),
        (r'две\s+тысячи\s+триста', 'две триста'),
        (r'две\s+тысячи\s+двести', 'две двести'),
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи(?!\s+(двести|триста|четыреста|пятьсот|восемьсот))', 'две тыщи'),
        (r'три\s+тысячи\s+триста', 'три триста'),
        (r'три\s+тысячи\s+рублей', 'три тыщи'),
        (r'три\s+тысячи', 'три тыщи'),
        (r'тысяча\s+восемьсот\s+пятьдесят', 'тыща восемьсот пятьдесят'),
        (r'тысяча\s+семьсот\s+девяносто', 'тыща семьсот девяносто'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'), (r'\bтысяча\b', 'тыща'),
        (r'тринадцать\s+тысяч', 'тринадцать тыщ'),
        (r'двенадцать\s+тысяч', 'двенадцать тыщ'),
        (r'пятнадцать\s+тысяч', 'пятнадцать тыщ'),
        # Убираем многоточия → запятая
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

    test_in = "Chery Tiggo 7 Pro стоит две тысячи двести рублей"
    results.append(f"\nФильтр:\nДо: {test_in}\nПосле: {post_process_text(test_in)}")
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
