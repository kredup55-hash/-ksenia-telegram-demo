import logging
import aiohttp
import tempfile
import os
import asyncio
import re
import random
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()

conversations = {}

# === СИСТЕМНЫЙ ПРОМПТ — МАКСИМАЛЬНАЯ ЕСТЕСТВЕННОСТЬ И ЛОГИКА ===
SYSTEM_PROMPT = """Ты Ксения, живой менеджер таксопарка «Моментум». Общаешься голосом по телефону. Твоя задача — вести естественный человеческий разговор, а не читать скрипт.

ГЛАВНЫЕ ПРАВИЛА РЕЧИ:
1. Говори короткими фразами. Живые люди не произносят сложные предложения без остановки.
2. Используй «...» для естественных пауз и раздумий. Используй «—» для резких акцентов или уточнений.
3. Избегай идеальной пунктуации. Заменяй точки на «...» где есть смысловая пауза. Не ставь запятые там, где человек бы просто сделал вдох.
4. Делай ударения через порядок слов и контекст. Например: «У нас же первый день вообще бесплатно» (ударение падает на «бесплатно»).
5. Добавляй лёгкие разговорные связки: «честно говоря», «понимаете», «ну», «смотрите», «на самом деле». Не перебарщивай — 1-2 на ответ.
6. Максимум 2-3 коротких предложения. Не вываливай информацию. Веди диалог: сказал фразу — спросил, как отклик.

ЛОГИКА ОТВЕТА (СТРОГО СОБЛЮДАЙ ПОРЯДОК):
1. Сначала присоединись к эмоции или проблеме клиента. («Понимаю, почему это смутило...», «Да, бывает такое, честно...», «Слышу вас...»)
2. Дай 1-2 предложения пояснения или решения. Не дави.
3. Только если клиент открыт или вопрос исчерпан — мягко предложи следующий шаг. («Может, просто подъедете посмотреть?..», «Как вам такая идея?..»)
4. НИКОГДА не перескакивай на «когда приедете» если клиент ещё не согласился, жалуется или задаёт уточняющий вопрос. Это звучит неестественно и раздражает.

РАБОТА С ВОЗРАЖЕНИЯМИ:
— Если клиент говорит «дорого» / «не устроило» / «подумаю» / «ушёл в другой парк»:
  Сначала признай его позицию → дай 1 конкретный факт → мягко предложи попробовать без риска.
  Пример: «Понимаю... цена кажется высокой, но там же всё включено — страховка, ТО, ремонт. По факту вы не думаете о расходах... Первый день вообще бесплатно, можно просто посидеть за рулём, оценить. Как вам?..»

— Если клиент спрашивает про условия / цены / машины:
  Дай чёткий ответ → сразу спроси, что важнее для него.
  Пример: «Комфорт у нас от 1790 в день первые две недели... Что для вас важнее — цена или свежая машина?..»

РЕАЛЬНЫЕ ФАКТЫ (ИСПОЛЬЗУЙ ТОЛЬКО ИХ):
Комфорт+: Tiggo 7 Pro новым 2000 руб/день 2 недели, затем 2400. Atlas Pro новым 2200, затем 2800. Belgee X70 новым 2500, затем 2800.
Комфорт: Tiggo 4 Pro новым 1790 руб/день 2 недели, затем 2200. Coolray новым 2000, затем 2300.
Без депозита новые авто: Tenet T7 и Arrizo 8, 2500 в день, бонус 13500 руб за активную работу.
Первый день бесплатно на всех машинах. Три дня бесплатно при переходе из другого парка. Свой сервис Ремтакс до 21:00. Путевые электронные.

ЗАПРЕЩЕНО:
- Идеальные списки, точки с запятой, двоеточия, канцеляризмы.
- Резкие переходы к продаже без отработки контекста.
- Игнорировать последнее сообщение клиента.
- Повторять одну и ту же фразу дважды подряд.
- Заканчивать фразу вопросом «да?» — это шаблон. Используй «как вам?..», «что думаете?..», «подскажите?..»

ГОВОРИ НА ВЫ. Используй «...» для пауз, «—» для акцентов, «?» для вопросов. Будь живой, тёплой и уверенной."""


async def recognize_speech(audio_bytes):
    """Распознавание речи через Yandex STT"""
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
                    return j.get("result", "").strip()
        return ""
    except Exception as e:
        logger.error(f"STT error: {e}")
        return ""


async def generate_response(user_text, history, uid):
    """Генерация ответа через Claude с оптимизацией под естественность и логику"""
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
        "max_tokens": 80,  # Короткие ответы = естественнее
        "temperature": 0.85,  # Больше креативности для живой речи
    }
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"].strip()
                    
                    # === ОЧИСТКА И НОРМАЛИЗАЦИЯ ДЛЯ TTS ===
                    # Убираем теги
                    reply = re.sub(r'<[^>]+>', '', reply).strip()
                    # Убираем капс ( ElevenLabs его игнорирует или кричит)
                    reply = re.sub(r'\b[A-ZА-ЯЁ]{2,}\b', lambda m: m.group(0).lower(), reply)
                    # Нормализуем многоточия
                    reply = re.sub(r'\.{4,}', '...', reply)
                    reply = re.sub(r'\.{2}', '...', reply)
                    # Убираем лишние пробелы
                    reply = re.sub(r'\s+', ' ', reply)
                    
                    # === ЛОГИЧЕСКАЯ ПРОВЕРКА: НЕ ПРЫГАТЬ К ПРОДАЖЕ ===
                    objection_keywords = ["дорого", "не устроило", "подумаю", "не нравится", "ушёл", "перешёл", "мало заказов", "сломалась", "далеко"]
                    is_objection = any(kw in user_text.lower() for kw in objection_keywords)
                    
                    cta_keywords = ["встреч", "подъехать", "записать", "приехать", "сегодня или завтра", "когда удобно"]
                    has_cta = any(ck in reply.lower() for ck in cta_keywords)
                    
                    # Если клиент жалуется/возражает, а бот сразу зовёт на встречу — убираем CTA и добавляем мостик
                    if is_objection and has_cta and len(reply) < 120:
                        reply = re.sub(r'[.!?]?[^.!?]*\b(встреч|подъехать|записать|приехать|сегодня или завтра)[^.!?]*[.!?]?', '', reply, flags=re.IGNORECASE)
                        reply = reply.strip().rstrip('.,!? ')
                        if reply:
                            reply += "... Может, просто подъедете посмотреть?.. Как вам идея?..."
                        else:
                            reply = "Понимаю... Давайте просто попробуем без риска — первый день бесплатно. Как вам?.."
                    
                    # Обрезаем до 2 предложений если слишком длинно
                    sentences = [s.strip() for s in re.split(r'[.!?]+', reply) if s.strip()]
                    if len(sentences) > 2:
                        reply = '. '.join(sentences[:2]) + '...'
                    
                    # Добавляем финальную паузу если фраза короткая и не вопрос
                    if len(reply) < 60 and not reply.endswith(('...', '?', '!')):
                        reply += "..."
                    
                    logger.info(f"Generated: {reply}")
                    history.append({"role": "assistant", "content": reply})
                    return reply
                    
        return "Простите... что-то со связью... Повторите, пожалуйста..."
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return "Простите... что-то со связью..."


async def synthesize_speech(text):
    """Синтез речи через ElevenLabs с настройками для максимальной естественности"""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    
    # === НАСТРОЙКИ ГОЛОСА: МАКСИМАЛЬНАЯ ЖИВОСТЬ ===
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.35,        # Низкая стабильность = живая интонация, естественные колебания
            "similarity_boost": 0.85, # Сохраняет тембр, но не делает его плоским
            "style": 0.85,            # Высокая экспрессия = эмоциональная окраска
            "use_speaker_boost": True, # Улучшает разборчивость и глубину
        },
        "output_format": "mp3_44100_192",
    }
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    return await r.read()
                err = await r.text()
                logger.error(f"TTS error {r.status}: {err}")
        return b""
    except Exception as e:
        logger.error(f"TTS exception: {e}")
        return b""


async def human_like_delay(text_length):
    """Микро-задержка имитирующая 'мышление' перед ответом"""
    delay = min(0.9, max(0.3, text_length / 90))
    delay += random.uniform(-0.15, 0.15)
    await asyncio.sleep(max(0.15, delay))


async def send_voice(update, text, uid):
    """Отправка голосового сообщения с имитацией живого ответа"""
    await update.message.reply_chat_action("typing")
    await human_like_delay(len(text))
    
    audio = await synthesize_speech(text)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        with open(tmp, "rb") as af:
            await update.message.reply_audio(af, title="Ксения")
        os.unlink(tmp)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт диалога — естественное открытие"""
    uid = str(update.effective_user.id)
    conversations[uid] = {"history": [], "objections_handled": [], "closing_attempted": False}
    
    openings = [
        "Добрый день... Это Ксения из Моментума... Вы раньше у нас работали... подскажите, как сейчас дела?..",
        "Здравствуйте... Ксения, менеджер Моментума... Вы у нас раньше работали... не против пару минут поговорить?..",
        "Добрый день... Это Ксения из таксопарка Моментум... Видела, вы раньше у нас работали... как у вас сейчас с работой?.."
    ]
    first = random.choice(openings)
    
    conversations[uid]["history"].append({"role": "assistant", "content": first})
    await send_voice(update, first, uid)
    await update.message.reply_text("Отвечайте голосом или текстом — как удобнее 😊")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосового сообщения"""
    uid = str(update.effective_user.id)
    if uid not in conversations:
        conversations[uid] = {"history": [], "objections_handled": [], "closing_attempted": False}
    
    await update.message.reply_text("Слушаю...")
    
    f = await context.bot.get_file(update.message.voice.file_id)
    ab = await f.download_as_bytearray()
    text = await recognize_speech(bytes(ab))
    
    if not text:
        await update.message.reply_text("Не разобрала... Напишите текстом, пожалуйста...")
        return
        
    await update.message.reply_text(f"Вы: {text}")
    
    reply = await generate_response(text, conversations[uid]["history"], uid)
    await send_voice(update, reply, uid)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстового сообщения"""
    uid = str(update.effective_user.id)
    if uid not in conversations:
        conversations[uid] = {"history": [], "objections_handled": [], "closing_attempted": False}
    
    reply = await generate_response(update.message.text, conversations[uid]["history"], uid)
    await send_voice(update, reply, uid)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сброс диалога"""
    uid = str(update.effective_user.id)
    conversations[uid] = {"history": [], "objections_handled": [], "closing_attempted": False}
    await update.message.reply_text("Сброшено... /start чтобы начать заново...")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("🎙️ Ксения запущена! Голос оптимизирован под живого человека")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
