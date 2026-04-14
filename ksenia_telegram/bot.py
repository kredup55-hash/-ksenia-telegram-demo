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

# === СИСТЕМНЫЙ ПРОМПТ — ЦЕЛЬ: ЗАКРЫТЬ НА ВСТРЕЧУ ===
SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Твоя ЕДИНСТВЕННАЯ ЦЕЛЬ — записать водителя на встречу.

ПРАВИЛО #1: После ЛЮБОГО ответа задавай вопрос который двигает к встрече. Никогда не заканчивай фразу просто информацией.
ПРАВИЛО #2: Если возражение уже отработано — НЕ повторяй тот же скрипт. Переходи к следующему шагу: "Что ещё смущает? Давайте запишемся на встречу — первый день бесплатно."
ПРАВИЛО #3: Максимум 2 попытки отработать возражения. После этого — мягкий переход к закрытию: "Давайте просто попробуем — первый день бесплатно, если не понравится — сдадите."
ПРАВИЛО #4: НИКОГДА не заканчивай фразу вопросом "да?" — это звучит как шаблон. Используй "подскажите...", "как сейчас...", "не против поговорить?".

СТИЛЬ: Коротко, 1-2 предложения. Используй "..." для пауз, "?" для вопросов, "!" для акцентов. Без тегов, без маркдауна.

=== ОТКРЫТИЕ (используй один вариант, чередуя) ===
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

=== СКРИПТЫ ВОЗРАЖЕНИЙ (используй ТОЛЬКО если это возражение ещё не отработано) ===
Дорого: "Понимаю... у нас нет скрытых платежей — цена фиксированная, всё включено... ОСАГО, ТО, страховка за наш счёт... Первый день бесплатно — можно попробовать без риска... Записать вас на встречу?"
Другой парк: "Три дня аренды бесплатно при переходе... главное взять минимум на две недели... Когда удобно подъехать посмотреть?"
Мало заказов: "На новом авто приоритет в Яндексе выше автоматически... это напрямую влияет на заказы... Хотите попробовать без риска?"
Подумать: "Что именно хочется обдумать — машина, цена или условия?.. Может сразу отвечу... Или давайте просто попробуем — первый день бесплатно?"
Поломка: "Свой сервис Ремтакс до 21 часа... ТО каждые 15 тысяч полностью за наш счёт... Не переживайте за технику."

=== ЗАКРЫТИЕ НА ВСТРЕЧУ (используй после 1-2 отработанных возражений) ===
"Первый день бесплатно... можно просто попробовать. Когда удобно подъехать — сегодня или завтра?"
"Давайте запишу вас на встречу — возьмёте машину на день, посмотрите. Если не понравится — сдадите. Когда удобно — сегодня или завтра?"

=== КРИТИЧЕСКИ ВАЖНО ===
- НИКОГДА не повторяй один и тот же скрипт возражения дважды
- После ответа на возражение СРАЗУ задавай вопрос на действие
- Если водитель уходит от ответа — мягко возвращай к цели: "Что скажете насчёт встречи?"
- Максимум 3 сообщения без призыва к действию — потом ОБЯЗАТЕЛЬНО закрывай
- Используй альтернативный вопрос: "сегодня или завтра?", а не "когда удобно?"

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

ЕСЛИ ГОВОРИТ "нет неудобно":
"Хорошо, не буду мешать... Когда лучше перезвонить?"

ЗАПРЕЩЕНО:
- Любые теги: <speak>, <break>, <prosody>, SSML, XML, markdown
- Давить после отказа
- Два вопроса в одном сообщении
- Называть цены при невыполнении условий
- Упоминать штрафы парка
- Заканчивать фразу вопросом "да?"

ГОВОРИ НА ВЫ. Максимум 2 предложения."""


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
    """Генерация ответа через Claude с оптимизацией под естественность и цель"""
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
        "max_tokens": 90,
        "temperature": 0.75,
    }
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"].strip()
                    
                    # Очистка от тегов
                    reply = re.sub(r'<[^>]+>', '', reply).strip()
                    
                    # Нормализация пунктуации
                    reply = re.sub(r'\.{4,}', '...', reply)
                    reply = re.sub(r'\.{2}', '.', reply)
                    reply = re.sub(r'\s+', ' ', reply)
                    
                    # Обрезаем до 2 предложений
                    sentences = [s.strip() for s in re.split(r'[.!?]+', reply) if s.strip()]
                    if len(sentences) > 2:
                        reply = '. '.join(sentences[:2]) + '.'
                    
                    # === КОНТЕКСТНАЯ ПРОВЕРКА: отслеживание возражений ===
                    objections_map = {
                        "дорого": ["дорого", "цена высокая", "невыгодно", "комиссия большая", "переплачивал", "дорого было"],
                        "другой парк": ["другой парк", "перешёл", "работаю в другом", "ушёл в другой"],
                        "мало заказов": ["мало заказов", "нет заказов", "простой", "заказов мало"],
                        "подумать": ["подумать", "не сейчас", "позже", "завтра", "надо подумать"],
                        "поломка": ["поломка", "сервис", "машина сломалась", "ремонт"],
                    }
                    
                    # Определяем текущее возражение
                    current_objection = None
                    for key, keywords in objections_map.items():
                        if any(kw in user_text.lower() for kw in keywords):
                            current_objection = key
                            break
                    
                    # Получаем состояние диалога
                    state = conversations.get(uid, {})
                    
                    # Если это возражение уже отработано — форсируем переход к закрытию
                    if current_objection and current_objection in state.get("objections_handled", []):
                        reply = "Что ещё смущает?... Давайте просто попробуем — первый день бесплатно, если не понравится — сдадите. Когда удобно подъехать — сегодня или завтра?..."
                    
                    # Если ответ содержит призыв к встрече — помечаем
                    if any(ck in reply.lower() for ck in ["встреч", "подъехать", "записать", "приехать", "номер"]):
                        state["closing_attempted"] = True
                    
                    # Добавляем текущее возражение в отработанные
                    if current_objection and current_objection not in state.get("objections_handled", []):
                        if "objections_handled" not in state:
                            state["objections_handled"] = []
                        state["objections_handled"].append(current_objection)
                    
                    # Если уже 2 возражения отработано и нет закрытия — форсируем
                    if len(state.get("objections_handled", [])) >= 2 and not state.get("closing_attempted"):
                        if not any(ck in reply.lower() for ck in ["встреч", "подъехать", "записать", "приехать"]):
                            reply = reply.rstrip(".!?") + "... Когда удобно подъехать — сегодня или завтра?..."
                            state["closing_attempted"] = True
                    
                    # === ГАРАНТИЯ CTA: если нет призыва к действию — добавляем ===
                    cta_keywords = ["встреч", "подъехать", "записать", "приехать", "номер", "позвон", "удобно", "сегодня", "завтра"]
                    if not any(ck in reply.lower() for ck in cta_keywords) and not state.get("phone_received"):
                        cta_options = [
                            "... Когда удобно подъехать — сегодня или завтра?...",
                            "... Записать вас на встречу?...",
                            "... Напишите номер — специалист перезвонит и подберёт вариант...",
                        ]
                        reply += random.choice(cta_options)
                    
                    # Добавляем финальную паузу если фраза короткая
                    if len(reply) < 70 and not reply.endswith(('...', '?', '!')):
                        reply += "..."
                    
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
    
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.45,        # Больше вариативности = живее
            "similarity_boost": 0.85, # Сохраняет тембр
            "style": 0.75,            # Умеренная экспрессия
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
    """Старт диалога — с сбросом трекинга возражений"""
    uid = str(update.effective_user.id)
    
    # === СБРОС ТРЕКИНГА ПРИ НОВОМ ДИАЛОГЕ ===
    conversations[uid] = {
        "history": [],
        "objections_handled": [],
        "closing_attempted": False,
        "phone_received": False,
    }
    
    # Естественное открытие (чередуем варианты)
    openings = [
        "Добрый день... Это Ксения из Моментума... Вы раньше у нас работали... подскажите, как сейчас дела?",
        "Здравствуйте... Ксения, менеджер Моментума... Вы у нас раньше работали... не против пару минут поговорить?",
        "Добрый день... Это Ксения из таксопарка Моментум... Видела, вы раньше у нас работали... как у вас сейчас с работой?",
    ]
    first = random.choice(openings)
    
    conversations[uid]["history"].append({"role": "assistant", "content": first})
    await send_voice(update, first, uid)
    await update.message.reply_text("Отвечайте голосом или текстом — как удобнее 😊")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосового сообщения"""
    uid = str(update.effective_user.id)
    if uid not in conversations:
        conversations[uid] = {"history": [], "objections_handled": [], "closing_attempted": False, "phone_received": False}
    
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
        conversations[uid] = {"history": [], "objections_handled": [], "closing_attempted": False, "phone_received": False}
    
    reply = await generate_response(update.message.text, conversations[uid]["history"], uid)
    await send_voice(update, reply, uid)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сброс диалога"""
    uid = str(update.effective_user.id)
    conversations[uid] = {"history": [], "objections_handled": [], "closing_attempted": False, "phone_received": False}
    await update.message.reply_text("Сброшено... /start чтобы начать заново...")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("🎙️ Ксения запущена! Голос оптимизирован под естественность + цель: закрыть на встречу")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
