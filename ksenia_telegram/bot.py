import asyncio
import logging
import os
import tempfile
import aiohttp
from io import BytesIO
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

try:
    from pydub import AudioSegment
    from pydub.generators import Silence
    PYDUB_OK = True
except ImportError:
    PYDUB_OK = False
    logging.warning("⚠️ pydub не установлен. Хвосты аудио не будут добавляться.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
TOKEN = os.getenv("TOKEN", "").strip()
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "").strip()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

if not TOKEN:
    logger.critical("❌ TOKEN не задан!")
    exit(1)

user_states = {}

# ==========================================
# ПРОМПТ (оптимизирован под живую речь)
# ==========================================
SYSTEM_PROMPT = """
Ты Ксения, живой менеджер таксопарка «Моментум». Ты звонишь бывшим водителям, чтобы вернуть их.
Твоя цель: вести естественный, короткий разговор голосом. Водитель должен думать, что говорит с реальным человеком.

ЖЕСТКИЕ ПРАВИЛА РЕЧИ:
1. Говори только от женского лица (записала, позвонила, решила).
2. НИКОГДА не используй цифры. Все суммы и числа пиши прописью: "тысяча семьсот девяносто", "две двести", "тринадцать пятьсот".
3. Названия машин пиши слитно или через дефис без пробелов, чтобы не было робо-пауз: "черритигго-семёрка", "джили-атлас-про", "бэлджи-икс-семьдесят", "черритигго-четвёрка".
4. Избегай искусственных слов-паразитов ("м-м", "ну-у", "э-э"). Они звучат как робот.
5. Говори короткими фразами по 5-8 слов. Не читай длинными предложениями.
6. В конце предложений делай естественную паузу, но не обрывай слова.
7. Тон: тёплый, деловой, без официоза. Ты не колл-центр, ты живой менеджер.

УСЛОВИЯ ПАРКА (используй только эти данные):
- Тигго 7 про: 2000 первые две недели, потом 2400.
- Тигго 4 про: 1790 в день, потом 2200.
- Бэлджи Х70: 2500 новые, потом 2800.
- Кулрей / Атлас про: 2000, потом 2400.
- Эконом класс: 1850 в день.
- Залогов нет, первый день бесплатно, ОСАГО и ТО за парк.
- Бонус 13500 за активную работу на особых условиях.

Если водитель спрашивает цену или модель — называй кратко и чётко. Если сомневается — предлагай приехать посмотреть. Не дави.
"""

# ==========================================
# ФУНКЦИИ
# ==========================================
async def synthesize_speech(text: str) -> bytes:
    if not ELEVENLABS_API_KEY:
        return b""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.28,
            "similarity_boost": 0.90,
            "style": 0.65,
            "use_speaker_boost": True
        },
        "output_format": "mp3_44100_128"
    }
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    audio = await resp.read()
                    # Добавляем 800мс тишины в конец, чтобы Telegram не обрезал хвосты
                    if PYDUB_OK:
                        try:
                            seg = AudioSegment.from_mp3(BytesIO(audio))
                            seg += Silence(duration=800).to_audio_segment()
                            out = BytesIO()
                            seg.export(out, format="mp3", bitrate="128k")
                            return out.getvalue()
                        except Exception as e:
                            logger.warning(f"Padding failed: {e}")
                    return audio
                else:
                    logger.error(f"ElevenLabs {resp.status}: {await resp.text()}")
                    return b""
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return b""

async def recognize_speech(ogg_bytes: bytes) -> str:
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return ""
    url = f"https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?folderId={YANDEX_FOLDER_ID}&lang=ru-RU&format=oggopus"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=ogg_bytes) as resp:
                return (await resp.json()).get("result", "").strip() if resp.status == 200 else ""
    except Exception as e:
        logger.error(f"STT error: {e}")
        return ""

async def get_ai_reply(user_text: str, history: list) -> str:
    if not OPENROUTER_KEY:
        return "Простите, связь прервалась."
    history.append({"role": "user", "content": user_text})
    payload = {
        "model": "anthropic/claude-3-haiku",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history[-5:]],
        "max_tokens": 120,
        "temperature": 0.85
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"].strip()
                    history.append({"role": "assistant", "content": reply})
                    return reply
                return "Не расслышала... Повторите, пожалуйста."
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return "Простите, связь прервалась."

# ==========================================
# ОБРАБОТЧИКИ
# ==========================================
async def send_voice_reply(update: Update, text: str, context: ContextTypes.DEFAULT_TYPE):
    await update.message.answer(text)
    await asyncio.sleep(0.8)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="record_voice")
    await asyncio.sleep(0.7)
    audio = await synthesize_speech(text)
    if audio:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            f.seek(0)
            await update.message.reply_voice(open(f.name, "rb"))
        os.unlink(f.name)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user_states[uid] = {"history": []}
    greeting = "Здрасьте, это Ксения из Моментума. Вы раньше у нас работали, я звоню потому что сейчас условия классные стали. Уделите пару минут?"
    await send_voice_reply(update, greeting, context)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in user_states: user_states[uid] = {"history": []}
    file = await context.bot.get_file(update.message.voice.file_id)
    ogg = await file.download_as_bytearray()
    text = await recognize_speech(bytes(ogg))
    if not text:
        await update.message.answer("Не расслышала... Говорите чуть громче.")
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await asyncio.sleep(1)
    reply = await get_ai_reply(text, user_states[uid]["history"])
    await send_voice_reply(update, reply, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in user_states: user_states[uid] = {"history": []}
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await asyncio.sleep(0.8)
    reply = await get_ai_reply(update.message.text, user_states[uid]["history"])
    await send_voice_reply(update, reply, context)

# ==========================================
# ЗАПУСК
# ==========================================
def main():
    request = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60)
    app = Application.builder().token(TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("🎙️ Бот Ксения запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
