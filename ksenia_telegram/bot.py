import asyncio, re, aiohttp, os, json, time, random, sys, logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiohttp import web

# ==========================================
# ЛОГИРОВАНИЕ
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# КОНФИГУРАЦИЯ — МОМЕНТУМ (ИСПРАВЛЕНО)
# ==========================================
TOKEN = os.getenv("TOKEN", "").strip()
if not TOKEN:
    logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная TOKEN не задана или пуста! Проверьте Variables в Railway.")
    sys.exit(1)

MANAGER_ID = int(os.getenv("MANAGER_ID", "-1003726537840"))
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "").strip()
WAZZUP_API_KEY = os.getenv("WAZZUP_API_KEY", "").strip()
WAZZUP_AVITO_CHANNEL_ID = os.getenv("WAZZUP_AVITO_CHANNEL_ID", "").strip()
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8082"))
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "").strip()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Rachel").strip()

bot = Bot(token=TOKEN)
dp = Dispatcher()
user_states = {}
reanimation_tasks = {}
processed_msgs = set()

# Актуальное наличие машин (обновляется через webhook каждый час)
INVENTORY_SECRET = os.getenv("INVENTORY_SECRET", "momentum_secret_2026").strip()
SHEETS_URL = "https://docs.google.com/spreadsheets/d/1e-k0aiA_1QOEmjSy2nJln0T9quxyGAKtcbVr4CSazFw/export?format=csv&gid=0"
INVENTORY_CHANNEL_ID = int(os.getenv("INVENTORY_CHANNEL_ID", "-1003879952093"))

inventory_special = []  # особые условия
inventory_regular = []  # обычная аренда
inventory_raw_text = " "  # сырой текст последнего сообщения из канала
debounce_buffers = {}   # uid -> { "texts": [], "task": Task, "name": str, "username": str, "chat_id": int}
DEBOUNCE_DELAY = 12.0    # секунд ждём перед ответом

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def clean_markdown(text):
    """Убираем маркдаун из ответов бота — Telegram/Авито его не поддерживают"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s', '', text, flags=re.MULTILINE)
    return text

async def human_delay(text_length, is_first=False):
    delay = random.uniform(3.0, 5.0) if is_first else random.uniform(2.0, 4.0)
    await asyncio.sleep(delay)

def get_state(uid):
    if uid not in user_states:
        user_states[uid] = {
            "history": [], "phone_received": False, "message_count": 0,
            "preferred_class": None, "preferred_model": None, "funnel_step": "greeting",
            "source": "telegram", "greeted": False, "first_question": " ",
            "client_name": " ", "stopped": False, "cars_shown": False,
            "phone_asked_after_car": False, "price_allowed": False, "wants_new_cars": False, "sb_rejected": False,
        }
    return user_states[uid]

def get_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Подобрать авто"), KeyboardButton(text="Сколько заработаю")],
            [KeyboardButton(text="Условия"), KeyboardButton(text="Адрес и часы")],
            [KeyboardButton(text="Задать вопрос")],
        ],
        resize_keyboard=True,
    )

def is_stop_message(text):
    t = text.lower()
    return any(p in t for p in ["заблокирую", "заблокировал", "в черный список"])

def is_sb_rejection(text):
    t = text.lower()
    return any(p in t for p in ["отказ сб", "отказали сб", "не прошел сб", "не прошла сб", "сб отказ", "безопасность отказ", "служба безопасности отказ", "пришёл отказ", "пришел отказ", "к сожалению отказ", "отказ по результатам", "не можем оформить", "проверка не пройдена"])

def is_avito_system_message(text):
    t = text.lower()
    return "системное сообщение" in t and "avito.ru" in t

def is_missed_call_or_vacancy(text):
    t = text.lower()
    call_kw = ["входящий звонок", "пропущенный", "звонили", "не дозвонил"]
    vacancy_kw = ["откликнул", "резюме", "отклик", "ознакомился с вашим предложением", "собеседование", "ваканс"]
    return any(kw in t for kw in call_kw), any(kw in t for kw in vacancy_kw)

# ==========================================
# БАЗА ЗНАНИЙ (СОКРАЩЁННО ДЛЯ ЧИТАЕМОСТИ, ЛОГИКА СОХРАНЕНА)
# ==========================================
KNOWLEDGE_BASE = """Ты Ксения, дружелюбный менеджер по подключению водителей в таксопарке Моментум. Всегда отвечай на русском языке.

ГЛАВНОЕ ПРАВИЛО О НАЛИЧИИ МАШИН: Называй ТОЛЬКО те машины которые есть в актуальном списке из таблицы. Если машины нет — НЕ упоминай её.
АБСОЛЮТНЫЙ ЗАПРЕТ: Запрещено использовать маркдаун: * _ ` #. Только обычный текст.
НИКОГДА не говори "от X руб" — только конкретные цены.

ЦЕНЫ (минимальные для новых):
Комфорт+: Belgee X70 — 2500/день (2 нед), затем 2800. Atlas Pro — 2200/день (2 нед), затем 2800. Tiggo 7 Pro — 2000/день (2 нед), затем 2400. FAW Bestune B70 — 2400/день.
Комфорт: Coolray — 2000/день (2 нед), затем 2300. Tiggo 4 Pro — 1790/день (2 нед), затем 2200.
Особые условия (без депозита, бонус 13 500 руб): Tenet T7, Arrizo 8 — 2500/день (2 нед), затем 3000/3300.
Первый день бесплатно на всех машинах!

ПРАВИЛА ОБЩЕНИЯ:
- Пиши КАК ЖИВОЙ ЧЕЛОВЕК: 2-3 предложения максимум.
- Обращайся на "вы". НЕ здоровайся повторно.
- НИКОГДА не начинай ответ повторяя слово клиента.
- Цель — ЗАПИСАТЬ НА ВСТРЕЧУ. Закрывай с альтернативой: "Вам удобнее сегодня или завтра?"
- На вопросы не по теме: "Я помогаю только с вопросами по аренде авто в таксопарке Моментум 😊"
- На "ты бот?": "Я Ксения, менеджер таксопарка Моментум 😊 Помогаю подобрать авто и ответить на вопросы по аренде."

ВОЗРАЖЕНИЯ (используй точно):
Дорого: "У нас нет скрытых платежей — цена фиксированная, всё включено. ОСАГО, ТО, страховка за наш счёт. Первый день бесплатно — можно попробовать без риска. Если приедете в течение 24 часов — дадим ещё один день бесплатно сверху. Записать вас на встречу?"
Думаю: "Что именно хочется обдумать — машина, цена или условия? Может сразу отвечу. Моментум 10 лет на рынке, работаем стабильно."
Далеко: "Путевые листы электронные, в парк раз в 14 дней. Если приедете сегодня или завтра — дадим доп день бесплатно. Записать?"

АДРЕС: Братеевская 22А, м. Алма-Атинская. Вход с торца, 2 этаж, вывеска Hot Wheels. Администратор Татьяна: +7 977 089-98-97.
"""

# ==========================================
# AI & КОНТЕКСТ
# ==========================================
async def ask_ai(user_message, history, context=" "):
    messages = list(history[-14:])
    messages.append({"role": "user", "content": user_message})
    system_messages = [{"type": "text", "text": KNOWLEDGE_BASE, "cache_control": {"type": "ephemeral"}}]
    if context:
        system_messages.append({"type": "text", "text": context})
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json={"model": "anthropic/claude-3-5-sonnet-20240620", "max_tokens": 500, "temperature": 0.3, "messages": [{"role": "system", "content": system_messages}] + messages},
            ) as r:
                data = await r.json()
                if "choices" in data:
                    return clean_markdown(data["choices"][0]["message"]["content"])
                logger.error(f"AI err: {data}")
                return None
    except Exception as e:
        logger.error(f"AI exc: {e}")
        return None

def build_context(state):
    parts = []
    from datetime import datetime
    moscow_hour = (datetime.utcnow().hour + 3) % 24
    if moscow_hour >= 19 or moscow_hour < 10:
        parts.append("ВАЖНО: Сейчас офис закрыт (с 19:00 до 10:00 МСК). НЕ говори 'сегодня до 18:30'.")
    if state.get("greeted") or state.get("message_count", 0) > 0:
        parts.append("УЖЕ ПОЗДОРОВАЛСЯ — КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО здороваться снова!")
    if state.get("preferred_class"):
        parts.append(f"Выбранный класс: {state['preferred_class']}")
    if state.get("phone_received"):
        parts.append("Номер уже получен. НЕ ПРОСИ номер повторно!")
    return "\n".join(parts)

def find_phone(text):
    patterns = [re.compile(r'[+]?[78][\s-(]?\d{3}[\s-)]?\s?\d{3}[\s-]?\d{2}[\s-]?\d{2}'), re.compile(r'\b\d{10,11}\b')]
    for p in patterns:
        m = p.search(text)
        if m: return m.group()
    return None

def parse_inventory_text(text):
    if not text: return ""
    lines = text.strip().split("\n")
    result = []
    in_block = False
    for line in lines:
        line = line.strip()
        if "Авто в парке на выдачу" in line: in_block = True; continue
        if in_block:
            if "Всего на линии" in line or "Итого" in line or (line and not " - " in line and not line.startswith("-")): break
            if " - " in line and line.strip():
                parts = line.split(" - ")
                if len(parts) == 2:
                    model, count = parts[0].strip(), parts[1].strip().split("(")[0].strip()
                    if count.isdigit() and int(count) > 0: result.append(f"{model}: {count} шт")
    return ", ".join(result) if result else ""

# ==========================================
# ОСНОВНАЯ ЛОГИКА ДИАЛОГА
# ==========================================
async def process_message(text, user_id, name, source="telegram", username=""):
    state = get_state(user_id)
    state["source"] = source
    state["message_count"] = state.get("message_count", 0) + 1
    if is_stop_message(text):
        state["stopped"] = True; cancel_reanimation(user_id)
        return "Понял вас, извините за беспокойство! Если передумаете — пишите, всегда рады помочь."
    if is_sb_rejection(text):
        state["sb_rejected"] = True; state["stopped"] = True; cancel_reanimation(user_id)
        return None

    phone = find_phone(text)
    if phone and not state["phone_received"]:
        state["phone_received"] = True; state["phone_number"] = phone; cancel_reanimation(user_id)
        # Уведомление менеджеру (упрощено)
        try: await bot.send_message(MANAGER_ID, f"🔥 НОВЫЙ ЛИД — МОМЕНТУМ!\nТелефон: {phone}\nИнтерес: {state.get('preferred_model') or state.get('preferred_class') or '-'}")
        except Exception as e: logger.error(f"Lead send err: {e}")
        return "Спасибо, записала! Передам менеджеру, скоро с вами свяжутся 😊"

    # Определение класса/модели (упрощено для стабильности)
    tl = text.lower()
    if any(k in tl for k in ["комфорт+", "к+"]): state["preferred_class"] = "комфорт+"
    elif "эконом" in tl: state["preferred_class"] = "эконом"
    elif "комфорт" in tl and "+" not in tl: state["preferred_class"] = "комфорт"

    context = build_context(state)
    response = await ask_ai(text, state["history"], context)
    if response:
        state["history"].append({"role": "user", "content": text})
        state["history"].append({"role": "assistant", "content": response})
        return response
    return "Какой класс авто рассматриваете — эконом, комфорт или комфорт+? 😊"

# ==========================================
# WAZZUP / AVITO / WEBHOOKS
# ==========================================
async def wazzup_send(chat_id, text, channel_id=None):
    use_channel = channel_id or WAZZUP_AVITO_CHANNEL_ID
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.wazzup24.com/v3/message", headers={"Authorization": f"Bearer {WAZZUP_API_KEY}", "Content-Type": "application/json"}, json={"channelId": use_channel, "chatId": chat_id, "chatType": "avito", "text": text}) as r:
                if r.status not in (200, 201): logger.error(f"Wazzup send err {r.status}")
    except Exception as e: logger.error(f"Wazzup send exc: {e}")

async def inventory_special_webhook(request):
    try:
        if request.headers.get("X-Secret", "") != INVENTORY_SECRET: return web.Response(status=403, text="Forbidden")
        data = await request.json(); global inventory_special
        inventory_special = data.get("cars", [])
        return web.Response(status=200, text="OK")
    except Exception as e: logger.error(f"Inventory special err: {e}"); return web.Response(status=400)

async def inventory_regular_webhook(request):
    try:
        if request.headers.get("X-Secret", "") != INVENTORY_SECRET: return web.Response(status=403, text="Forbidden")
        data = await request.json(); global inventory_regular
        inventory_regular = data.get("cars", [])
        return web.Response(status=200, text="OK")
    except Exception as e: logger.error(f"Inventory regular err: {e}"); return web.Response(status=400)

async def wazzup_webhook(request):
    try: await request.json()
    except: pass
    return web.Response(status=200)

# ==========================================
# РЕАНИМАЦИЯ
# ==========================================
def cancel_reanimation(uid):
    if uid in reanimation_tasks:
        for t in reanimation_tasks[uid]: t.cancel()
        del reanimation_tasks[uid]

def schedule_reanimation(uid, name):
    state = user_states.get(uid)
    if state and state.get("stopped"): return
    cancel_reanimation(uid)
    # Упрощённая реанимация через 60 мин
    reanimation_tasks[uid] = [asyncio.create_task(asyncio.sleep(3600))]

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = str(message.from_user.id)
    user_states[uid] = {"history": [], "phone_received": False, "message_count": 0, "preferred_class": None, "preferred_model": None, "funnel_step": "greeting", "source": "telegram", "greeted": True, "first_question": " ", "client_name": " ", "stopped": False, "cars_shown": False, "phone_asked_after_car": False, "price_allowed": False, "wants_new_cars": False}
    cancel_reanimation(uid)
    greeting = "Здравствуйте! Меня зовут Ксения, я менеджер таксопарка Моментум 😊\nПомогу подобрать авто для работы в такси. Какой класс авто вас интересует — эконом, комфорт или комфорт+?"
    user_states[uid]["funnel_step"] = "conversation"
    user_states[uid]["history"].append({"role": "assistant", "content": greeting})
    await message.answer(greeting, reply_markup=get_menu())

@dp.message()
async def handle_message(message: types.Message):
    if message.chat.type in ["group", "supergroup"]: return
    uid = str(message.from_user.id)
    text = message.text
    if not text: return
    
    name = message.from_user.first_name or "друг"
    username = f"@{message.from_user.username}" if message.from_user.username else ""
    cancel_reanimation(uid)
    state = get_state(uid)
    if state.get("stopped"): return

    response = await process_message(text, uid, name, "telegram", username)
    is_first = (state.get("message_count", 0) <= 1)
    await human_delay(len(response or ""), is_first=is_first)
    await message.answer(response or "Чем могу помочь?", reply_markup=get_menu())
    schedule_reanimation(uid, name)

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    await asyncio.sleep(3)
    try: await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e: logger.error(f"Webhook cleanup: {e}")
    
    app = web.Application()
    app.router.add_post("/wazzup", wazzup_webhook)
    app.router.add_post("/inventory/special", inventory_special_webhook)
    app.router.add_post("/inventory/regular", inventory_regular_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"✅ Webhook listening on port {WEBHOOK_PORT}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(5)
    logger.info("🤖 Starting Telegram polling...")
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
