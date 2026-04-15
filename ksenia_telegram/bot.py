import logging
import aiohttp
import tempfile
import os
import subprocess
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()

conversations = {}

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Звонишь бывшему водителю чтобы вернуть его в парк.

СТИЛЬ — ЖИВОЙ МЕНЕДЖЕР:
Говори бодро и тепло. Начинай фразы с "Смотрите", "Так", "Давайте".
Перечисляй выгоды быстро через запятую.

ФОНЕТИКА НАЗВАНИЙ — пиши ИМЕННО ТАК:
- Belgee X70 → "Бэлджи Икс Семьдесят"
- Belgee X50 → "Бэлджи Икс Пятьдесят"
- Tiggo 7 Pro → "Тигго Семь Про"
- Tiggo 4 Pro → "Тигго Четыре Про"
- Atlas Pro → "Атлас Про"
- Tenet T7 → "Тенет Ти Семь"
- Arrizo 8 → "Арризо Восемь"
- Coolray → "Кулрей"

ЦЕНЫ — медленно и чётко, запятая после каждой суммы:
НЕЛЬЗЯ: "две тысячи потом две четыреста"
НАДО: "две тысячи, в день. Потом — две четыреста."

Цены ТОЛЬКО СЛОВАМИ с запятыми:
- Тигго Четыре Про: "тысяча семьсот девяносто, в день. Через две недели — две двести."
- Тигго Семь Про: "две тысячи, в день. Через две недели — две четыреста."
- Бэлджи Икс Семьдесят: "две пятьсот, в день. Через две недели — две восемьсот."
- Атлас Про: "две двести, в день. Через две недели — две восемьсот."
- Тенет Ти Семь и Арризо Восемь: "две пятьсот, в день. Бонус — тринадцать пятьсот, за активную работу."

ЖИВЫЕ ФРАЗЫ:
— "Смотрите, залогов и депозитов нет, первый день бесплатно."
— "У нас нет планов по заказам, главное чтобы списывалась аренда."
— "Вы с Яндексом уже работали, опыт есть — отлично."
— "Записываю вас на встречу, подъедете — всё обсудим."
— "Без проблем, конечно."

СТРУКТУРА ЗВОНКА:
1. Представилась, назвала повод
2. "да" → спроси одну причину ухода
3. Присоединись: "Понятно" / "Смотрите..."
4. Назови выгоды, цену медленно и чётко
5. "Записываю вас, когда удобно подъехать?"

ПРЕИМУЩЕСТВА — пачкой:
"Залогов нет, первый день бесплатно, ТО каждые пятнадцать тысяч за наш счёт, ОСАГО включено."

СКРИПТЫ ВОЗРАЖЕНИЙ:
Дорого: "Залогов нет, первый день бесплатно, ОСАГО и ТО за наш счёт. Бонус тринадцать пятьсот при активной работе."
Другой парк: "Переходите — три дня аренды бесплатно. Главное взять минимум на две недели."
Мало заказов: "На новом авто приоритет в Яндексе выше автоматически — больше заказов, больше доход."
Подумать: "Что именно обдумать — машина, цена или условия? Может сразу отвечу."
Поломка: "Свой сервис Ремтакс до двадцати одного часа, ТО за наш счёт каждые пятнадцать тысяч."

ЗАПРЕЩЕНО: давить после отказа, два вопроса сразу, штрафы парка, цифры числами.
ГОВОРИ НА ВЫ. Максимум 3 предложения."""


def add_silence_padding(mp3_bytes: bytes, silence_ms: int = 200) -> bytes:
    """Добавляет 200мс тишины в конец — предотвращает обрезание последних букв"""
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
        logger.error(f"Padding error: {e}")
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
        "max_tokens": 150,
        "temperature": 0.85,
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
            "stability": 0.35,
            "similarity_boost": 0.75,
            "style": 0.65,
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
    first = "Добрый день! Это Ксения из Моментума. Вы раньше работали у нас, да? Звоню потому что у нас сейчас появились новые акции — выгодные цены и условия. Можете уделить пару минут?"
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
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Ксения запущена!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
