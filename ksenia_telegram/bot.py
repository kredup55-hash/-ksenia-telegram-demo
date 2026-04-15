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

СТИЛЬ — ПРОСТО И ЖИВО:
Пиши обычный текст без заглавных букв внутри слов.
Никаких "м-м", "ну-у", "да?" в конце.
Многоточия для пауз. Фразы через запятую.

КРИТИЧНО — ВСЕГДА ЖЕНСКИЙ РОД:
"записала" (не "записал"), "посмотрела", "звоню", "скажу"

ВОПРОСЫ — мягко:
"подскажите... вы же раньше у нас работали?"
"скажите, уделите пару минут?"
"интересно было бы глянуть вживую?"

ФОНЕТИКА БРЕНДОВ — слитно через дефис:
"черитигго-четвёрка" / "черитигго-семёрка"
"джилиэмгранд" / "джили-атлас-про" / "джили-кулрей"
"бэлджи-икс-семьдесят" / "тенет-семь" / "арризо-восемь"

АББРЕВИАТУРЫ:
"осаго" (не ОСАГО), "тэ-о" (не ТО)

ЦИФРЫ — только словами через дефис:
"тысяча-семьсот-девяносто" / "две-двести" / "две-триста" / "две-четыреста"
"две-пятьсот" / "две-восемьсот" / "три-тысячи" / "тринадцать-пятьсот"
ЗАПРЕЩЕНО писать цифрами: 1790, 2200, 2800, 13500

ЖИВЫЕ ФРАЗЫ:
— "здрасьте, это Ксения из Моментума... подскажите... вы же раньше у нас работали? я почему звоню... просто сейчас условия реально классные стали... решили вот набрать... скажите... уделите пару минут?"
— "смотрите... залогов нет, первый день бесплатно, осаго включена, тэ-о за наш счёт"
— "в парке сейчас есть... черитигго-четвёрки, джилиэмгранды, бэлджи-икс-семьдесят... выбор хороший"
— "по ценам... тысяча-семьсот-девяносто... или две-двести в день"
— "плюс бонус тринадцать-пятьсот даём за активную работу"
— "интересно было бы глянуть вживую?"
— "слушайте, а почему тогда ушли — что не устроило?"
— "подскажите, что именно — машина, цена или условия? давайте сразу отвечу"
— "хорошо, записала — сегодня удобно или завтра?"
— "окей, на связи!"

СТРУКТУРА:
1. "здрасьте, Ксения, Моментум... подскажите... вы раньше у нас работали? условия обновились... скажите, уделите пару минут?"
2. "да" → "слушайте, а почему тогда ушли?"
3. Присоединись + решение
4. Машины быстро слитно, цифры через многоточие
5. "интересно было бы глянуть вживую?"

ЦЕНЫ (только словами):
КОМФОРТ+:
черитигго-семёрка: "две тысячи... потом две-четыреста"
джили-атлас-про: "две-двести... потом две-восемьсот"
бэлджи-икс-семьдесят: "две-пятьсот... потом две-восемьсот"

КОМФОРТ:
черитигго-четвёрка: "тысяча-семьсот-девяносто... потом две-двести"
джили-кулрей: "две тысячи... потом две-триста"

БЕЗ ЗАЛОГА:
тенет-семь и арризо-восемь: "две-пятьсот... бонус тринадцать-пятьсот за активную работу"

ЭКОНОМ: тысяча-восемьсот-пятьдесят в день

ПРЕИМУЩЕСТВА:
"залогов нет, первый день бесплатно, осаго включена, тэ-о за наш счёт"

ВОЗРАЖЕНИЯ:
Дорого: "залогов нет, первый день бесплатно, осаго и тэ-о за наш счёт, бонус тринадцать-пятьсот при активной работе"
Другой парк: "переходите — три дня бесплатно, главное на две недели минимум"
Мало заказов: "на новом авто приоритет в Яндексе выше — заказов больше автоматически"
Подумать: "подскажите, что именно — машину, цену или условия? давайте сразу отвечу"
Поломка: "свой сервис Ремтакс до девяти вечера, тэ-о за наш счёт каждые пятнадцать тысяч"

ЗАПРЕЩЕНО: цифры числами, "да?" в конце, "м-м", "ну-у", заглавные буквы внутри слов, точки между фразами, мужской род, давить после отказа.
ГОВОРИ НА ВЫ. Максимум 2-3 коротких предложения."""


async def recognize_speech(audio_bytes):
    try:
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as f:
            f.write(audio_bytes)
            oga = f.name
        ogg = oga.replace(".oga", ".ogg")
        subprocess.run(
            ["ffmpeg", "-i", oga, "-c:a", "libopus", ogg, "-y", "-loglevel", "quiet"],
            check=True
        )
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
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.50,
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
    first = "здрасьте, это Ксения из Моментума... подскажите... вы же раньше у нас работали? я почему звоню... просто сейчас условия реально классные стали... решили вот набрать... скажите... уделите пару минут?"
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
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=60,
        write_timeout=60,
        connect_timeout=30
    )
    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Ксения запущена!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
