import asyncio, re, aiohttp, os, json, time, random, io, tempfile, subprocess
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiohttp import web

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
TOKEN = os.getenv("TOKEN")
MANAGER_ID = int(os.getenv("MANAGER_ID", "-1003726537840"))
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8082"))
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Rachel")

bot = Bot(token=TOKEN)
dp = Dispatcher()
user_states = {}

# ==========================================
# БАЗА ЗНАНИЙ (сокращённая)
# ==========================================
KNOWLEDGE_BASE = """Ты Ксения, менеджер таксопарка Моментум. Отвечай кратко, по-человечески.

ЦЕНЫ:
- Комфорт+: Belgee X70 — 2500 руб/день (2 нед), затем 2800. Atlas Pro — 2200/день (2 нед), затем 2800. Tiggo 7 Pro — 2000/день (2 нед), затем 2400.
- Комфорт: Coolray — 2000/день (2 нед), затем 2300. Tiggo 4 Pro — 1790/день (2 нед), затем 2200.
- Особые условия (без депозита): Tenet T7, Arrizo 8 — 2500/день (2 нед), бонус 13 500 руб.

Первый день бесплатно на всех машинах!

Отвечай как живой человек — коротко, с паузами (...), без формальностей. Максимум 2-3 предложения."""

# ==========================================
# АУДИО ОБРАБОТКА (без pydub!)
# ==========================================
def humanize_audio(audio_bytes: bytes) -> bytes:
    """Просто возвращаем аудио как есть — ElevenLabs и так даёт хорошее качество"""
    return audio_bytes

# ==========================================
# РАСПОЗНАВАНИЕ РЕЧИ (Yandex STT)
# ==========================================
async def recognize_speech(audio_path: str) -> str:
    try:
        # Конвертируем OGG в OPUS
        ogg_path = audio_path.replace(".ogg", "_opus.ogg")
        subprocess.run([
            "ffmpeg", "-i", audio_path, 
            "-c:a", "libopus", 
            "-b:a", "32k", 
            ogg_path, 
            "-y", "-loglevel", "quiet"
        ], check=True)
        
        with open(ogg_path, "rb") as f:
            data = f.read()
        
        # Отправляем в Yandex STT
        async with aiohttp.ClientSession() as s:
            url = f"https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?folderId={YANDEX_FOLDER_ID}&lang=ru-RU&format=oggopus"
            headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
            async with s.post(url, headers=headers, data=data) as r:
                if r.status == 200:
                    j = await r.json()
                    return j.get("result", "").strip()
        return ""
    except Exception as e:
        print(f"STT Error: {e}", flush=True)
        return ""

# ==========================================
# ГЕНЕРАЦИЯ ОТВЕТА (Claude)
# ==========================================
async def generate_response(user_text: str, history: list) -> str:
    history.append({"role": "user", "content": user_text})
    
    payload = {
        "model": "anthropic/claude-3-5-sonnet-20240620",
        "messages": [
            {"role": "system", "content": KNOWLEDGE_BASE},
            *history[-6:]  # Последние 6 сообщений
        ],
        "max_tokens": 100,
        "temperature": 0.85
    }
    
    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json"
            },
            json=payload
        ) as r:
            if r.status == 200:
                data = await r.json()
                reply = data["choices"][0]["message"]["content"].strip()
                history.append({"role": "assistant", "content": reply})
                return reply
    return "Простите... что-то со связью... Повторите..."

# ==========================================
# СИНТЕЗ РЕЧИ (ElevenLabs)
# ==========================================
async def synthesize_speech(text: str) -> bytes:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.40,
            "similarity_boost": 0.85,
            "style": 0.35,
            "use_speaker_boost": True
        }
    }
    
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=payload) as r:
            if r.status == 200:
                return await r.read()
    return b""

# ==========================================
# ЗАДЕРЖКА (имитация мышления)
# ==========================================
async def human_delay(text_length: int):
    delay = min(1.5, max(0.5, text_length / 40)) + random.uniform(0.2, 0.5)
    await asyncio.sleep(delay)

# ==========================================
# ОБРАБОТЧИКИ TELEGRAM
# ==========================================
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = str(message.from_user.id)
    user_states[uid] = {"history": [], "message_count": 0}
    
    greeting = "Добрый день... Это Ксения из Моментума... Вы раньше у нас работали... подскажите, как сейчас дела?.."
    user_states[uid]["history"].append({"role": "assistant", "content": greeting})
    
    await message.answer(greeting)

@dp.message(types.ContentType.VOICE)
async def handle_voice(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in user_states:
        user_states[uid] = {"history": [], "message_count": 0}
    
    state = user_states[uid]
    
    # Скачиваем голосовое
    file = await message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        audio_path = tmp.name
    
    # Распознаём речь
    user_text = await recognize_speech(audio_path)
    if not user_text:
        await message.answer("Не расслышала... Повторите, пожалуйста.")
        return
    
    print(f"User said: {user_text}", flush=True)
    
    # Генерируем ответ
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await generate_response(user_text, state["history"])
    print(f"Bot replied: {reply}", flush=True)
    
    # Синтезируем голос
    await bot.send_chat_action(message.chat.id, "record_audio")
    await human_delay(len(reply))
    
    audio_bytes = await synthesize_speech(reply)
    human_audio = humanize_audio(audio_bytes)
    
    # Отправляем голосовое
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out:
        out.write(human_audio)
        out.seek(0)
        await message.reply_voice(types.FSInputFile(out.name, filename="ksenia.mp3"))
    
    # Очистка
    os.unlink(out.name)
    os.unlink(audio_path)

@dp.message()
async def handle_text(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in user_states:
        user_states[uid] = {"history": [], "message_count": 0}
    
    state = user_states[uid]
    user_text = message.text
    
    # Генерируем ответ
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await generate_response(user_text, state["history"])
    
    # Отправляем текстом И голосом
    await human_delay(len(reply))
    await message.answer(reply)
    
    # Синтез голоса
    audio_bytes = await synthesize_speech(reply)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out:
        out.write(audio_bytes)
        out.seek(0)
        await message.reply_voice(types.FSInputFile(out.name, filename="ksenia.mp3"))
    os.unlink(out.name)

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    await asyncio.sleep(3)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"Webhook cleanup: {e}", flush=True)
    
    app = web.Application()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    print(f"Voice Bot listening on port {WEBHOOK_PORT}", flush=True)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(5)
    print("Starting polling...", flush=True)
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
