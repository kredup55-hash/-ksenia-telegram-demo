import logging, aiohttp, tempfile, os, re, io, asyncio, random, json, threading
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TOKEN", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "z5HAjLhe7iDUpZbsW2kb").strip()

conversations = {}
audio_cache = {"filler": None, "laugh": None, "ah": None, "search": None}

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Говоришь максимально живо, тепло и немного небрежно, как реальный человек. ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

ГЛАВНЫЕ ЗАКОНЫ (СТРОГО!):
1. НИКАКОЙ ЛАТИНИЦЫ. Названия машин только кириллицей.
2. ДОЗИРУЙ ИНФОРМАЦИЮ. Одна мысль — одно короткое предложение. Никогда не сваливай в кучу цену, акции и вопрос!
3. Выдавай по одному факту и жди реакции. Максимум 2 предложения за один раз.

СЕКРЕТЫ "ЖИВОЙ" РЕЧИ:
- Начинай ответы с: "угу...", "поняла...", "ну смотрите...", "слушайте..."
- Делай вид, что думаешь: "так...", "если не ошибаюсь..."
- Не читай текст как робот-диктор.

ЕСЛИ СПРАШИВАЮТ РОБОТ ЛИ ТЫ:
"угу... (смешок) живой человек я, ксения меня зовут. ну что, условия интересно послушать?"

МАШИНЫ И ЦЕНЫ (строго кириллицей):
комфорт плюс:
- тиго семь — две тыщи в день
- атлас — две двести
- бельджи семьдесят — две пятьсот
- тенет — две пятьсот без залога
комфорт:
- кулрей — две тыщи
- тиго четыре — тыща семьсот девяносто
- бельджи пятьдесят — две триста без залога
эконом:
- солярис, рапид или поло — тыща восемьсот пятьдесят

АКЦИИ И УСЛОВИЯ:
- первый день бесплатно всегда
- приедешь сегодня — два дня бесплатно (экономия четыре тыщи)
- депозита нет, страховка включена

СТРУКТУРА ДИАЛОГА (ШАГ ЗА ШАГОМ, НЕ ВСЁ СРАЗУ!):
Шаг 1. Клиент согласился говорить -> Спроси: "угу, а вы вообще на каком классе работаете?"
Шаг 2. Клиент назвал класс -> Назови ТОЛЬКО ОДНУ машину и цену. Спроси: "как вам по цене?"
Шаг 3. Клиент согласен с ценой -> Добавь выгоду: "кстати, первый день у нас вообще бесплатно. вам сегодня или завтра удобнее подъехать?"
Шаг 4. Возражения (дорого/подумаю) -> "понимаю... просто если сегодня приедете — это четыре тыщи экономия. сегодня или завтра?"
"""

SURPRISE_WORDS = ['много', 'миллион', 'лет работаю', 'офигеть', 'серьёзно', 'столько']
ROBOT_WORDS = ['робот', 'бот', 'искусственный', 'нейросеть', 'chatgpt', 'программа']
SEARCH_WORDS = ['в каком году', 'когда открываете', 'какой адрес', 'где находитесь']

def detect_macro(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ROBOT_WORDS): return "laugh"
    if any(w in t for w in SURPRISE_WORDS): return "ah"
    if any(w in t for w in SEARCH_WORDS): return "search"
    return "filler"

def remove_emoji(text: str) -> str:
    return re.compile("[\U00010000-\U0010ffff\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+", flags=re.UNICODE).sub('', text).strip()

def post_process_text(text: str) -> str:
    text = remove_emoji(text)
    # Агрессивный фильтр для удаления латиницы и исправления цифр
    fixes = [
        (r'(?i)chery tiggo 7 pro', 'чери тиго семь про'),
        (r'(?i)chery tiggo 7', 'чери тиго семь'),
        (r'(?i)tiggo 7', 'тиго семь'),
        (r'(?i)tiggo 4', 'тиго четыре'),
        (r'(?i)tiggo', 'тиго'),
        (r'(?i)geely atlas pro', 'джили атлас про'),
        (r'(?i)atlas pro', 'атлас про'),
        (r'(?i)belgee x70', 'бельджи икс семьдесят'),
        (r'(?i)belgee x50', 'бельджи икс пятьдесят'),
        (r'(?i)belgee', 'бельджи'),
        (r'(?i)coolray', 'кулрей'),
        (r'(?i)arrizo', 'аризо'),
        (r'(?i)tenet', 'тенет'),
        (r'черри\s+тигго', 'чери тиго'), 
        (r'чери\s+тигго', 'чери тиго'),
        (r'белджи', 'бельджи'), 
        (r'билджи', 'бельджи'),
        (r'две\s+тысячи\s+восемьсот', 'две восемьсот'), 
        (r'две\s+тысячи\s+пятьсот', 'две пятьсот'),
        (r'две\s+тысячи\s+четыреста', 'две четыреста'), 
        (r'две\s+тысячи\s+триста', 'две триста'),
        (r'две\s+тысячи\s+двести', 'две двести'), 
        (r'две\s+тысячи\s+рублей', 'две тыщи'),
        (r'две\s+тысячи(?!\s+(двести|триста|четыреста|пятьсот|восемьсот))', 'две тыщи'),
        (r'три\s+тысячи\s+триста', 'три триста'), 
        (r'три\s+тысячи', 'три тыщи'),
        (r'тысяча\s+восемьсот\s+пятьдесят', 'тыща восемьсот пятьдесят'),
        (r'тысяча\s+семьсот\s+девяносто', 'тыща семьсот девяносто'),
        (r'тысяча\s+восемьсот', 'тыща восемьсот'),
        (r'\bтысячи\b', 'тыщи'), 
        (r'\bтысяча\b', 'тыща'),
        (r'тринадцать\s+тысяч', 'тринадцать тыщ'), 
        (r'двенадцать\s+тысяч', 'двенадцать тыщ'),
        (r'\.{2,}', ','), 
        (r',\s*,', ','), 
        (r' {2,}', ' '),
        (r'2000', 'две тыщи'),
        (r'2500', 'две пятьсот'),
        (r'2200', 'две двести'),
        (r'2400', 'две четыреста')
    ]
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.strip()

async def handle_llm(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")
    
    messages = [m for m in body.get("messages", []) if m.get("role") != "system"]
    stream = body.get("stream", False)
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model": "anthropic/claude-3.5-sonnet", 
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages, 
                    "temperature": 0.4, 
                    "max_tokens": 150
                },
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}", 
                    "HTTP-Referer": "https://railway.app", 
                    "Content-Type": "application/json"
                },
                timeout=aiohttp.ClientTimeout(total=20)) as r:
                
                if r.status == 200:
                    data = await r.json()
                    reply = data["choices"][0]["message"]["content"]
                    reply = post_process_text(reply)
                    logger.info(f"Vapi reply: {reply[:80]}")
                else:
                    logger.error(f"OpenRouter {r.status}: {(await r.text())[:200]}")
                    reply = "секундочку."
    except Exception as e:
        logger.error(f"Vapi LLM error: {e}")
        reply = "секундочку."

    if stream:
        import json as json_mod
        async def stream_gen():
            chunk = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"delta": {"role": "assistant", "content": reply}, "index": 0, "finish_reason": None}]}
            yield f"data: {json_mod.dumps(chunk)}\n\n".encode()
            done = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
            yield f"data: {json_mod.dumps(done)}\n\n".encode()
            yield b"data: [DONE]\n\n"
            
        return web.Response(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
            body=b"".join([
                f"data: {json.dumps({'id': 'chatcmpl-1', 'object': 'chat.completion.chunk', 'choices': [{'delta': {'role': 'assistant', 'content': reply}, 'index': 0, 'finish_reason': None}]})}\n\n".encode(),
                f"data: {json.dumps({'id': 'chatcmpl-1', 'object': 'chat.completion.chunk', 'choices': [{'delta': {}, 'index': 0, 'finish_reason': 'stop'}]})}\n\n".encode(),
                b"data: [DONE]\n\n"
            ])
        )

    result = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": reply}, "index": 0, "finish_reason": "stop"}]
    }
    return web.json_response(result)

async def handle_health(request):
    return web.Response(text="OK")

def main():
    import threading
    
    async def run_web():
        app_web = web.Application()
        app_web.router.add_post("/llm", handle_llm)
        app_web.router.add_post("/chat/completions", handle_llm)
        app_web.router.add_get("/health", handle_health)
        runner = web.AppRunner(app_web)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("WEBHOOK_PORT", 8082)))
        await site.start()
        await asyncio.Event().wait()

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=lambda: loop.run_until_complete(run_web()), daemon=True)
    t.start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.run_polling()

if __name__ == "__main__":
    main()
