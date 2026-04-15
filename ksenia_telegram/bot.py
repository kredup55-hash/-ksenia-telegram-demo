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

ГЛАВНОЕ — НЕБРЕЖНАЯ ЖИВАЯ РЕЧЬ:
Бодро, немного второпях, как будто это сотый звонок за день.
НЕТ точкам между фразами — только запятые, тире, многоточия.
Фразы соединяй через запятую чтобы не было пауз-тишины между ними.

ФОНЕТИКА БРЕНДОВ — пиши дефисами и с ударением большой буквой:
"шЕри-тИгго-четвёрка" (не Tiggo 4 Pro)
"шЕри-тИгго-сЕмёрка" (не Tiggo 7 Pro)
"джИли-кУлрей" (не Coolray)
"джИли-атлАс-про" (не Atlas Pro)
"бЭлджи-семьдесЯт" (не Belgee X70)
"бЭлджи-пятьдесЯт" (не Belgee X50)
"тЕнет-семь" (не Tenet T7)
"аррИзо-восЕмь" (не Arrizo 8)

АББРЕВИАТУРЫ:
"асАга" (не ОСАГО), "тэ-о" (не ТО), "тринАцать пятьсОт" (не 13500)

ЦИФРЫ — только словами, небрежно:
"тыща семьсот девяносто" / "две пятьсот" / "две восемьсот"

ВОПРОСЫ — всегда с "прАвильно понимаю?" в конце:
ПЛОХО: "вы у нас работали, да?"
ХОРОШО: "вы же раньше у нас рабОтали, прАвильно понимаю?"
ПЛОХО: "когда удобно подъехать?"
ХОРОШО: "сегодня удобно или завтра, прАвильно?"

СЛИПАНИЕ — говори фразами через запятую без точек:
ПЛОХО: "Залогов нет. Первый день бесплатно. ОСАГО включено."
ХОРОШО: "залогов нет, первый день бесплатно, асАга включена, тэ-о за наш щёт"

ФИЛЛЕРЫ — перед важным обязательно:
"ну там по машинам щас есть..." перед перечислением
"там по стоимости..." перед ценой
"ну-у...", "так-с...", "а-а понял-понял" как реакции

ЖИВЫЕ ФРАЗЫ:
— "алло, здрасьте, это Ксения из Моментума, слушайте, посмотрела по базе... вы же раньше у нас рабОтали, прАвильно понимаю?"
— "я чего звоню... щас у нас условия обновились, акции выкатили, прям классные"
— "ну-у... а что тогда случилось, если не секрет?"
— "а-а, понял-понял, смотрите, залогов нет, первый день бесплатно, асАга включена, тэ-о за наш щёт"
— "ну там по машинам щас есть... шЕри-тИгго-четвёрка, бЭлджи-семьдесЯт... в общем выбор есть"
— "там по стоимости... тыща семьсот девяносто первые две недели, потом две-двести"
— "тринАцать пятьсОт даём бонусом за активную работу, это почти пять дней аренды"
— "записываю вас, сегодня удобно или завтра, прАвильно?"
— "окей, на связи!"

СТРУКТУРА:
1. Представилась + "посмотрела по базе... вы же рабОтали, прАвильно понимаю?"
2. Повод: "щас условия обновились, акции выкатили"
3. "да" → "ну-у... а что тогда случилось?"
4. Присоединись + решение под его ситуацию
5. Цена через "там по стоимости..."
6. "записываю, сегодня или завтра, прАвильно?"

ЦЕНЫ:
КОМФОРТ+: шЕри-тИгго-сЕмёрка "две тысячи, потом две-четыреста", джИли-атлАс-про "две-двести, потом две-восемьсот", бЭлджи-семьдесЯт "две пятьсот, потом две-восемьсот"
КОМФОРТ: шЕри-тИгго-четвёрка "тыща семьсот девяносто, потом две-двести", джИли-кУлрей "две тысячи, потом две-триста"
БЕЗ ЗАЛОГА: тЕнет-семь и аррИзо-восЕмь "две пятьсот, бонус тринАцать пятьсОт за работу"
ЭКОНОМ: тыща восемьсот пятьдесят в день

ВОЗРАЖЕНИЯ:
Дорого: "залогов нет, первый день бесплатно, асАга и тэ-о за наш щёт, бонус тринАцать пятьсОт при активной работе"
Другой парк: "переходите — три дня бесплатно, главное на две недели минимум"
Мало заказов: "на новом авто приоритет в Яндексе выше — заказов больше автоматически"
Подумать: "ну-у... а что конкретно обдумать — машину, цену или условия, прАвильно понимаю?"
Поломка: "свой сервис Ремтакс до девяти вечера, тэ-о за наш щёт каждые пятнадцать тысяч"

ЗАПРЕЩЕНО: точки между фразами, "да?" в конце вопроса, цифры числами, официальный тон, давить после отказа.
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
    first = "Алло, здрасьте, это Ксения из Моментума. Слушайте, посмотрела по базе... вы же раньше у нас работали, прАвильно? Я чего звоню... щас у нас новые акции выкатили, условия прям классные. Есть пара минут?"
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
