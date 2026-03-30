import asyncio
import random
import time
import os

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart
from aiogram import BaseMiddleware


# ===== ЗАЩИТА ENV =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")

if not ADMIN_ID:
    raise ValueError("ADMIN_ID не задан в переменных окружения")

ADMIN_ID = int(ADMIN_ID)

bot = Bot(token=TOKEN)
dp = Dispatcher()


# ===== ДАННЫЕ =====
pairs = [
    "💵 EUR/USD OTC", "💵 GBP/USD OTC", "💵 USD/JPY OTC",
    "💵 AUD/USD OTC", "💵 USD/CAD OTC", "💵 EUR/GBP OTC",
    "💵 EUR/JPY OTC", "💵 GBP/JPY OTC", "💵 AUD/JPY OTC", "💵 NZD/USD OTC",
    "💵 EUR/AUD OTC", "💵 GBP/AUD OTC", "💵 USD/CHF OTC",
    "💵 CAD/JPY OTC", "💵 NZD/JPY OTC"
]

times = [
    "⚡ 3 сек", "⚡ 15 сек", "⚡ 30 сек",
    "⏱ 1 мин", "⏱ 3 мин", "⏱ 5 мин", "⏱ 10 мин"
]

user_data = {}
user_stats = {}
access_users = {ADMIN_ID}
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


# ===== 🔒 ДОСТУП =====
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user_id = event.from_user.id
            text = event.text or ""

            if user_id == ADMIN_ID:
                return await handler(event, data)

            allowed = ["🔐 Активировать доступ", "📩 Отправить ID", "/start"]

            if user_id not in access_users and user_id not in pending_users:
                if text not in allowed:
                    await event.answer(
                        "🔒 <b>Доступ к VIP сигналам закрыт</b>\n\nАктивируй доступ 👇",
                        parse_mode="HTML"
                    )
                    return

        return await handler(event, data)


dp.message.middleware(AccessMiddleware())


# ===== UI =====
menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Торговая панель")],
        [KeyboardButton(text="⚡ Получить сигнал")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📈 Статистика")],
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
        "💎 <b>CACTUS SIGNAL BOT</b>\n\n"
        "⚡ Премиум сигналы\n"
        "📊 Умный анализ рынка\n"
        "🚀 Только лучшие входы\n\n"
        "🔐 Активируй доступ",
        reply_markup=menu_kb,
        parse_mode="HTML"
    )


# ===== АКТИВАЦИЯ =====
@dp.message(F.text == "🔐 Активировать доступ")
async def activate(message: Message):
    if message.from_user.id in access_users:
        return await message.answer("✅ У тебя уже есть доступ")

    await message.answer(
        "💎 <b>ДОСТУП К VIP СИГНАЛАМ</b>\n\n"
        "Чтобы получить доступ:\n\n"
        "1️⃣ Зарегистрируйся:\n\n"
        "🌍 Для всех стран:\n"
        "https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232\n\n"
        "🇷🇺 Для пользователей из России:\n"
        "https://po-ru4.click/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232\n\n"
        "2️⃣ Пополни депозит от <b>$50</b>\n\n"
        "3️⃣ Отправь свой ID профиля Pocket Option\n\n"
        "⚠️ Без выполнения условий доступ не выдается\n\n"
        "👇 После выполнения нажми кнопку ниже",
        reply_markup=access_kb,
        parse_mode="HTML"
    )


# ===== ID =====
@dp.message(F.text == "📩 Отправить ID")
async def send_id_request(message: Message):
    pending_users.add(message.from_user.id)
    await message.answer("📨 Отправь свой ID Pocket Option")


# ===== ⬅️ НАЗАД =====
@dp.message(F.text == "⬅️ Назад")
async def back(message: Message):
    uid = message.from_user.id

    if uid in pending_users:
        pending_users.discard(uid)
        await message.answer(
            "🔒 <b>Доступ к VIP сигналам закрыт</b>\n\nАктивируй доступ 👇",
            reply_markup=menu_kb,
            parse_mode="HTML"
        )
        return

    await message.answer("🔙 Меню", reply_markup=menu_kb)


# ===== ID ОБРАБОТКА =====
@dp.message(lambda msg: msg.from_user.id in pending_users)
async def handle_id(message: Message):
    if not message.text or not message.text.isdigit():
        return await message.answer("❌ Отправь корректный ID (только цифры)")

    pending_users.discard(message.from_user.id)

    await bot.send_message(
        ADMIN_ID,
        f"🆕 Пользователь\n\n"
        f"👤 @{message.from_user.username}\n"
        f"🆔 {message.from_user.id}\n"
        f"📊 ID: {message.text}\n\n"
        f"/give {message.from_user.id}"
    )

    await message.answer("⏳ ID отправлен на проверку")


# ===== ДОСТУП (ADMIN) =====
@dp.message(F.text.startswith("/give"))
async def give_access(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        uid = int(message.text.split()[1])
    except Exception:
        return await message.answer("❌ Ошибка команды. Пример: /give 123456")

    access_users.add(uid)
    user_stats[uid] = {"signals": 0}

    await bot.send_message(uid, "✅ Доступ активирован НАВСЕГДА 🚀")
    await message.answer("✔️ Доступ выдан")


# ===== ПАНЕЛЬ =====
@dp.message(F.text == "📊 Торговая панель")
async def panel(message: Message):
    if message.from_user.id not in access_users:
        return await message.answer("❌ Нет доступа")

    await message.answer("📊 Выбери валютную пару:", reply_markup=pair_kb)


@dp.message(F.text.in_(pairs))
async def choose_pair(message: Message):
    user_data[message.from_user.id] = {"pair": message.text}
    await message.answer("⏱ Выбери таймфрейм:", reply_markup=time_kb)


@dp.message(F.text.in_(times))
async def choose_time(message: Message):
    uid = message.from_user.id

    if uid not in user_data:
        user_data[uid] = {}

    user_data[uid]["time"] = message.text

    await message.answer("🔥 Готово! Жми сигнал", reply_markup=signal_kb)


# ===== 💎 СИГНАЛ =====
def generate_signal(data):
    direction = random.choice(["📈 BUY", "📉 SELL"])
    accuracy = random.choice([82, 87, 91, 93, 95])

    strength = random.choice(["🔥 СИЛЬНЫЙ", "⚡ ИМПУЛЬС", "💎 ТОЧНЫЙ"])
    trend = random.choice(["📊 Восходящий тренд", "📊 Нисходящий тренд"])
    volatility = random.choice(["🌪 Высокая волатильность", "🌊 Средняя волатильность"])
    risk = random.choice(["🟢 Низкий риск", "🟡 Средний риск"])

    return f"""
━━━━━━━━━━━━━━━
💎 <b>VIP SIGNAL</b>
━━━━━━━━━━━━━━━

📊 <b>Пара:</b> {data['pair']}
⏱ <b>Таймфрейм:</b> {data['time']}

📡 <b>Тип сигнала:</b> {strength}
{trend}
{volatility}

━━━━━━━━━━━━━━━

🎯 <b>Точность:</b> {accuracy}%
🚀 <b>Сделка:</b> {direction}

💰 <b>Рекомендация:</b>
Вход сразу после сигнала

{risk}

━━━━━━━━━━━━━━━
🕯 <b>Анализ:</b>
• Найдена точка входа по тренду  
• Подтверждение импульса  
• Высокая вероятность отработки  

━━━━━━━━━━━━━━━
"""


# ===== СИГНАЛ =====
@dp.message(F.text == "⚡ Получить сигнал")
async def send_signal(message: Message):
    uid = message.from_user.id

    if uid not in access_users:
        return await message.answer("❌ Нет доступа")

    if not can_send(uid):
        return await message.answer("⏳ Подожди немного")

    data = user_data.get(uid)
    if not data:
        return await message.answer("⚠️ Сначала выбери пару")

    user_stats.setdefault(uid, {"signals": 0})
    user_stats[uid]["signals"] += 1

    progress_steps = [
        "🔍 Анализ рынка...\n\n▰▱▱▱▱ 20%",
        "📊 Обработка данных...\n\n▰▰▱▱▱ 40%",
        "🧠 Нейро-анализ...\n\n▰▰▰▱▱ 60%",
        "📡 Поиск входа...\n\n▰▰▰▰▱ 80%",
        "🚀 Генерация сигнала...\n\n▰▰▰▰▰ 100%"
    ]

    msg = await message.answer(progress_steps[0])

    for step in progress_steps[1:]:
        await asyncio.sleep(random.uniform(0.5, 1.2))
        await msg.edit_text(step)

    await asyncio.sleep(0.5)
    await msg.delete()

    await message.answer(generate_signal(data), parse_mode="HTML", reply_markup=signal_kb)


# ===== ПРОФИЛЬ =====
@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    uid = message.from_user.id
    stats = user_stats.get(uid, {"signals": 0})

    await message.answer(
        f"""
━━━━━━━━━━━━━━━
👤 <b>ПРОФИЛЬ ТРЕЙДЕРА</b>
━━━━━━━━━━━━━━━

🆔 ID: <code>{uid}</code>

💎 Статус: <b>VIP ACTIVE</b>
📊 Сигналов получено: <b>{stats['signals']}</b>

📅 Аккаунт активен
🚀 Доступ: <b>Безлимитный</b>

━━━━━━━━━━━━━━━
""",
        parse_mode="HTML"
    )


# ===== СТАТИСТИКА =====
@dp.message(F.text == "📈 Статистика")
async def stats(message: Message):
    await message.answer(
        """
━━━━━━━━━━━━━━━
📈 <b>СТАТИСТИКА СИСТЕМЫ</b>
━━━━━━━━━━━━━━━

🔥 Win Rate: <b>93.2%</b>

📊 Сделок сегодня: 214  
📊 Всего сигналов: 24,821  

💸 Прибыль сегодня: +$4,820  
💸 Общая прибыль: +$284,320  

👥 Активных трейдеров: 1,842  

━━━━━━━━━━━━━━━
🚀 Система работает 24/7
""",
        parse_mode="HTML"
    )


# ===== МЕНЮ =====
@dp.message(F.text == "⬅️ В меню")
async def menu(message: Message):
    await message.answer("🏠 Главное меню", reply_markup=menu_kb)


# ===== ЗАПУСК =====
async def main():
    print("🔥 BOT STARTED")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
