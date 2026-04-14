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

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Звонишь бывшему водителю чтобы вернуть его в парк.

ВАЖНО: Отвечай простым текстом БЕЗ каких-либо тегов. Только слова, запятые и точки. Никаких "мгм", "э-э", "ну" в тексте — только естественные паузы через запятые и многоточия "..." .

СТИЛЬ — КАК ЖИВОЙ ЧЕЛОВЕК ПО ТЕЛЕФОНУ:
- Говори коротко: 1-2 предложения, максимум 30 слов
- Используй "да?", "понимаю", "бывает", "знаете", "как думаете"
- Делай паузы через запятые и многоточия "..." — например: "Слушаю... понимаю, что вы ушли, но..."
- Не давай сразу весь монолог — веди диалог, задавай один вопрос за раз
- Избегай повторений и формальностей ("как я уже говорил")

ПСИХОЛОГИЯ ВОДИТЕЛЯ:
- Не терпит давления — предложил раз, не повторяй
- Верит цифрам, не словам
- Хочет чтобы его услышали — сначала выслушай, потом предлагай
- "Первый день бесплатно" снимает барьер

СТРУКТУРА РАЗГОВОРА:
1. Водитель говорит "да, есть минута" — спроси ОДНУ причину ухода
2. Выслушай, скажи "понимаю" или "да, бывает"
3. Предложи конкретное решение под его ситуацию
4. Мягкое закрытие — "попробуйте без риска, первый день бесплатно"

РЕАЛЬНЫЕ ЦЕНЫ:
Комфорт+: Tiggo 7 Pro новым 2000 руб/день 2 недели, затем 2400. Atlas Pro новым 2200, затем 2800. Belgee X70 новым 2500, затем 2800.
Комфорт: Tiggo 4 Pro новым 1790 руб/день 2 недели, затем 2200. Coolray новым 2000, затем 2300.
Без депозита новые авто: Tenet T7 и Arrizo 8, 2500 в день, бонус 13500 руб за активную работу.

ПРЕИМУЩЕСТВА — одно под ситуацию:
Дорого: "Tiggo 4 Pro от 1790 в день первые две недели, и первый день бесплатно, попробуете без риска"
Другой парк: "У нас три дня бесплатно при переходе из другого парка"
Мало заказов: "Приоритет в Яндексе вырос, водители говорят 12 плюс заказов в день даже вечером"
Проблемы с машиной: "Свой сервис Ремтакс до 21 часа, ТО за наш счёт каждые 15 тысяч"
Далеко ехать: "Путевые листы электронные, в парк раз в 14 дней"
Депозит: "На новых авто депозита нет совсем"
Сомневается: "Моментум 10 лет на рынке, ОСАГО и страховка включены"

СКРИПТЫ ВОЗРАЖЕНИЙ:
Дорого новые авто: "Нет скрытых платежей, всё включено. Бонус 13500 рублей в месяц при активной работе, это почти 5 дней аренды бесплатно. Плюс первый день бесплатно сразу."
Дорого обычная аренда: "ОСАГО, ТО, страховка за наш счёт. Первый день бесплатно, можно попробовать без риска."
Другой парк: "Три дня аренды бесплатно при переходе, главное взять минимум на две недели."
Мало заказов: "На новом авто приоритет в Яндексе выше автоматически, это напрямую влияет на заказы."
Подумать: "Что именно хочется обдумать, машина, цена или условия? Может сразу отвечу."
Поломка: "Свой сервис Ремтакс до 21 часа, ТО каждые 15 тысяч полностью за наш счёт."

ЕСЛИ ГОВОРИТ "да есть минута":
Следующая фраза: "Скажите, а почему тогда ушли от нас? Хочу понять."

ЕСЛИ ГОВОРИТ "нет неудобно":
"Хорошо, не буду мешать. Когда лучше перезвонить?"

ЗАКРЫТИЕ когда заинтересован:
"Первый день бесплатно, можно просто попробовать. Когда удобно подъехать, сегодня или завтра?"

ЗАПРЕЩЕНО:
- XML теги любые — SSML, prosody, break, speak и подобные
- Давить после отказа
- Два вопроса сразу
- Цены при невыполнении условий
- Штрафы парка

ГОВОРИ НА ВЫ. Максимум 2 предложения. Не используй "мгм", "э-э", "ну" в тексте — только естественные паузы через запятые и многоточия."""
# Добавлено: уточнение про отсутствие ненужных звуков в тексте

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
        "max_tokens": 100,  # Сократили для более коротких ответов
        "temperature": 0.7,  # Снижаем вариативность для естественности
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    j = await r.json()
                    reply = j["choices"][0]["message"]["content"]
                    # Убираем любые XML теги
                    import re
                    reply = re.sub(r'<[^>]+>', '', reply).strip()
                    
                    # Добавляем естественные паузы и проверяем длину
                    reply = reply.replace("...", "...")  # Унифицируем многоточия
                    reply = reply.replace("..", ".")  # Убираем лишние точки
                    
                    # Если ответ слишком длинный — сокращаем до 2 предложений
                    sentences = reply.split('.')
                    if len(sentences) > 2:
                        reply = '.'.join(sentences[:2]).strip() + '.'
                    
                    # Добавляем естественные паузы в конце
                    if not reply.endswith("...") and len(reply) < 80:
                        reply += "..."
                    
                    logger.info(f"Generated reply: {reply}")
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
            "stability": 0.6,  # Снижаем стабильность для более живого голоса
            "similarity_boost": 0.8,
            "style": 0.7,  # Уменьшаем стиль для естественности
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
    # Убираем текстовое сообщение "Ксения: {text}" — в реальном разговоре нет такого
    # Отправляем только аудио, чтобы звучало естественно
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
    first = "Добрый день! Это Ксения из Моментума. Вы раньше работали у нас, да? Звоню потому что у нас сейчас появились новые акции, выгодные цены и условия. Можете уделить пару минут?"
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
