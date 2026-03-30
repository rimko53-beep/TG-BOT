import asyncio
import random
import time
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart
from aiogram import BaseMiddleware

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== ДАННЫЕ =====
pairs = [
    "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC",
    "AUD/USD OTC", "USD/CAD OTC", "EUR/GBP OTC",
    "EUR/JPY OTC", "GBP/JPY OTC", "AUD/JPY OTC", "NZD/USD OTC",
    "EUR/AUD OTC", "GBP/AUD OTC", "USD/CHF OTC",
    "CAD/JPY OTC", "NZD/JPY OTC"
]

times = [
    "3 сек", "15 сек", "30 сек",
    "1 мин", "3 мин", "5 мин", "10 мин"
]

user_data = {}
access_users = {ADMIN_ID}  # ✅ админ сразу есть
pending_users = set()

last_signal_time = {}
COOLDOWN_SECONDS = 5


def can_send(user_id: int):
    now = time.time()
    last = last_signal_time.get(user_id, 0)
    if now - last < COOLDOWN_SECONDS:
        return False
    last_signal_time[user_id] = now
    return True


# ===== 🔒 МИДЛВАРЬ ДОСТУПА =====
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user_id = event.from_user.id
            text = event.text or ""

            # ✅ админ всегда имеет доступ
            if user_id == ADMIN_ID:
                return await handler(event, data)

            allowed_buttons = [
                "🔐 Активировать доступ",
                "📩 Отправить ID",
                "/start"
            ]

            if user_id not in access_users and user_id not in pending_users:
                if text not in allowed_buttons:
                    await event.answer(
                        "🔒 <b>Доступ к сигналам закрыт</b>\n\n"
                        "Чтобы пользоваться ботом — сначала получи доступ 👇",
                        parse_mode="HTML"
                    )
                    return

        return await handler(event, data)


dp.message.middleware(AccessMiddleware())

# ===== КНОПКИ =====
menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Выбрать пару")],
        [KeyboardButton(text="⚡ Получить сигнал")],
        [KeyboardButton(text="🔐 Активировать доступ")]
    ],
    resize_keyboard=True
)

access_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📩 Отправить ID")],
        [KeyboardButton(text="⬅️ Назад")]
    ],
    resize_keyboard=True
)

pair_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=p)] for p in pairs] + [[KeyboardButton(text="⬅️ Назад")]],
    resize_keyboard=True
)

time_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=t)] for t in times] + [[KeyboardButton(text="⬅️ Назад")]],
    resize_keyboard=True
)

signal_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⚡ Получить сигнал")],
        [KeyboardButton(text="⬅️ В меню")]
    ],
    resize_keyboard=True
)

# ===== START =====
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "💎 <b>PRO SIGNAL BOT</b>\n\n"
        "📊 OTC сигналы (3с - 10м)\n"
        "⚡ Высокая точность\n"
        "🤖 Авто сигналы 24/7\n\n"
        "🔐 Чтобы начать — активируй доступ",
        reply_markup=menu_kb,
        parse_mode="HTML"
    )

# ===== АКТИВАЦИЯ =====
@dp.message(F.text == "🔐 Активировать доступ")
async def activate(message: Message):
    user_id = message.from_user.id

    if user_id in access_users:
        await message.answer("✅ У тебя уже есть доступ")
        return

    text = (
        "🚀 <b>Чтобы получить доступ к боту:</b>\n\n"
        "1️⃣ Регистрация:\n"
        "https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&ac=tgtraffic\n\n"
        "2️⃣ Пополнение от $50\n\n"
        "3️⃣ После пополнения нажми кнопку ниже и отправь свой ID\n\n"
        "⚠️ Без выполнения условий доступ не выдается"
    )

    await message.answer(text, reply_markup=access_kb, parse_mode="HTML")

# ===== ОТПРАВКА ID =====
@dp.message(F.text == "📩 Отправить ID")
async def send_id_request(message: Message):
    pending_users.add(message.from_user.id)

    await message.answer(
        "📨 Отправь свой ID аккаунта Pocket Option\n\n"
        "⏳ После проверки тебе откроется доступ"
    )

# ===== ПОЛУЧЕНИЕ ID (🔥 ФИКС БЕЗ ЛОМА КНОПОК) =====
@dp.message(lambda msg: msg.from_user.id in pending_users)
async def handle_id(message: Message):
    user_id = message.from_user.id
    user_id_text = message.text

    pending_users.discard(user_id)

    await bot.send_message(
        ADMIN_ID,
        f"🆕 <b>Новый пользователь на проверку</b>\n\n"
        f"👤 @{message.from_user.username}\n"
        f"🆔 TG ID: <code>{user_id}</code>\n"
        f"📊 PO ID: <code>{user_id_text}</code>\n\n"
        f"/give {user_id}",
        parse_mode="HTML"
    )

    await message.answer("⏳ ID отправлен на проверку")

# ===== ВЫДАЧА ДОСТУПА =====
@dp.message(F.text.startswith("/give"))
async def give_access(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        uid = int(message.text.split()[1])
        access_users.add(uid)

        await bot.send_message(uid, "✅ Доступ одобрен! Теперь тебе доступны сигналы 🚀")
        await message.answer("✔️ Выдал доступ")
    except:
        await message.answer("Ошибка")

# ===== МЕНЮ =====
@dp.message(F.text == "📊 Выбрать пару")
async def choose_pair_menu(message: Message):
    if message.from_user.id not in access_users:
        await message.answer("❌ Сначала получи доступ")
        return

    await message.answer("📊 Выбери валютную пару:", reply_markup=pair_kb)

# ===== ВЫБОР ПАРЫ =====
@dp.message(F.text.in_(pairs))
async def choose_pair(message: Message):
    user_data[message.from_user.id] = {"pair": message.text}
    await message.answer("⏱ Выбери время сделки:", reply_markup=time_kb)

# ===== ВЫБОР ВРЕМЕНИ =====
@dp.message(F.text.in_(times))
async def choose_time(message: Message):
    if message.from_user.id not in user_data:
        await message.answer("⚠️ Сначала выбери пару")
        return

    user_data[message.from_user.id]["time"] = message.text
    await message.answer("🔥 Готово! Нажми для сигнала", reply_markup=signal_kb)

# ===== СИГНАЛ =====
def generate_signal(data):
    direction = random.choice(["📈 ВВЕРХ (BUY)", "📉 ВНИЗ (SELL)"])
    accuracy = random.randint(83, 97)

    signal_type = (
        "⚡ СКАЛЬПИНГ СИГНАЛ"
        if "сек" in data['time']
        else "📊 ТРЕНДОВЫЙ СИГНАЛ"
    )

    return f"""
💎 <b>PRO TRADING SIGNAL</b>

{signal_type}

📊 Пара: {data['pair']}
⏱ Тайминг: {data['time']}
📡 Точность: {accuracy}%

🚀 Вход: {direction}

⏳ Открывай сделку СЕЙЧАС
━━━━━━━━━━━━━━━
"""

@dp.message(F.text == "⚡ Получить сигнал")
async def send_signal(message: Message):
    user_id = message.from_user.id

    if user_id not in access_users:
        await message.answer("❌ Нет доступа")
        return

    if not can_send(user_id):
        await message.answer("⏳ Подожди пару секунд")
        return

    data = user_data.get(user_id)

    if not data:
        await message.answer("⚠️ Сначала выбери пару и время")
        return

    await message.answer(generate_signal(data), parse_mode="HTML", reply_markup=signal_kb)

# ===== АВТО СИГНАЛЫ =====
async def auto_signals():
    while True:
        await asyncio.sleep(60)

        for user_id in list(access_users):
            try:
                pair = random.choice(pairs)
                t = random.choice(times)
                text = generate_signal({"pair": pair, "time": t})

                await bot.send_message(user_id, "🤖 <b>АВТО СИГНАЛ</b>\n" + text, parse_mode="HTML")
            except:
                pass

# ===== ФЕЙК АКТИВНОСТЬ =====
async def fake_activity():
    messages = [
        "🔥 Сделка закрыта в плюс +92%",
        "💸 Профит зафиксирован!",
        "🚀 Успешный вход",
        "📈 Серия WIN продолжается",
    ]

    while True:
        await asyncio.sleep(45)

        for user_id in list(access_users):
            try:
                await bot.send_message(user_id, random.choice(messages))
            except:
                pass

# ===== НАВИГАЦИЯ =====
@dp.message(F.text == "⬅️ Назад")
async def back(message: Message):
    await message.answer("🔙 Меню", reply_markup=menu_kb)

@dp.message(F.text == "⬅️ В меню")
async def back_menu(message: Message):
    await message.answer("🏠 Главное меню", reply_markup=menu_kb)

# ===== ЗАПУСК =====
async def main():
    print("🔥 BOT STARTED")

    asyncio.create_task(auto_signals())
    asyncio.create_task(fake_activity())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())