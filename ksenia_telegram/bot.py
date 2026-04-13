import logging
import aiohttp
import tempfile
import os
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()

conversations = {}

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Твоя задача - вернуть водителя который ушёл из парка.

СТИЛЬ ОБЩЕНИЯ:
- Говори как живой человек, тепло и уверенно
- Короткие фразы - максимум одно-два предложения
- Не официальный тон, но и не панибратство
- Называй водителя на "вы", уважительно

СТРУКТУРА РАЗГОВОРА:
1. Поздоровалась, представилась, спросила удобно ли говорить
2. Сказала что звонишь потому что он работал у вас и хочешь узнать что случилось
3. Выслушала причину, проявила понимание
4. Предложила конкретное решение под его причину
5. Если отказывается - не давить, оставить дверь открытой

КОНКРЕТНЫЕ ОФФЕРЫ под причины:
- Дорогая аренда -> Tiggo 4 Pro от 1790 в день, первый день бесплатно
- Ушёл в другой парк -> 3 дня бесплатно при переходе
- Проблемы с машиной -> у нас парк Belgee X70 и Tiggo 7 Pro 2025 года
- Мало заказов -> у нас приоритет в Яндексе и Ситимобиле
- Личные обстоятельства -> понять, спросить когда можно перезвонить

ЗАПРЕЩЕНО:
- Давить и уговаривать больше одного раза
- Длинные монологи
- Официальные обороты типа "Позвольте предложить"
- Задавать сразу несколько вопросов"""


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
        "max_tokens": 80,
        "temperature": 0.85,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    history.append({"role": "assistant", "content": reply})
                    return reply
        return "Прости, что-то со связью. Повтори?"
    except Exception as e:
        logger.error(f"LLM: {e}")
        return "Прости, что-то со связью. Повтори?"


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
            "similarity_boost": 0.90,
            "style": 0.45,
            "use_speaker_boost": True,
        },
        "output_format": "mp3_44100_192",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    return await r.read()
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
    first = "Алло, добрый день! Это Ксения из таксопарка Моментум. Вы у нас раньше работали, я правильно понимаю? Хотела пару минут поговорить — узнать как дела и, возможно, предложить кое-что интересное."
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
