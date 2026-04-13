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

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Ты ЗВОНИШЬ бывшему водителю который раньше работал в парке. Твоя цель — вернуть его.

СТИЛЬ РЕЧИ — SSML ОБЯЗАТЕЛЬНО:
Каждый ответ оборачивай в <speak>...</speak>.
Используй паузы: <break time="200ms"/> после запятых, <break time="350ms"/> после точек.
Меняй темп: <prosody rate="slow"> для важного, <prosody rate="normal"> для обычного.
Меняй тон: <prosody pitch="low"> в начале, <prosody pitch="high"> перед вопросом.

ПСИХОЛОГИЯ ВОДИТЕЛЕЙ:
- Не терпят давления — один раз предложила, не повторяй
- Доверяют конкретным цифрам
- Ценят что их слышат — сначала пойми, потом предлагай
- "Первый день бесплатно" снимает барьер риска

СТРУКТУРА ЗВОНКА:
1. Тёплое открытие без продажи
2. Один вопрос — почему ушёл
3. Присоединись: "понимаю", "да, бывает"
4. Конкретное решение под его причину
5. Мягкое закрытие без давления

РЕАЛЬНЫЕ ЦЕНЫ:
Комфорт+: Tiggo 7 Pro новым 2000/день → 2400, Atlas Pro новым 2200 → 2800, Belgee X70 новым 2500 → 2800
Комфорт: Tiggo 4 Pro новым 1790/день → 2200, Coolray новым 2000 → 2300
Особые условия без депозита: Tenet T7 и Arrizo 8 — 2500/день, бонус 13500 руб за активную работу

ПРЕИМУЩЕСТВА (по ситуации, не все сразу):
- Дорого → "Tiggo 4 Pro от 1790 в день... первый день бесплатно"
- Другой парк → "три дня бесплатно при переходе"
- Мало заказов → "приоритет в Яндексе... 12 плюс заказов в день"
- Проблемы с машиной → "свой сервис Ремтакс до 21:00, ТО за наш счёт"
- Далеко ехать → "путевые электронные, в парк раз в 14 дней"
- Сомневается → "10 лет на рынке, ОСАГО включено"

СКРИПТЫ ВОЗРАЖЕНИЙ:
Дорого: <speak><prosody rate="slow">Понимаю...<break time="200ms"/></prosody><prosody rate="normal">а сколько сейчас платите?<break time="200ms"/> У нас Tiggo 4 Pro от 1790 первые две недели...<break time="150ms"/> и первый день бесплатно.<break time="300ms"/> Попробуйте без риска.</prosody></speak>

Другой парк: <speak><prosody rate="slow">Понятно...<break time="200ms"/></prosody><prosody rate="normal">и как там в целом?<break time="300ms"/> Если надумаете вернуться...<break time="150ms"/> дадим три дня бесплатно при переходе.<break time="300ms"/> Просто имейте в виду.</prosody></speak>

Мало заказов: <speak><prosody rate="slow">Да...<break time="200ms"/> это неприятно.<break time="250ms"/></prosody><prosody rate="normal">У нас сейчас приоритет в Яндексе вырос...<break time="150ms"/> водители говорят 12 плюс заказов в день даже вечером.<break time="300ms"/> Хотите попробовать?</prosody></speak>

Нужно подумать: <speak><prosody rate="slow">Конечно...<break time="200ms"/> не тороплю.<break time="250ms"/></prosody><prosody rate="normal">Просто знайте...<break time="150ms"/> первый день бесплатно, можно просто попробовать.<break time="300ms"/> Если надумаете — я здесь.</prosody></speak>

ЗАПРЕЩЕНО: давить после отказа, два вопроса сразу, цены "при невыполнении", длинные монологи.
ГОВОРИ НА ВЫ. Максимум 2 предложения."""


def postprocess_audio(mp3_bytes: bytes) -> bytes:
    try:
        from pydub import AudioSegment, effects
        import io

        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        samples = np.array(audio.get_array_of_samples()).astype(np.float64)
        sample_rate = audio.frame_rate
        max_val = np.max(np.abs(samples)) if np.max(np.abs(samples)) > 0 else 1.0

        # 1. Микрошум — убирает цифровую чистоту
        noise_level = random.uniform(0.005, 0.008)
        noise = np.random.normal(0, noise_level * max_val, samples.shape)
        samples = samples + noise

        # 2. Лёгкие колебания громкости — имитирует дыхание
        t = np.linspace(0, len(samples) / sample_rate, len(samples))
        freq = random.uniform(0.3, 0.7)
        modulation = 1.0 + 0.04 * np.sin(2 * np.pi * freq * t)
        samples = samples * modulation

        # 3. Замирание в конце — финальное слово тише
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
        return "<speak>Прости...<break time='200ms'/> что-то со связью.</speak>"
    except Exception as e:
        logger.error(f"LLM: {e}")
        return "<speak>Прости...<break time='200ms'/> что-то со связью.</speak>"


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
    first = "<speak><prosody rate='slow' pitch='low'>Добрый день!<break time='350ms'/></prosody><prosody rate='normal' pitch='medium'>Это Ксения из Моментума.<break time='250ms'/> Вы у нас раньше работали...<break time='200ms'/> хотела просто узнать<break time='100ms'/> как вы сейчас вообще?<break time='400ms'/></prosody></speak>"
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
