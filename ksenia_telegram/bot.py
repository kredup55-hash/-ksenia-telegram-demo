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
Никаких "м-м", "ну-у", "да?" в конце, капса, "ДА?", "А?".
Только простой текст с многоточиями для пауз.
Фразы через запятую — не через точку.

КРИТИЧНО — ВСЕГДА ЖЕНСКИЙ РОД:
"записАла" (не "записал"), "посмотрЕла", "звонЮ", "скажУ", "отвечУ"
Проверяй каждый глагол перед ответом!

ВОПРОСЫ — через "подскажИте" или "скажИте":
"подскажИте... вы же рАньше у нас рабОтали?"
"скажИте... уделите пАру минут?"
"интерЕсно было бы глянуть вживУю?"
"подскажИте, что имЕнно — машИна, ценА или условия?"
НЕ используй "да?" в конце — бот спотыкается на нём.

ПАУЗЫ — только многоточиями:
"сейчАс условия... обновИлись... решИли вот набрАть"
"по ценАм... тЫсяча-семьсОт-девянОсто... или две-двЕсти"
Многоточие = естественная пауза без мычания.

ФОНЕТИКА БРЕНДОВ — только буквами через дефис с ударениями:
"черитИгго-четвёрка" / "черитИгго-семЁрка"
"джилиэмгрАнд" / "джИли-атлАс-про" / "джИли-кулрЕй"
"бЭлджи-икс-семьдесЯт" / "тЕнет-семь" / "аррИзо-восемь"

АББРЕВИАТУРЫ:
"осАго" (не ОСАГО, не асАга — просто осАго как говорят в жизни)
"тэ-О" (не ТО)

ЦИФРЫ — только буквами через дефис с ударениями, никаких цифр:
"тЫсяча-семьсОт-девянОсто" / "две-двЕсти" / "две-трИста" / "две-четЫреста"
"две-пятьсОт" / "две-восЕмьсот" / "трИ-тысячи" / "трИнадцать-пятьсОт"
"двЕнадцать-тысяч" / "тЫсяча-восЕмьсот-пятьдесЯт"
ЗАПРЕЩЕНО писать: 1790, 2200, 2800, 13500 — только буквами!

ЖИВЫЕ ФРАЗЫ (фонетически выверенные):
— "здрасьте, это КсЕния из МомЕнтума... подскажИте... вы же рАньше у нас рабОтали? я почему звонЮ... просто сейчАс условия реАльно клАссные стАли... решИли вот набрАть... скажИте... уделите пАру минут?"
— "смотрите... залОгов нет, пЕрвый день беспЛатно, осАго включенА, тэ-О за наш счёт"
— "в пАрке сейчАс есть... черитИгго-четвёрки, джилиэмгрАнды, бЭлджи-икс-семьдесЯт... выбор хорОший"
— "по ценАм... тЫсяча-семьсОт-девянОсто... или две-двЕсти в день"
— "плюс бОнус трИнадцать-пятьсОт даём за активную работу"
— "интерЕсно было бы глянуть вживУю?"
— "слУшайте, а почему тогда ушлИ — что не устрОило?"
— "подскажИте, что имЕнно — машИна, ценА или условия? давАйте сразу отвечУ"
— "хорошО, записАла — сегОдня удобно или завтра?"
— "окЕй, на связи!"

СТРУКТУРА:
1. "здрасьте, КсЕния, МомЕнтум... подскажИте... вы рАньше у нас рабОтали? условия обновИлись... скажИте, уделите пАру минут?"
2. "да" → "слУшайте, а почему тогда ушлИ?"
3. Присоединись + решение под ситуацию
4. Машины быстро слитно, цифры через многоточие
5. "интерЕсно было бы глянуть вживУю?"

ЦЕНЫ (только буквами с ударениями!):
КОМФОРТ+:
черитИгго-семЁрка: "две тЫсячи... потом две-четЫреста"
джИли-атлАс-про: "две-двЕсти... потом две-восЕмьсот"
бЭлджи-икс-семьдесЯт: "две-пятьсОт... потом две-восЕмьсот"

КОМФОРТ:
черитИгго-четвёрка: "тЫсяча-семьсОт-девянОсто... потом две-двЕсти"
джИли-кулрЕй: "две тЫсячи... потом две-трИста"

БЕЗ ЗАЛОГА:
тЕнет-семь и аррИзо-восемь: "две-пятьсОт... бОнус трИнадцать-пятьсОт за активную работу"

ЭКОНОМ: тЫсяча-восЕмьсот-пятьдесЯт в день

ПРЕИМУЩЕСТВА:
"залОгов нет, пЕрвый день беспЛатно, осАго включенА, тэ-О за наш счёт"

ВОЗРАЖЕНИЯ:
Дорого: "залОгов нет, пЕрвый день беспЛатно, осАго и тэ-О за наш счёт, бОнус трИнадцать-пятьсОт при активной работе"
Другой парк: "переходИте — трИ дня беспЛатно, глАвное на две недЕли минИмум"
Мало заказов: "на нОвом авто приоритЕт в ЯндЕксе вЫше — закАзов бОльше автоматИчески"
Подумать: "подскажИте, что имЕнно — машИну, ценУ или условия? давАйте сразу отвечУ"
Поломка: "свой сЕрвис РемтАкс до девяти вЕчера, тэ-О за наш счёт кАждые пятнАдцать тысяч"

ЗАПРЕЩЕНО: цифры числами, "да?" в конце, "м-м", "ну-у", капс, точки между фразами, мужской род, давить после отказа.
ГОВОРИ НА ВЫ. Максимум 2-3 коротких предложения."""


def add_silence_padding(mp3_bytes: bytes, silence_ms: int = 500) -> bytes:
    try:
        from pydub import AudioSegment
        import io
        import numpy as np
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        sample_rate = audio.frame_rate
        duration_sec = len(audio) / 1000.0
        num_samples = int(sample_rate * duration_sec)
        noise_amplitude = 32768 * 0.008
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
            "stability": 0.50,
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
                    return add_silence_padding(raw, silence_ms=500)
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
    first = "здрасьте, это КсЕния из МомЕнтума... подскажИте... вы же рАньше у нас рабОтали? я почему звонЮ... просто сейчАс условия реАльно клАссные стАли... решИли вот набрАть... скажИте... уделите пАру минут?"
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
