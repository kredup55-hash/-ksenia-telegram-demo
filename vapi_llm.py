"""
vapi_llm.py — промежуточный LLM сервер для Vapi.
Vapi шлёт запросы сюда → мы вызываем Claude → фильтруем ответ → возвращаем чистый текст.
"""
import os, re, json, asyncio, logging
from aiohttp import web, ClientSession, ClientTimeout

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
PORT = int(os.environ.get("VAPI_PORT", 8083))

SYSTEM_PROMPT = """Ты Ксения, менеджер таксопарка Моментум. Говоришь по телефону — тепло, уверенно, по-человечески.

ТЫ УЖЕ ПОЗДОРОВАЛАСЬ.

КАК ОТВЕЧАТЬ В ЗАВИСИМОСТИ ОТ СИТУАЦИИ:

КОРОТКИЙ ОТВЕТ (1 предложение + вопрос) — когда клиент согласен, говорит да, хорошо, понял:
отлично. вам сегодня или завтра удобнее подъехать?

СРЕДНИЙ ОТВЕТ (2-3 предложения) — когда спрашивает про одну машину или одно условие:
тиго семь — это кроссовер, две тысячи в день. первый день вообще бесплатный. вам сегодня или завтра?

РАЗВЕРНУТЫЙ ОТВЕТ (3-4 предложения) — когда просит рассказать подробнее про класс:
называй по одной машине, потом вопрос что больше интересует.

НИКОГДА:
- не перечисляй всё сразу списком
- не говори больше 4 предложений без вопроса
- не используй латиницу в названиях машин — ТОЛЬКО кириллица
- не здоровайся снова

ЕСЛИ СПРАШИВАЮТ РОБОТ:
живой человек, ксения. условия интересно?

МАШИНЫ — ТОЛЬКО русские названия, никакой латиницы:
комфорт плюс:
- тиго семь — две тысячи в день
- атлас — две тысячи двести в день
- белджи семьдесят — две тысячи пятьсот в день
- тенет — две тысячи пятьсот, без залога
комфорт:
- кулрей — две тысячи в день
- тиго четыре — тысяча семьсот девяносто в день
- белджи пятьдесят — две тысячи триста, без залога
эконом:
- солярис, рапид или поло — тысяча восемьсот пятьдесят в день

АКЦИИ — действуют на все машины:
- первый день бесплатно всегда
- приедешь сегодня — два дня бесплатно, четыре тысячи экономия
- из другого парка — три дня бесплатно, шесть тысяч экономия
- приведи друга — по тысяча пятьсот каждому

УСЛОВИЯ:
- депозита нет
- страховка включена
- техобслуживание за наш счёт
- сервис до девяти вечера
- в парк раз в две недели
- деньги выводишь когда хочешь
- оформление тридцать минут
- опыт не нужен, только стаж три года

СТРУКТУРА ЗВОНКА:
1. спроси класс: вы на каком классе работаете?
2. назови одну лучшую машину + цену
3. скажи про бесплатный первый день
4. закрой: вам сегодня или завтра удобнее?

ВОЗРАЖЕНИЯ — одна фраза + вопрос:
- дорого: первый день бесплатно, риска ноль. сегодня или завтра?
- подумаю: если сегодня — четыре тысячи экономия. сегодня или завтра?
- другой парк: три дня бесплатно при переходе. сегодня или завтра?
- далеко: один раз тридцать минут, потом путевые электронные. сегодня или завтра?
- нет опыта: опыт не нужен, только стаж три года. сегодня или завтра?

АДРЕС: братеевская двадцать два а, метро алма-атинская, с десяти до половины седьмого."""


def filter_text(text: str) -> str:
    """Фильтр — убирает латиницу брендов, цифры, исправляет адрес."""
    fixes = [
        # Бренды латиницей → кириллица
        (r'(?i)chery\s+tiggo\s+7\s+pro', 'тиго семь'),
        (r'(?i)chery\s+tiggo\s+7', 'тиго семь'),
        (r'(?i)chery\s+tiggo\s+4\s+pro', 'тиго четыре'),
        (r'(?i)chery\s+arrizo\s+8', 'аризо восемь'),
        (r'(?i)geely\s+atlas\s+pro', 'атлас'),
        (r'(?i)geely\s+atlas', 'атлас'),
        (r'(?i)geely\s+coolray', 'кулрей'),
        (r'(?i)belgee\s+x70', 'белджи семьдесят'),
        (r'(?i)belgee\s+x50', 'белджи пятьдесят'),
        (r'(?i)belgee\s+x\s*70', 'белджи семьдесят'),
        (r'(?i)belgee\s+x\s*50', 'белджи пятьдесят'),
        (r'(?i)tenet\s+t7', 'тенет'),
        (r'(?i)tiggo\s+7', 'тиго семь'),
        (r'(?i)tiggo\s+4', 'тиго четыре'),
        (r'(?i)coolray', 'кулрей'),
        (r'(?i)atlas\s+pro', 'атлас'),
        # Цифры → слова
        (r'\b2000\b', 'две тысячи'),
        (r'\b2200\b', 'две тысячи двести'),
        (r'\b2300\b', 'две тысячи триста'),
        (r'\b2400\b', 'две тысячи четыреста'),
        (r'\b2500\b', 'две тысячи пятьсот'),
        (r'\b2800\b', 'две тысячи восемьсот'),
        (r'\b1790\b', 'тысяча семьсот девяносто'),
        (r'\b1850\b', 'тысяча восемьсот пятьдесят'),
        (r'\b4000\b', 'четыре тысячи'),
        (r'\b6000\b', 'шесть тысяч'),
        (r'\b13500\b', 'тринадцать тысяч пятьсот'),
        (r'\b12000\b', 'двенадцать тысяч'),
        (r'\b1500\b', 'тысяча пятьсот'),
        # Адрес
        (r'(?i)братьевской', 'братеевской'),
        (r'(?i)братьевская', 'братеевская'),
        (r'(?i)алма-атинское', 'алма-атинская'),
        (r'(?i)алма\s*-\s*атинское', 'алма-атинская'),
        # Чистка
        (r' {2,}', ' '),
    ]
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text)
    return text.strip()


async def call_claude(messages: list) -> str:
    """Вызов Claude через OpenRouter."""
    async with ClientSession() as session:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={
                "model": "anthropic/claude-sonnet-4-5",
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                "temperature": 0.3,
                "max_tokens": 200,
            },
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://railway.app",
                "Content-Type": "application/json",
            },
            timeout=ClientTimeout(total=15),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data["choices"][0]["message"]["content"]
            else:
                body = await r.text()
                logger.error(f"OpenRouter {r.status}: {body[:200]}")
                return "секундочку."


async def handle_llm(request: web.Request) -> web.StreamResponse:
    """
    Endpoint /llm — принимает запрос от Vapi в формате OpenAI,
    вызывает Claude, фильтрует ответ и стримит обратно.
    """
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    messages = body.get("messages", [])
    # Убираем system из messages — он у нас в SYSTEM_PROMPT
    messages = [m for m in messages if m.get("role") != "system"]

    stream = body.get("stream", False)

    try:
        reply = await call_claude(messages)
        reply = filter_text(reply)
        logger.info(f"Reply: {reply[:100]}")
    except Exception as e:
        logger.error(f"Claude error: {e}")
        reply = "секундочку."

    if stream:
        # SSE стриминг для Vapi
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        })
        await response.prepare(request)

        # Отправляем chunk по chunk (по словам)
        words = reply.split()
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            data = {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "choices": [{"delta": {"content": chunk}, "index": 0, "finish_reason": None}]
            }
            await response.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
            await asyncio.sleep(0.01)

        # Финальный chunk
        final = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]
        }
        await response.write(f"data: {json.dumps(final)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response
    else:
        # Обычный JSON ответ
        result = {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [{
                "message": {"role": "assistant", "content": reply},
                "index": 0,
                "finish_reason": "stop"
            }]
        }
        return web.json_response(result)


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/llm", handle_llm)
    app.router.add_get("/health", handle_health)
    return app


if __name__ == "__main__":
    app = create_app()
    logger.info(f"Starting vapi_llm on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
