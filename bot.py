import logging, aiohttp, tempfile, os, re, io, asyncio, random, json, threading
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()
PRONUNCIATION_DICT_ID = os.environ.get("PRONUNCIATION_DICT_ID", "").strip()
PORT = int(os.environ.get("WEBHOOK_PORT", 8082))

conversations = {}
audio_cache = {"filler": None, "laugh": None, "ah": None, "search": None}

PLS_MINIMAL = """<?xml version="1.0" encoding="UTF-8"?>
<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.w3.org/2005/01/pronunciation-lexicon http://www.w3.org/TR/2007/CR-pronunciation-lexicon-20071212/pls.xsd" alphabet="ipa" xml:lang="ru-RU">
<lexeme>
<grapheme>тыщи</grapheme>
<alias>тыщи</alias>
</lexeme>
</lexicon>"""

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

МАШИНЫ И ЦЕНЫ:

КОМФОРТ+ (обычная аренда):
- бельджи икс семьдесят — новым две пятьсот первые две недели, потом две восемьсот
- атлас про — новым две двести первые две недели, потом две восемьсот
- тиго семь про — новым две тыщи первые две недели, потом две четыреста
- фав бестун — две четыреста в день

КОМФОРТ+ (без депозита, бонус тринадцать тыщ пятьсот в месяц):
- тенет тэ семь — новым две пятьсот первые две недели, потом три тыщи
- аризо восемь — новым две пятьсот первые две недели, потом три триста

КОМФОРТ (обычная аренда):
- кулрей — новым две тыщи первые две недели, потом две триста
- тиго четыре про — новым тыща семьсот девяносто первые две недели, потом две двести

КОМФОРТ (без депозита, бонус двенадцать тыщ):
- бельджи икс пятьдесят — новым две триста первые две недели, потом две восемьсот

ЭКОНОМ:
- рапид, солярис, поло — тыща восемьсот пятьдесят в день

ПРЕИМУЩЕСТВА — 1-2 в нужный момент:
- первый день бесплатно — говорят "дорого"
- депозита нет совсем — спрашивают про залог
- сервис ремтакс до девяти вечера — беспокоятся про поломки
- в парк раз в две недели, путевые электронные — говорят "далеко ехать"
- осаго и страховка включены — спрашивают про страховку
- деньги выводишь в любое время — спрашивают про выплаты
- оформление тридцать минут — говорят "нет времени"
- опыт в такси не нужен, главное стаж три года — боятся нет опыта

УСЛОВИЯ:
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

АДРЕС:
братеевская двадцать два а, метро алма-атинская. с десяти до половины седьмого каждый день.

КОГДА КЛИЕНТ СОГЛАСЕН:
"ну смотрите, по машинам сейчас так. тиго семь про — две тыщи в начале, потом две четыреста. атлас про — две двести. первый день бесплатный, залогов нет. как думаете, когда удобно подъехать посмотреть?"

КОГДА "ДОРОГО":
"первый день вообще бесплатно — можно попробовать без риска. то и страховка за наш счёт, скрытых платежей нет. как думаете, попробуете?"

КОГДА "ПОДУМАЮ":
"что именно хочется обдумать — машина, цена или условия? может сразу проясню. кстати, если приедете сегодня — ещё один день бесплатно."

КОГДА "ДАЛЕКО ЕХАТЬ":
"приедете один раз на тридцать минут. потом путевые электронные, в офис раз в две недели. как думаете, попробуете?"
"""

SURPRISE_WORDS = ['много', 'миллион', 'лет работаю', 'офигеть', 'серьёзно', 'столько']
ROBOT_WORDS = ['робот', 'бот', 'искусственный', 'нейросеть', 'chatgpt', 'программа']
SEARCH_WORDS = ['в каком году', 'когда открываете', 'какой адрес', 'где находитесь']

def detect_macro(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ROBOT_WORDS): return "laugh"
    if any(w in t for w in SURPRISE_WORDS): return "ah"
    if any(w in t for w in SEARCH_WORDS): return "search"
    return "filler"

def remove_emoji(text: str) -> str:
    return re.compile("[\U00010000-\U0010ffff\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+", flags=re.UNICODE).sub('', text).strip()

def post_process_text(text: str) -> str:
    text = remove_emoji(text)
    fixes = [
        (r'(?i)chery\s+tiggo\s+7\s+pro', 'тиго семь про'),
        (r'(?i)chery\s+tiggo\s+7', 'тиго семь'),
        (r'(?i)chery\s+tiggo\s+4\s+pro', 'тиго четыре про'),
        (r'(?i)chery\s+arrizo\s+8', 'аризо восемь'),
        (r'(?i)geely\s+atlas\s+pro', 'атлас про'),
        (r'(?i)geely\s+atlas', 'атлас'),
        (r'(?i)geely\s+coolray', 'кулрей'),
        (r'(?i)belgee\s+x\s*70', 'бельджи икс семьдесят'),
        (r'(?i)belgee\s+x\s*50', 'бельджи икс пятьдесят'),
        (r'(?i)tiggo\s+7', 'тиго семь'),
        (r'(?i)tiggo\s+4', 'тиго четыре'),
        (r'(?i)coolray', 'кулрей'),
        (r'(?i)tenet\s+t7', 'тенет тэ семь'),
        (r'(?i)faw\s+bestune', 'фав бестун'),
        (r'черри\s+тигго', 'тиго'), (r'чери\s+тигго', 'тиго'),
        (r'белджи', 'бельджи'), (r'билджи', 'бельджи'),
        (r'\b2000\b', 'две тыщи'), (r'\b2200\b', 'две двести'),
        (r'\b2300\b', 'две триста'), (r'\b2400\b', 'две четыреста'),
        (r'\b2500\b', 'две пятьсот'), (r'\b2800\b', 'две восемьсот'),
        (r'\b1790\b', 'тыща семьсот девяносто'), (r'\b1850\b', 'тыща восемьсот пятьдесят'),
        (r'\b4000\b', 'четыре тыщи'), (r'\b6000\b', 'шесть тыщ'),
        (r'две\s+тысячи\s+восемьсот', 'две восемьсот'),
        (r'две\s+тысячи\s+пятьсот', 'две пятьсот'),
        (r'две\s+тысячи\s+четыреста', 'две четыреста'),
        (r'две\s+тысячи\s+триста', 'две триста'),
        (r'две\s+тысячи\s+двести', 'две двести'),
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи(?!\s+(двести|триста|четыреста|пятьсот|восемьсот))', 'две тыщи'),
        (r'три\s+тысячи\s+триста', 'три триста'), (r'три\s+тысячи', 'три тыщи'),
        (r'тысяча\s+восемьсот\s+пятьдесят', 'тыща восемьсот пятьдесят'),
        (r'тысяча\s+семьсот\s+девяносто', 'тыща семьсот девяносто'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'), (r'\bтысяча\b', 'тыща'),
        (r'тринадцать\s+тысяч', 'тринадцать тыщ'), (r'двенадцать\s+тысяч', 'двенадцать тыщ'),
        (r'(?i)братьевской', 'братеевской'), (r'(?i)братьевская', 'братеевская'),
        (r'(?i)алма-атинское', 'алма-атинская'),
        (r'\.{2,}', ','), (r',\s*,', ','), (r' {2,}', ' '),
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
        n = int(44100 * len(voice) / 1000.0)
        t = np.linspace(0, len(voice)/1000.0, n)
        noise_arr = ((np.random.normal(0, noise_volume*0.5, n) + noise_volume*0.3*np.sin(2*np.pi*60*t)) * 32767).astype(np.int16)
        mixed = voice.overlay(AudioSegment(noise_arr.tobytes(), frame_rate=44100, sample_width=2, channels=1))
        out = io.BytesIO()
        mixed.export(out, format="mp3", bitrate="128k")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"Noise: {e}")
        return voice_bytes

async def synthesize_raw(text: str) -> bytes:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text, "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.50, "similarity_boost": 0.80, "style": 0.30, "use_speaker_boost": True},
        "optimize_streaming_latency": 4
    }
    if PRONUNCIATION_DICT_ID:
        payload["pronunciation_dictionary_locators"] = [{"pronunciation_dictionary_id": PRONUNCIATION_DICT_ID}]
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 200:
                    return await asyncio.get_event_loop().run_in_executor(None, mix_with_office_noise, await r.read())
                logger.error(f"EL {r.status}: {(await r.text())[:100]}")
    except Exception as e:
        logger.error(f"TTS: {e}")
    return b""

async def tts(text: str) -> bytes:
    return await synthesize_raw(post_process_text(text))

async def send_audio(update: Update, audio: bytes):
    if not audio: return
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio); tmp = f.name
    with open(tmp, "rb") as af: await update.message.reply_audio(af)
    os.unlink(tmp)

async def preload_audio_cache():
    for key, text in {"filler": "угу,", "laugh": "живой человек, ксения меня зовут.", "ah": "ничего себе,", "search": "сейчас, секундочку,"}.items():
        try:
            audio = await synthesize_raw(text)
            if audio: audio_cache[key] = audio; logger.info(f"Cached: {key}")
        except Exception as e: logger.warning(f"Cache {key}: {e}")

async def create_dict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Создаю словарь (минимальный тест)...")
    url = "https://api.elevenlabs.io/v1/pronunciation-dictionaries/add-from-file"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    data = aiohttp.FormData()
    data.add_field("name", "momentum_test")
    data.add_field("file", PLS_MINIMAL.encode("utf-8"), filename="dict.pls", content_type="application/x-pls+xml")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=30)) as r:
                body = await r.text()
                logger.info(f"Dict {r.status}: {body}")
                if r.status in (200, 201):
                    dict_id = json.loads(body).get("id") or json.loads(body).get("pronunciation_dictionary_id")
                    await update.message.reply_text(f"✅ Создан!\n\nPRONUNCIATION_DICT_ID = {dict_id}")
                else:
                    await update.message.reply_text(f"❌ {r.status}:\n{body[:400]}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def generate_response(user_text: str, history: list) -> str:
    history.append({"role": "user", "content": user_text})
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://openrouter.ai/api/v1/chat/completions",
                json={"model": "anthropic/claude-haiku-4.5", "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history, "temperature": 0.5, "max_tokens": 150},
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "HTTP-Referer": "https://railway.app", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    reply = (await r.json())["choices"][0]["message"]["content"]
                    reply = re.sub(r'^(Ксения|Ksenia|Ответ|assistant)\s*:', '', reply, flags=re.IGNORECASE).strip()
                    reply = remove_emoji(reply)
                    history.append({"role": "assistant", "content": reply})
                    return reply
    except asyncio.TimeoutError: return "[таймаут]"
    except Exception as e: logger.error(f"LLM: {e}")
    return "[ошибка соединения]"

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\n".join([
        f"OPENROUTER: {'есть' if OPENROUTER_API_KEY else 'НЕТ'}",
        f"ELEVENLABS: {'есть' if ELEVENLABS_API_KEY else 'НЕТ'}",
        f"СЛОВАРЬ: {PRONUNCIATION_DICT_ID or 'нет — напиши /createdict'}",
        f"Макросы: {', '.join(k for k,v in audio_cache.items() if v) or 'нет'}",
    ]))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "добрый день, это ксения из таксопарка моментум. вы раньше у нас работали, звоню потому что условия сейчас стали намного лучше. уделите пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await update.message.reply_text(f"Ксения: {first}")
    await send_audio(update, await tts(first))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations: conversations[uid] = []
    user_text = update.message.text
    macro_key = detect_macro(user_text)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    reply_task = asyncio.create_task(generate_response(user_text, conversations[uid]))
    if macro_key and audio_cache.get(macro_key):
        await send_audio(update, audio_cache[macro_key])
    reply = await reply_task
    await update.message.reply_text(f"Ксения: {reply}")
    await send_audio(update, await tts(reply))

async def post_init(application):
    await preload_audio_cache()

# ═══ VAPI LLM ENDPOINT ═══

async def handle_llm(request: web.Request) -> web.Response:
    import requests as req_lib
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")
    messages = [m for m in body.get("messages", []) if m.get("role") != "system"]
    stream = body.get("stream", False)
    try:
        resp = await asyncio.get_event_loop().run_in_executor(None, lambda: req_lib.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={"model": "anthropic/claude-sonnet-4-5", "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages, "temperature": 0.3, "max_tokens": 200},
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "HTTP-Referer": "https://railway.app", "Content-Type": "application/json"},
            timeout=20
        ))
        if resp.status_code == 200:
            reply = resp.json()["choices"][0]["message"]["content"]
            reply = post_process_text(reply)
            logger.info(f"Vapi reply: {reply[:80]}")
        else:
            logger.error(f"OpenRouter {resp.status_code}: {resp.text[:200]}")
            reply = "секундочку."
    except Exception as e:
        logger.error(f"Vapi LLM error: {e}")
        reply = "секундочку."
    if stream:
        chunk = json.dumps({"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"delta": {"role": "assistant", "content": reply}, "index": 0, "finish_reason": None}]})
        done = json.dumps({"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]})
        body_bytes = f"data: {chunk}\n\ndata: {done}\n\ndata: [DONE]\n\n".encode()
        return web.Response(status=200, body=body_bytes, headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    result = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": reply}, "index": 0, "finish_reason": "stop"}]
    }
    return web.json_response(result)

async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK")

async def run_web_server():
    app_web = web.Application()
    app_web.router.add_post("/llm", handle_llm)
    app_web.router.add_post("/chat/completions", handle_llm)
    app_web.router.add_get("/health", handle_health)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

def main():
    loop = asyncio.new_event_loop()

    def start_web():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_web_server())
        loop.run_forever()

    t = threading.Thread(target=start_web, daemon=True)
    t.start()

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(CommandHandler("createdict", create_dict_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
