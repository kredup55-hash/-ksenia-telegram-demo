import logging, aiohttp, tempfile, os, re, io, asyncio
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

SYSTEM_PROMPT = """Ты Ксения из таксопарка Моментум. ТЫ ЖЕНЩИНА.
ТЫ УЖЕ ПОЗДОРОВАЛАСЬ В САМОМ НАЧАЛЕ.

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
- Использовать приветствия: привет, приветик, здравствуйте.
- Начинать ответ с подтверждений типа "да конечно", "я во внимании", "хорошо".
- Писать цифры числами: 2000, 1790, 13500 — только словами!

ПРАВИЛА ТЕКСТА:
1. Пиши только маленькими буквами. Ставь точку после каждой мысли.
2. Когда спрашивают про преимущества — выдавай весь список сразу короткими фразами.
3. ЦЕНЫ ПИШИ СТРОГО СЛОВАМИ через дефис: "две-тыщи", "две-двести", "две-четыреста", "тыща-семьсот-девяносто".

ФОНЕТИКА МАРОК — пиши слитно через дефис:
- Chery Tiggo 7 Pro → "черритигго-семёрка"
- Chery Tiggo 4 Pro → "черритигго-четвёрка"
- Geely Atlas Pro → "джили-атлас-про"
- Geely Coolray → "джили-кулрей"
- Belgee X70 → "бэлджи-икс-семьдесят"
- Tenet T7 → "тенет-семь"
- Arrizo 8 → "арризо-восемь"
ОСАГО → "осаго", ТО → "тэ-о"

ТВОЙ СЦЕНАРИЙ:
Если клиент согласен: "отлично. смотрите, по машинам сейчас так."
Цены: черритигго-семёрка — две-тыщи первые две недели, потом две-четыреста. джили-атлас-про — две-двести, потом две-восемьсот. бэлджи-икс-семьдесят — две-пятьсот, потом две-восемьсот. черритигго-четвёрка — тыща-семьсот-девяносто, потом две-двести.
Преимущества: залогов нет. первый день бесплатно. осаго включена. тэ-о за наш счёт. бонус тринадцать-пятьсот за активную работу.

ВОЗРАЖЕНИЯ:
Дорого: "залогов нет, первый день бесплатно, осаго и тэ-о за наш счёт. бонус тринадцать-пятьсот при активной работе."
Другой парк: "переходите — три дня бесплатно. главное на две недели минимум."
Мало заказов: "на новом авто приоритет в яндексе выше — заказов больше автоматически."
Подумать: "что именно — машину, цену или условия? может сразу отвечу."

В конце задай один вопрос: "интересно было бы попробовать?"

ГОВОРИ НА ВЫ. Максимум 2-3 коротких предложения."""


def process_audio_quality(mp3_bytes: bytes) -> bytes:
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        silence = AudioSegment.silent(duration=800)
        combined = audio + silence
        out = io.BytesIO()
        combined.export(out, format="mp3", bitrate="192k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Audio error: {e}")
        return mp3_bytes


async def synthesize_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.28,
            "similarity_boost": 0.90,
            "style": 0.65,
            "use_speaker_boost": True
        },
        "output_format": "mp3_44100_192",
        "optimize_streaming_latency": 1
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, headers=headers) as r:
            if r.status == 200:
                raw = await r.read()
                return process_audio_quality(raw)
    return b""


async def generate_response(user_text, history):
    history.append({"role": "user", "content": user_text})
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "anthropic/claude-sonnet-4-5",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "max_tokens": 300,
        "temperature": 0.85,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    reply = re.sub(r'<[^>]+>', '', reply).strip()
                    history.append({"role": "assistant", "content": reply})
                    return reply
    except Exception as e:
        logger.error(f"AI Error: {e}")
    return "простите, связь барахлит."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "здрасьте, это Ксения из Моментума. вы раньше у нас работали. я звоню потому что сейчас условия классные стали. уделите пару минут?"
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


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations[update.effective_user.id] = []
    await update.message.reply_text("Сброшено. /start чтобы начать заново.")


def main():
    request = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60, connect_timeout=30)
    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Ксения запущена!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
