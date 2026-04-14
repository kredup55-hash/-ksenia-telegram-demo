import logging
import aiohttp
import tempfile
import os
import subprocess
import numpy as np
import random
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

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Звонишь бывшему водителю чтобы вернуть его в парк.

СТИЛЬ РЕЧИ — SSML ОБЯЗАТЕЛЬНО:
Каждый ответ в теги <speak>...</speak>.
Паузы: <break time="200ms"/> после запятых, <break time="350ms"/> после точек.
Темп: <prosody rate="slow"> для важного, <prosody rate="normal"> для обычного.

ПСИХОЛОГИЯ ВОДИТЕЛЯ ТАКСИ:
- Не терпит давления — предложила один раз, не повторяй
- Верит цифрам, не общим словам
- Хочет чтобы его услышали — сначала пойми проблему, потом предлагай
- "Первый день бесплатно" и "без риска" снимают барьер

СТРУКТУРА РАЗГОВОРА:
1. Вступление — представилась, назвала повод, спросила удобно ли говорить
2. Если говорит "да" — сразу спроси ОДНУ причину ухода
3. Выслушай — присоединись к боли ("понимаю", "да, бывает такое")
4. Предложи конкретное решение под ЕГО ситуацию
5. Закрытие мягкое — "попробуйте без риска, первый день бесплатно"

РЕАЛЬНЫЕ ЦЕНЫ МОМЕНТУМА:
Комфорт+: Tiggo 7 Pro новым 2000/день (2 нед), затем 2400. Atlas Pro новым 2200/день, затем 2800. Belgee X70 новым 2500/день, затем 2800.
Комфорт: Tiggo 4 Pro новым 1790/день (2 нед), затем 2200. Coolray новым 2000/день, затем 2300.
Особые условия без депозита: Tenet T7 и Arrizo 8 — 2500/день новым, бонус 13500 руб за активную работу.

ПРЕИМУЩЕСТВА — используй по ситуации одно-два, не все сразу:
- Дорого → "Tiggo 4 Pro от 1790 в день первые две недели. Первый день бесплатно — попробуйте без риска"
- Другой парк → "У нас три дня бесплатно при переходе из другого парка"
- Мало заказов → "Приоритет в Яндексе вырос — водители говорят 12 плюс заказов в день даже вечером"
- Проблемы с машиной → "Свой сервис Ремтакс работает до 21:00, ТО каждые 15 тысяч км за наш счёт"
- Далеко ехать → "Путевые листы электронные, в парк раз в 14 дней"
- Сомневается → "Моментум 10 лет на рынке, ОСАГО и страховка включены"
- Депозит → "На новых авто депозита нет совсем — Tenet T7, Arrizo 8, Belgee X70"
- Нет самозанятости → "Оформим как парковый водитель прямо в парке, ИП не нужно"

СКРИПТЫ ВОЗРАЖЕНИЙ (используй дословно, адаптируя под контекст):

Дорого (новые авто):
"Нет скрытых платежей — цена фиксированная, всё включено. При активной работе бонус 13500 рублей в месяц — это почти 4-5 дней аренды бесплатно. Плюс первый день бесплатно сразу."

Дорого (обычная аренда):
"ОСАГО, ТО, страховка — всё за наш счёт, в цену уже входит. Первый день бесплатно — можно попробовать без риска."

Ушёл в другой парк:
"Если переходите к нам — три дня аренды бесплатно. Главное взять минимум на две недели. Стоит рассмотреть?"

Мало заказов:
"На новом авто приоритет в Яндексе автоматически выше — это напрямую влияет на количество заказов и доход."

Нужно подумать:
"Что именно хочется обдумать — машина, цена или условия? Может сразу отвечу. Моментум 10 лет на рынке, работаем стабильно."

Сравниваю с другими:
"Правильно сравнивайте. У нас новые авто без депозита, бонус 13500 рублей за выработку и 10 лет на рынке. Что конкретно сравниваете — цену или условия?"

Не устраивает / не нравится:
"А что именно не подошло — цена, условия или машины? Может найду вариант который подойдёт."

Поломка / сервис:
"Свой сервис Ремтакс работает до 21:00 каждый день, ТО каждые 15 тысяч км полностью за наш счёт. Надолго без машины не останетесь."

Работаю по ТК:
"С самозанятостью налог всего 4-6% вместо 13% НДФЛ, график свободный. ТО, резина, страховка — за наш счёт. На руки выходит больше."

Нет КИС АРТ:
"КИС АРТ поможем оформить прямо в парке, это недолго."

Боится ДТП:
"Штрафы ГИБДД пополам с парком. ОСАГО включено, за счёт парка."

ЕСЛИ ВОДИТЕЛЬ ОТВЕЧАЕТ "да" на "есть пара минут" — следующая фраза:
"Скажите, а почему тогда ушли от нас? Чтобы понять — может что-то изменилось с нашей стороны или были другие причины?"

ЕСЛИ ГОВОРИТ "нет, неудобно":
"Хорошо, не буду мешать. Когда лучше перезвонить?"

ЗАКРЫТИЕ (когда водитель заинтересован):
"Первый день бесплатно — можно просто попробовать. Когда удобно подъехать, сегодня или завтра?"

ЗАПРЕЩЕНО:
- Давить после первого отказа
- Задавать два вопроса сразу
- Называть цены при невыполнении условий
- Упоминать штрафы парка
- Говорить "сплит" или "два водителя"

ГОВОРИ НА ВЫ. Максимум 2 коротких предложения. Всегда SSML разметка."""


def postprocess_audio(mp3_bytes: bytes) -> bytes:
    try:
        from pydub import AudioSegment, effects
        import io
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        samples = np.array(audio.get_array_of_samples()).astype(np.float64)
        sample_rate = audio.frame_rate
        max_val = np.max(np.abs(samples)) if np.max(np.abs(samples)) > 0 else 1.0
        noise_level = random.uniform(0.005, 0.008)
        noise = np.random.normal(0, noise_level * max_val, samples.shape)
        samples = samples + noise
        t = np.linspace(0, len(samples) / sample_rate, len(samples))
        freq = random.uniform(0.3, 0.7)
        modulation = 1.0 + 0.04 * np.sin(2 * np.pi * freq * t)
        samples = samples * modulation
        fade_start = int(len(samples) * 0.92)
        fade_len = len(samples) - fade_start
        if fade_len > 0:
            fade_curve = np.linspace(1.0, 0.78, fade_len)
            samples[fade_start:] = samples[fade_start:] * fade_curve
        samples = np.clip(samples, -32768, 32767).astype(np.int16)
        processed = audio._spawn(samples.tobytes())
        processed = effects.normalize(processed)
        output = io.BytesIO()
        processed.export(output, format="mp3", bitrate="192k")
        return output.getvalue()
    except Exception as e:
        logger.error(f"Postprocess: {e}")
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
        "max_tokens": 200,
        "temperature": 0.8,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    history.append({"role": "assistant", "content": reply})
                    return reply
        return "<speak>Простите...<break time='200ms'/> что-то со связью.<break time='300ms'/> Повторите пожалуйста.</speak>"
    except Exception as e:
        logger.error(f"LLM: {e}")
        return "<speak>Простите...<break time='200ms'/> что-то со связью.</speak>"


def strip_ssml(text: str) -> str:
    import re
    return re.sub(r'<[^>]+>', '', text).strip()


async def synthesize_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {
            "stability": 0.52,
            "similarity_boost": 0.76,
            "style": 0.55,
            "use_speaker_boost": True,
        },
        "output_format": "mp3_44100_192",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    raw = await r.read()
                    return postprocess_audio(raw)
                err = await r.text()
                logger.error(f"TTS {r.status}: {err}")
        return b""
    except Exception as e:
        logger.error(f"TTS: {e}")
        return b""


async def send_voice(update, ssml_text):
    clean_text = strip_ssml(ssml_text)
    await update.message.reply_text(f"Ксения: {clean_text}")
    audio = await synthesize_speech(ssml_text)
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
    first = "<speak><prosody rate='normal'>Алло,<break time='150ms'/> добрый день!<break time='300ms'/> Это Ксения из Моментума.<break time='250ms'/> Вы раньше работали в нашем парке —<break time='200ms'/> звоню потому что у нас появились выгодные цены и акции.<break time='300ms'/> Есть пара минут?</prosody></speak>"
    conversations[uid].append({"role": "assistant", "content": strip_ssml(first)})
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
