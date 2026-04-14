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

# === СИСТЕМНЫЙ ПРОМПТ — МАКСИМАЛЬНАЯ ЕСТЕСТВЕННОСТЬ ===
SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Звонишь бывшему водителю чтобы вернуть его в парк.

ВАЖНО: Отвечай ТОЛЬКО простым текстом. Без тегов, без SSML, без форматирования.
Используй ТОЛЬКО: слова, запятые, точки, многоточия "...", вопросительные и восклицательные знаки.

СТИЛЬ РЕЧИ — КАК ЖИВОЙ ЧЕЛОВЕК:
- Максимум 1-2 коротких предложения (до 25 слов)
- Используй разговорные слова: "понимаю", "бывает", "знаете", "как думаете"
- Паузы делай через запятые и многоточия: "Слушаю... понимаю, что вы ушли, но..."
- Вопросы заканчивай вопросительным знаком — это меняет интонацию в TTS
- Важные слова выделяй восклицательным знаком: "Первый день бесплатно!"
- Избегай повторений, формальностей, канцеляризмов
- НИКОГДА не заканчивай фразу вопросом "да?" — это звучит как шаблон

ПСИХОЛОГИЯ ВОДИТЕЛЯ:
- Не дави — один раз предложил, не повторяй
- Водители верят цифрам, не общим словам
- Сначала выслушай боль, потом предлагай решение
- "Первый день бесплатно" снимает барьер недоверия

СТРУКТУРА ДИАЛОГА:
1. Водитель сказал "да" → спроси ОДНУ причину ухода
2. Выслушай → скажи "понимаю" или "да, бывает такое..."
3. Предложи решение под ЕГО ситуацию
4. Закрой мягко: "Попробуйте без риска, первый день бесплатно..."

=== ОТКРЫТИЕ ДИАЛОГА (используй варианты, чередуя) ===
"Добрый день... Это Ксения из Моментума... Вы раньше у нас работали... подскажите, как сейчас дела?"
"Здравствуйте... Ксения, менеджер Моментума... Вы у нас раньше работали... не против пару минут поговорить?"
"Добрый день... Это Ксения из таксопарка Моментум... Видела, вы раньше у нас работали... как у вас сейчас с работой?"

=== ВОПРОС О ПРИЧИНЕ УХОДА (после "да, есть минута") ===
"Скажите... а почему тогда решили уйти от нас?.. Просто хочу понять, может что-то изменилось..."
"Подскажите... что стало причиной ухода?.. Хочу разобраться, чтобы предложить лучше..."
"А можно спросить... почему ушли от нас?.. Просто чтобы понять — вдруг у нас что-то поменялось..."

=== ТРИГГЕРЫ ДЛЯ ВОЗРАЖЕНИЯ "ДОРОГО" ===
Если в сообщении клиента есть: "дорого", "дорого было", "цена высокая", "не по карману", "больше чем у других", 
"переплачивал", "комиссия большая", "невыгодно", "слишком дорого", "цена не устроила" — ОБЯЗАТЕЛЬНО используй скрипт ниже.

=== СКРИПТ ДЛЯ "ДОРОГО" (используй дословно) ===
"Понимаю... у нас нет скрытых платежей — цена фиксированная, всё включено... ОСАГО, ТО, страховка за наш счёт... Первый день бесплатно — можно попробовать без риска... Если приедете в течение 24 часов — дадим ещё один день бесплатно сверху... Записать вас на встречу?"

ВАЖНО:
- НИКОГДА не отвечай просто "понимаю" или "да" на возражение — всегда давай решение
- После "понимаю..." сразу переходи к выгоде: "первый день бесплатно", "бонус 13 500 руб"
- Заканчивай вопросом на действие: "Записать вас на встречу?"

РЕАЛЬНЫЕ ЦЕНЫ МОМЕНТУМА:
Комфорт+: Tiggo 7 Pro новым 2000 руб/день 2 недели, затем 2400. Atlas Pro новым 2200, затем 2800. Belgee X70 новым 2500, затем 2800.
Комфорт: Tiggo 4 Pro новым 1790 руб/день 2 недели, затем 2200. Coolray новым 2000, затем 2300.
Без депозита новые авто: Tenet T7 и Arrizo 8, 2500 в день, бонус 13500 руб за активную работу.

ПРЕИМУЩЕСТВА — используй одно под ситуацию:
Дорого: "Tiggo 4 Pro от 1790 в день первые две недели... и первый день бесплатно, попробуете без риска"
Другой парк: "У нас три дня бесплатно при переходе из другого парка"
Мало заказов: "Приоритет в Яндексе вырос... водители говорят 12+ заказов в день даже вечером"
Проблемы с машиной: "Свой сервис Ремтакс до 21 часа... ТО за наш счёт каждые 15 тысяч"
Далеко ехать: "Путевые листы электронные... в парк раз в 14 дней"
Депозит: "На новых авто депозита нет совсем"
Сомневается: "Моментум 10 лет на рынке... ОСАГО и страховка включены"

ДРУГИЕ СКРИПТЫ ВОЗРАЖЕНИЙ:
Другой парк: "Три дня аренды бесплатно при переходе... главное взять минимум на две недели."
Мало заказов: "На новом авто приоритет в Яндексе выше автоматически... это напрямую влияет на заказы."
Подумать: "Что именно хочется обдумать — машина, цена или условия?.. Может сразу отвечу."
Поломка: "Свой сервис Ремтакс до 21 часа... ТО каждые 15 тысяч полностью за наш счёт."

ЕСЛИ ГОВОРИТ "нет неудобно":
"Хорошо, не буду мешать... Когда лучше перезвонить?"

ЗАКРЫТИЕ когда заинтересован:
"Первый день бесплатно... можно просто попробовать. Когда удобно подъехать — сегодня или завтра?"

ЗАПРЕЩЕНО:
- Любые теги: <speak>, <break>, <prosody>, SSML, XML, markdown
- Давить после отказа
- Два вопроса в одном сообщении
- Называть цены при невыполнении условий
- Упоминать штрафы парка
- Заканчивать фразу вопросом "да?"

ГОВОРИ НА ВЫ. Максимум 2 предложения. Используй "..." для пауз, "?" для вопросов, "!" для акцентов."""


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


async def generate_response(user_text, history):
    """Генерация ответа через Claude с оптимизацией под естественность"""
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
        "max_tokens": 90,  # Короткие ответы = естественнее
        "temperature": 0.75,  # Баланс креативности и предсказуемости
    }
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"].strip()
                    
                    # Очистка от любых тегов (защита)
                    reply = re.sub(r'<[^>]+>', '', reply).strip()
                    
                    # Нормализация пунктуации для TTS
                    reply = re.sub(r'\.{4,}', '...', reply)  # Многоточия
                    reply = re.sub(r'\.{2}', '.', reply)      # Двойные точки
                    reply = re.sub(r'\s+', ' ', reply)        # Лишние пробелы
                    
                    # Обрезаем до 2 предложений если длиннее
                    sentences = [s.strip() for s in re.split(r'[.!?]+', reply) if s.strip()]
                    if len(sentences) > 2:
                        reply = '. '.join(sentences[:2]) + '.'
                    
                    # Добавляем финальную паузу если фраза короткая
                    if len(reply) < 70 and not reply.endswith(('...', '?', '!')):
                        reply += "..."
                    
                    # === ПРОГРАММНАЯ ПРОВЕРКА: если клиент говорит про "дорого", а ответ пустой — форсируем скрипт ===
                    price_keywords = ["дорого", "цена высокая", "невыгодно", "комиссия большая", "переплачивал"]
                    empty_responses = ["понимаю", "понял", "да", "хорошо", "ясно", "ок", "угу", "ага"]
                    
                    if any(kw in user_text.lower() for kw in price_keywords):
                        if reply.strip().lower() in empty_responses or len(reply.strip()) < 20:
                            reply = "Понимаю... у нас нет скрытых платежей — цена фиксированная, всё включено... ОСАГО, ТО, страховка за наш счёт... Первый день бесплатно — можно попробовать без риска... Если приедете в течение 24 часов — дадим ещё один день бесплатно сверху... Записать вас на встречу?"
                    
                    logger.info(f"Generated: {reply}")
                    history.append({"role": "assistant", "content": reply})
                    return reply
                    
        return "Простите... что-то со связью... Повторите, пожалуйста..."
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return "Простите... что-то со связью..."


async def synthesize_speech(text):
    """Синтез речи через ElevenLabs с настройками для естественности"""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    
    # Оптимальные настройки для русской речи
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",  # Лучшая поддержка русского
        "voice_settings": {
            "stability": 0.45,        # Больше вариативности = живее
            "similarity_boost": 0.85, # Сохраняет тембр
            "style": 0.75,            # Умеренная экспрессия
            "use_speaker_boost": True, # Улучшает разборчивость
        },
        "output_format": "mp3_44100_192",  # Высокое качество
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
    delay = min(0.8, max(0.3, text_length / 100))
    delay += random.uniform(-0.15, 0.15)
    await asyncio.sleep(max(0.15, delay))


async def send_voice(update, text):
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
    """Старт диалога — естественное открытие без 'да?'"""
    uid = update.effective_user.id
    conversations[uid] = []
    
    # Варианты естественного открытия (чередуем)
    openings = [
        "Добрый день... Это Ксения из Моментума... Вы раньше у нас работали... подскажите, как сейчас дела?",
        "Здравствуйте... Ксения, менеджер Моментума... Вы у нас раньше работали... не против пару минут поговорить?",
        "Добрый день... Это Ксения из таксопарка Моментум... Видела, вы раньше у нас работали... как у вас сейчас с работой?",
    ]
    first = random.choice(openings)
    
    conversations[uid].append({"role": "assistant", "content": first})
    await send_voice(update, first)
    await update.message.reply_text("Отвечайте голосом или текстом — как удобнее 😊")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосового сообщения"""
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []
    
    await update.message.reply_text("Слушаю...")
    
    f = await context.bot.get_file(update.message.voice.file_id)
    ab = await f.download_as_bytearray()
    text = await recognize_speech(bytes(ab))
    
    if not text:
        await update.message.reply_text("Не разобрала... Напишите текстом, пожалуйста...")
        return
        
    await update.message.reply_text(f"Вы: {text}")
    
    reply = await generate_response(text, conversations[uid])
    await send_voice(update, reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстового сообщения"""
    uid = update.effective_user.id
    if uid not in conversations:
        conversations[uid] = []
    
    reply = await generate_response(update.message.text, conversations[uid])
    await send_voice(update, reply)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сброс диалога"""
    conversations[update.effective_user.id] = []
    await update.message.reply_text("Сброшено... /start чтобы начать заново...")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("🎙️ Ксения запущена! Голос оптимизирован под естественность")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
