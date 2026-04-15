import logging
import aiohttp
import tempfile
import os
import subprocess
import re
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Звонишь бывшему водителю чтобы вернуть его в парк.

ГЛАВНОЕ ПРАВИЛО — НЕБРЕЖНАЯ ЖИВАЯ РЕЧЬ:
Говори быстро, немного небрежно, как будто это сотый звонок за день.
Сливай вводные слова с фразой — не делай паузу после "Смотрите":
ПЛОХО: "Смотрите... у нас сейчас..."
ХОРОШО: "Смотрите, у нас щас отличные условия"

Начинай фразы с коротких вводных: "Так-с", "Смотрите", "Слушайте", "Давайте".
Перед вопросом — лёгкое сочувствие, не допрос.
Аббревиатуры: "ОсАго" (не О-СА-ГО), "тэ-о" (не ТО).

ФОНЕТИКА НАЗВАНИЙ:
- Belgee X70 → "Бэлджи семьдесят"
- Tiggo 7 Pro → "Тигго семь про"
- Tiggo 4 Pro → "Тигго четыре про"
- Atlas Pro → "Атлас про"
- Tenet T7 → "Тенет семь"
- Arrizo 8 → "Арризо восемь"
- Coolray → "Кулрей"

ЦЕНЫ — разговорно, не как отчёт:
ПЛОХО: "одна тысяча семьсот девяносто рублей"
ХОРОШО: "тыща семьсот девяносто" / "две пятьсот" / "две восемьсот"
Между ценами — тире с паузой: "тыща семьсот девяносто — потом две двести"

ТОНАЛЬНОСТЬ ПО СИТУАЦИИ:
- Вступление: бодро, быстро
- Вопрос почему ушёл: мягко, с лёгким сочувствием, будто реально интересно
- Возражение: уверенно но не давяще
- Закрытие: легко, без давления

ЖИВЫЕ ФРАЗЫ — используй именно такие:
— "Смотрите, у нас щас залогов нет, первый день бесплатно, ОсАго включено."
— "Так-с, давайте расскажу про условия."
— "Слушайте, а почему тогда ушли — что не устроило?"
— "Понял вас, без проблем. Смотрите..."
— "Записываю вас, когда удобно подъехать — сегодня или завтра?"
— "Хорошо, на связи!"

СТРУКТУРА ЗВОНКА:
1. Быстро представилась, назвала повод
2. "да" → мягко спроси одну причину ухода
3. "Понял" / "Да, бывает" → конкретное решение
4. Цены разговорно, быстро
5. "Записываю, когда удобно?"

ЦЕНЫ (разговорно):
Тигго четыре про: "тыща семьсот девяносто — потом две двести"
Тигго семь про: "две тысячи — потом две четыреста"
Бэлджи семьдесят: "две пятьсот — потом две восемьсот"
Тенет семь и Арризо восемь: "две пятьсот, бонус тринадцать пятьсот за работу"

ПРЕИМУЩЕСТВА — одним дыханием:
"Залогов нет, первый день бесплатно, ОсАго включено, тэ-о за наш счёт."

ВОЗРАЖЕНИЯ:
Дорого: "Залогов нет, первый день бесплатно, ОсАго и тэ-о за наш счёт. Бонус тринадцать пятьсот при активной работе."
Другой парк: "Переходите — три дня бесплатно. Главное на две недели минимум."
Мало заказов: "На новом авто приоритет в Яндексе выше — заказов больше автоматически."
Подумать: "Что обдумать — машина, цена или условия? Может сразу отвечу."

ЗАПРЕЩЕНО: пауза после "Смотрите", давить после отказа, два вопроса сразу, цифры числами.
ГОВОРИ НА ВЫ. Максимум 2-3 коротких предложения."""


def add_silence_padding(mp3_bytes: bytes, silence_ms: int = 200) -> bytes:
    try:
        from pydub import AudioSegment
        import io
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        silence = AudioSegment.silent(duration=silence_ms)
        padded = audio + silence
        output = io.BytesIO()
        padded.export(output, format="mp3", bitrate="192k")
        return output.getvalue()
    except Exception as e:
        logger.error(f"Padding: {e}")
        return mp3_bytes


async def recognize_speech(audio_bytes):
    try:
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as f:
            f.write(audio_bytes)
            oga = f.name
        ogg = oga.replace(".oga", ".ogg")
        subprocess.run(["ffmpeg", "-i", oga, "-c:a", "libopus", ogg, "-y", "-loglevel", "quiet"], check=True)
        with open(ogg, "rb") as f:
            data = f.read()
        os.unlink(oga)
        os.unlink(ogg)
        url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        params = {"folderId": YANDEX_FOLDER_ID, "lang": "ru-RU", "format": "oggopus"}
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, params=params, headers=headers, data=data) as r:
                if r.status == 200:
                    j = await r.json()
                    return j.get("result", "")
        return ""
    except Exception as e:
        logger.error(f"STT: {e}")
        return ""


async def generate_response(user_text, history):
    history.append({"role": "user", "content": user_text})
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://momentum-bot.railway.app",
    }
    payload = {
        "model": "anthropic/claude-sonnet-4-5",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "max_tokens": 100,
        "temperature": 0.9,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    reply = re.sub(r'<[^>]+>', '', reply).strip()
                    logger.info(f"Reply: {reply}")
                    history.append({"role": "assistant", "content": reply})
                    return reply
        return "Простите, что-то со связью. Повторите пожалуйста."
    except Exception as e:
        logger.error(f"LLM: {e}")
        return "Простите, что-то со связью."


async def synthesize_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.28,
            "similarity_boost": 0.75,
            "style": 0.70,
            "use_speaker_boost": True,
        },
        "output_format": "mp3_44100_192",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    raw = await r.read()
                    return add_silence_padding(raw, silence_ms=200)
                err = await r.text()
                logger.error(f"TTS {r.status}: {err}")
        return b""
    except Exception as e:
        logger.error(f"TTS: {e}")
        return b""


async def send_voice(update, text):
    await update.message.reply_text(f"Ксения: {text}")
    audio = await synthesize_speech(text)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_audio(af, title="Ксения")
        os.unlink(tmp)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conversations[uid] = []
    first = "Добрый день! Это Ксения из Моментума. Вы раньше работали у нас, да? Звоню потому что у нас щас появились новые акции — выгодные цены и условия. Можете уделить пару минут?"
    conversations[uid].append({"role": "assistant", "content": first})
    await send_voice(update, first)
    await update.message.reply_text("Отвечайте голосом или текстом")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []
    await update.message.reply_text("Слушаю...")
    f = await context.bot.get_file(update.message.voice.file_id)
    ab = await f.download_as_bytearray()
    text = await recognize_speech(bytes(ab))
    if not text:
        await update.message.reply_text("Не разобрала. Напишите текстом.")
        return
    await update.message.reply_text(f"Вы: {text}")
    reply = await generate_response(text, conversations[uid])
    await send_voice(update, reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []
    reply = await generate_response(update.message.text, conversations[uid])
    await send_voice(update, reply)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations[update.effective_user.id] = []
    await update.message.reply_text("Сброшено. /start чтобы начать заново.")


def main():
    request = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60, connect_timeout=30)
    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Ксения запущена!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
