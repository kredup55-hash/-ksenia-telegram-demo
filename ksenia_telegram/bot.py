import logging
import os
import random
import tempfile
import subprocess
import io
import numpy as np
import librosa
import soundfile as sf

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# --- НАСТРОЙКИ API ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "YOUR_YANDEX_KEY")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "YOUR_YANDEX_FOLDER")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "YOUR_OPENROUTER_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "YOUR_ELEVENLABS_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "Rachel") # Используй ID голоса Rachel или свой

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================
# 1. БАЗА ЗНАНИЙ (Сокращенная выжимка из твоего файла)
# ==========================================
KNOWLEDGE_BASE = """
Ты Ксения, менеджер таксопарка Моментум.
ЦЕНЫ И УСЛОВИЯ:
- Комфорт+: Tiggo 7 Pro (2000/день 2 нед, затем 2400), Atlas Pro (2200/день 2 нед, затем 2800), Belgee X70 (2500/день 2 нед, затем 2800).
- Комфорт: Tiggo 4 Pro (1790/день 2 нед, затем 2200), Coolray (2000/день 2 нед, затем 2300).
- Особые условия (новые авто, без депозита): Tenet T7 (2500/день 2 нед, затем 3000), Arrizo 8 (2500/день 2 нед, затем 3300).
- Бонус за активную работу: 13 500 руб/мес.
- ТО каждые 15 000 км за счет парка. Сервис Ремтакс.
- Адрес: м. Алма-Атинская, Братеевская 22А.
- Первый день бесплатно на всех машинах.
- Депозита нет на новых авто.
"""

# ==========================================
# 2. «ЖИВОЙ» ПРОМПТ (Из твоего сообщения)
# ==========================================
SYSTEM_PROMPT = f"""
{KNOWLEDGE_BASE}

Ты Ксения, оператор таксопарка "Моментум". Общаешься голосом по телефону с бывшим водителем. 
Твоя речь должна быть МАКСИМАЛЬНО ЕСТЕСТВЕННОЙ, как у живого человека.

ПРАВИЛА РЕЧИ:
1. Максимум 2-3 коротких предложения за раз. Живые люди не говорят монологами.
2. Используй естественные паузы: многоточие (...) для раздумий, тире (—) для уточнений.
3. Добавляй лёгкие разговорные связки: "ну...", "честно говоря", "понимаете", "давайте так". Не чаще 1 раза на ответ.
4. Допускай микро-самокоррекцию: "то есть... вернее...", "как бы... нет, лучше так".
5. Никогда не заканчивай фразу вопросом "да?". Используй: "как вам?", "что думаете?", "подскажите?".
6. Реагируй на тон водителя: если он раздражён — говори мягче. Если активен — энергичнее.
7. После любого возражения сначала присоединись ("понимаю, бывает", "слышу вас"), затем дай 1 факт, затем мягкий вопрос.
8. ЗАПРЕЩЕНО: идеальная пунктуация, канцеляризмы, списки, повторение одной структуры подряд.

ФОРМАТ ОТВЕТА:
Только текст. Без тегов, без SSML, без markdown. Используй "..." для пауз.
Максимум 40 слов. Всегда заканчивай мягким вопросом или предложением следующего шага.

ПРИМЕРЫ ЕСТЕСТВЕННОЙ РЕЧИ:
- "Понимаю... цена кажется высокой, но там же всё включено — страховка, ТО, ремонт. По факту вы не думаете о расходах... Первый день вообще бесплатно, можно просто посидеть за рулём, оценить. Как вам?.."
- "Да, бывает такое... А что именно смущает — машина, условия или график?.. Может, просто подъедете посмотреть?.. Как вам идея?.."

ГОВОРИ НА ВЫ. Будь тёплой, уверенной, без навязчивости.
"""

# ==========================================
# 3. ПОСТОБРАБОТКА АУДИО (Человечность)
# ==========================================
def humanize_audio(audio_bytes: bytes) -> bytes:
    """Добавляет микро-шум, компрессию (эффект телефона) и убирает цифровой звон."""
    try:
        # Декодируем MP3 в PCM
        audio, sr = librosa.load(io.BytesIO(audio_bytes), sr=22050, mono=True)
        
        # 1. Микро-шум комнаты (0.3-0.5%)
        noise_floor = np.random.normal(0, 0.003, audio.shape)
        audio = audio + noise_floor
        
        # 2. Лёгкая компрессия (имитация телефонной линии)
        threshold = -20
        ratio = 2.0
        # Избегаем log(0)
        audio_safe = np.clip(np.abs(audio), 1e-6, None)
        audio_db = 20 * np.log10(audio_safe)
        mask = audio_db > threshold
        audio[mask] = np.sign(audio[mask]) * (10**((threshold + (audio_db[mask] - threshold) / ratio)/20))
        
        # 3. Фильтр частот (убираем цифровой "звон") - Pre/De-emphasis
        audio = librosa.effects.preemphasis(audio, coef=0.97)
        audio = librosa.effects.deemphasis(audio, coef=0.97)
        
        # 4. Нормализация LUFS ~ -16
        rms = np.sqrt(np.mean(audio**2))
        target_rms = 10**(-16/20)
        if rms > 0:
            audio = audio * (target_rms / rms)
        
        # 5. Обрезаем клиппинг
        audio = np.clip(audio, -0.99, 0.99)
        
        # Экспорт в MP3
        out = io.BytesIO()
        sf.write(out, audio, sr, format='MP3', subtype='MPEG_LAYER_III', bitrate=192000)
        return out.getvalue()
    except Exception as e:
        logger.error(f"Audio humanize error: {e}")
        return audio_bytes

# ==========================================
# 4. ФУНКЦИИ AI (STT, LLM, TTS)
# ==========================================

async def recognize_speech(audio_path: str) -> str:
    """Yandex STT"""
    try:
        ogg_path = audio_path.replace(".ogg", "_opus.ogg")
        subprocess.run([
            "ffmpeg", "-i", audio_path, "-c:a", "libopus", "-b:a", "32k", ogg_path, "-y", "-loglevel", "quiet"
        ], check=True)
        
        with open(ogg_path, "rb") as f:
            data = f.read()
        
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = f"https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?folderId={YANDEX_FOLDER_ID}&lang=ru-RU&format=oggopus"
            headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
            async with session.post(url, headers=headers, data=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("result", "")
        return ""
    except Exception as e:
        logger.error(f"STT Error: {e}")
        return ""

async def generate_response(user_text: str, history: list) -> str:
    """OpenRouter (Claude)"""
    history.append({"role": "user", "content": user_text})
    
    payload = {
        "model": "anthropic/claude-3-5-sonnet-20240620", # Или sonnet-4-5 если доступен
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history[-6:], # Берем последние 6 сообщений для контекста
        "max_tokens": 100,
        "temperature": 0.85
    }
    
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://momentum-bot.railway.app"
            },
            json=payload
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                # Очистка от тегов
                reply = reply.replace("<speak>", "").replace("</speak>", "").replace("<break>", "")
                history.append({"role": "assistant", "content": reply})
                return reply
    return "Простите, связь прервалась... Повторите, пожалуйста."

async def synthesize_speech(text: str) -> bytes:
    """ElevenLabs TTS с параметрами «Человека»"""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.40,        # Меньше стабильности = живая интонация
            "similarity_boost": 0.85, # Сохраняет тембр
            "style": 0.35,            # Умеренная экспрессия
            "use_speaker_boost": True # Убирает "стекло"
        }
    }
    
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                return await resp.read()
    return b""

# ==========================================
# 5. TELEGRAM HANDLERS
# ==========================================

async def human_delay(text_length: int):
    """Имитация задержки перед ответом (человек не отвечает за 0.1с)"""
    delay = min(1.5, max(0.5, text_length / 40)) # Чем длиннее ответ, тем дольше "думает"
    delay += random.uniform(0.2, 0.5) # Случайная добавка
    await asyncio.sleep(delay)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосового сообщения"""
    chat_id = update.effective_chat.id
    
    # 1. Скачиваем голос
    file = await context.bot.get_file(update.message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        audio_path = tmp.name
    
    # 2. Распознаем речь
    user_text = await recognize_speech(audio_path)
    if not user_text:
        await update.message.reply_text("Я не расслышала... Повторите, пожалуйста.")
        return
    
    logger.info(f"User said: {user_text}")
    
    # 3. Генерируем ответ (Имитация набора текста)
    await update.message.reply_chat_action("typing")
    
    # Инициализация истории диалога для этого чата
    if 'history' not in context.user_data:
        context.user_data['history'] = []
    
    bot_reply = await generate_response(user_text, context.user_data['history'])
    logger.info(f"Bot replied: {bot_reply}")
    
    # 4. Синтез речи
    await update.message.reply_chat_action("record_audio")
    audio_bytes = await synthesize_speech(bot_reply)
    
    # 5. Постобработка (Humanize)
    human_audio_bytes = humanize_audio(audio_bytes)
    
    # 6. Живая задержка перед отправкой
    await human_delay(len(bot_reply))
    
    # 7. Отправка голосового
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out_tmp:
        out_tmp.write(human_audio_bytes)
        out_tmp.seek(0)
        await update.message.reply_voice(out_tmp)

# ==========================================
# 6. ЗАПУСК
# ==========================================

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Обработчик голосовых сообщений
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    # Можно добавить обработчик текста, который тоже будет отвечать голосом
    # app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_voice)) 
    
    print("🎙️ Голосовой бот Ксения запущен...")
    app.run_polling()

if __name__ == "__main__":
    import asyncio
    main()
