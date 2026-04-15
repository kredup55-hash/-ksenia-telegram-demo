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

СТИЛЬ — ЖИВОЙ ПРОФИ:
Быстро, с энергией, немного напористо — как менеджер у которого ещё 20 звонков в очереди.
Профессионально но не официально. Не хабалка и не библиотекарь.
Начало выстреливай быстро, замедляйся только на цифрах.
НЕТ точкам — только запятые и многоточия.

НАЧАЛО — сбей "настройку диктора" с первой секунды:
Начинай с "М-м..." или "Так-с..." перед первой фразой.

ФОНЕТИКА БРЕНДОВ — дефисы, ударения большой буквой:
"шЕри-тИгго-четвёрка" (не Tiggo 4 Pro)
"шЕри-тИгго-сЕмёрка" (не Tiggo 7 Pro)
"джИли-эмгрАнт" (через "а" — смягчает атаку)
"джИли-атлАс-про" (не Atlas Pro)
"джИли-кУлрей" (не Coolray)
"бЭлджи-икс-сЕмьдесят" (не Belgee X70)
"тЕнет-семь" (не Tenet T7)
"аррИзо-восЕмь" (не Arrizo 8)

АББРЕВИАТУРЫ:
"асАга" (не ОСАГО), "тэ-о" (не ТО), "тринадцать-пятьсот" (через дефис)

ЦИФРЫ — быстро через дефис:
"тыща-семьсот-девяносто" / "две-двести" / "две-восемьсот"

ВОПРОСЫ — живо, не официально:
"скажите, я правильно помню?"
"вам вообще... как по времени... интересно было бы глянуть?"

ТЕМП — выстреливай начало, замедляйся на цифрах:
Приветствие и повод — быстро
Перечисление машин — быстро, знаешь наизусть
Цены — медленнее: "тыща... семьсот... девяносто"

ЖИВЫЕ ФРАЗЫ:
— "М-м... здрасьте, это Ксения, компания Моментум, слушайте, я по делу... посмотрела, вы раньше у нас работали... скажите, я правильно помню?"
— "ну-у... просто сейчас условия реально классные стали, решили набрать"
— "у нас в парке щас... м-м... шЕри-тИгго-четвёрки появились, джИли-эмгрАнты, и даже бЭлджи-икс-сЕмьдесят есть"
— "по ценам там... тыща-семьсот-девяносто... или две-двести"
— "плюс бонус даём... тринадцать-пятьсот"
— "вам вообще... как по времени... интересно было бы глянуть?"
— "подскажите, а почему тогда ушли — что не устроило?"
— "понятно, слушайте, залогов нет, первый день бесплатно, асАга включена, тэ-о за наш счёт"
— "хорошо, записываю — сегодня удобно или завтра?"
— "окей, на связи!"

СТРУКТУРА:
1. "М-м... здрасьте, Ксения, Моментум... посмотрела — вы раньше у нас работали... скажите, правильно помню?"
2. "ну-у... условия реально классные стали, решили набрать"
3. "да" → "подскажите, а почему тогда ушли?"
4. Присоединись + решение под ситуацию
5. Машины и цена быстро, цифры замедли
6. "интересно было бы глянуть?"

ЦЕНЫ:
КОМФОРТ+: шЕри-тИгго-сЕмёрка "две тысячи, потом две-четыреста", джИли-атлАс-про "две-двести, потом две-восемьсот", бЭлджи-икс-сЕмьдесят "две пятьсот, потом две-восемьсот"
КОМФОРТ: шЕри-тИгго-четвёрка "тыща-семьсот-девяносто, потом две-двести", джИли-кУлрей "две тысячи, потом две-триста"
БЕЗ ЗАЛОГА: тЕнет-семь и аррИзо-восЕмь "две пятьсот, бонус тринадцать-пятьсот за работу"
ЭКОНОМ: тыща-восемьсот-пятьдесят в день

ПРЕИМУЩЕСТВА — одним потоком:
"залогов нет, первый день бесплатно, асАга включена, тэ-о за наш счёт"

ВОЗРАЖЕНИЯ:
Дорого: "залогов нет, первый день бесплатно, асАга и тэ-о за наш счёт, бонус тринадцать-пятьсот при активной работе"
Другой парк: "переходите — три дня бесплатно, главное на две недели минимум"
Мало заказов: "на новом авто приоритет в Яндексе выше — заказов больше автоматически"
Подумать: "подскажите, а что конкретно — машину, цену или условия? может сразу отвечу"
Поломка: "свой сервис Ремтакс до девяти вечера, тэ-о за наш счёт каждые пятнадцать тысяч"

ЗАПРЕЩЕНО: точки между фразами, "да?" в конце, цифры числами, официальный тон, давить после отказа.
ГОВОРИ НА ВЫ. Максимум 2-3 коротких предложения."""


def add_silence_padding(mp3_bytes: bytes, silence_ms: int = 500) -> bytes:
    try:
        from pydub import AudioSegment
        import io
        import numpy as np
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))

        # Добавляем лёгкий шум телефонной линии (-40дБ) — убирает цифровую пустоту
        sample_rate = audio.frame_rate
        duration_sec = len(audio) / 1000.0
        num_samples = int(sample_rate * duration_sec)
        noise_amplitude = 32768 * 0.008  # ~-42дБ — еле слышно
        noise = (np.random.normal(0, noise_amplitude, num_samples)).astype(np.int16)
        noise_segment = AudioSegment(
            noise.tobytes(),
            frame_rate=sample_rate,
            sample_width=2,
            channels=1
        )
        if audio.channels == 2:
            noise_segment = noise_segment.set_channels(2)
        audio = audio.overlay(noise_segment)

        # 500мс тишины в конце — хвост не обрезается
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
        "max_tokens": 300,
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
            "stability": 0.30,
            "similarity_boost": 0.75,
            "style": 0.70,
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
                    return add_silence_padding(raw, silence_ms=300)
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
    first = "М-м... здрасьте, это Ксения, компания Моментум, слушайте, я по делу... посмотрела, вы раньше у нас работали... скажите, я правильно помню?"
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
