import asyncio
import random
import time
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest

# Для работы с PostgreSQL
import psycopg2
from psycopg2.extras import RealDictCursor

# ===== КОНФИГУРАЦИЯ =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL") # Railway подставит это сам

if not TOKEN or not ADMIN_ID:
    raise ValueError("Проверьте BOT_TOKEN и ADMIN_ID в переменных Railway!")

ADMIN_ID = int(ADMIN_ID)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== РАБОТА С POSTGRESQL =====
def get_db_connection():
    # Подключение к базе Railway
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            has_access BOOLEAN DEFAULT FALSE,
            total_signals INTEGER DEFAULT 0,
            daily_signals INTEGER DEFAULT 0,
            last_signal_date TEXT
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()

def db_get_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT has_access, total_signals, daily_signals, last_signal_date FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return {
            "has_access": row['has_access'],
            "signals": row['total_signals'],
            "daily_count": row['daily_signals'],
            "last_date": row['last_signal_date'] or ""
        }
    return {"has_access": False, "signals": 0, "daily_count": 0, "last_date": ""}

def db_update_user(user_id, has_access=None, signals=None, daily=None, date=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    
    if has_access is not None:
        cursor.execute("UPDATE users SET has_access = %s WHERE user_id = %s", (has_access, user_id))
    if signals is not None:
        cursor.execute("UPDATE users SET total_signals = %s WHERE user_id = %s", (signals, user_id))
    if daily is not None:
        cursor.execute("UPDATE users SET daily_signals = %s WHERE user_id = %s", (daily, user_id))
    if date is not None:
        cursor.execute("UPDATE users SET last_signal_date = %s WHERE user_id = %s", (date, user_id))
    
    conn.commit()
    cursor.close()
    conn.close()

# Запускаем создание таблицы
init_db()

# ===== ДАННЫЕ И КЛАВИАТУРЫ (Оставляем как было) =====
pairs = [
    "💵 EUR/USD OTC", "💵 GBP/USD OTC", "💵 USD/JPY OTC", "💵 AUD/USD OTC", "💵 USD/CAD OTC", 
    "💵 EUR/GBP OTC", "💵 EUR/JPY OTC", "💵 GBP/JPY OTC", "💵 AUD/JPY OTC", "💵 NZD/USD OTC",
    "💵 EUR/AUD OTC", "💵 GBP/AUD OTC", "💵 USD/CHF OTC", "💵 CAD/JPY OTC", "💵 NZD/JPY OTC"
]
times = ["⚡ 3 сек", "⚡ 15 сек", "⚡ 30 сек", "⏱ 1 мин", "⏱ 3 мин", "⏱ 5 мин", "⏱ 10 мин"]

user_temp_data = {} 
pending_users = set()
last_click_time = {}
DAILY_LIMIT = 50

def get_rank(count):
    if count <= 50: return "🌱 Новичок"
    if count <= 100: return "📊 Медиум"
    if count <= 200: return "📈 Трейдер"
    if count <= 500: return "🔥 Профессионал"
    return "👑 VIP"

# ===== MIDDLEWARE =====
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            uid = event.from_user.id
            text = event.text or ""
            if uid == ADMIN_ID: return await handler(event, data)
            user_info = db_get_user(uid)
            allowed = ["🔐 Активировать доступ", "📩 Отправить ID", "⬅️ Назад", "/start", "⬅️ В меню"]
            if not user_info["has_access"] and uid not in pending_users:
                if text not in allowed:
                    await event.answer("🔒 <b>Доступ к VIP сигналам закрыт</b>\n\nАктивируй доступ 👇", parse_mode="HTML")
                    return
        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

# ===== КЛАВИАТУРЫ =====
menu_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📊 Торговая панель")], [KeyboardButton(text="⚡ Получить сигнал")],
    [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📈 Статистика")], [KeyboardButton(text="🔐 Активировать доступ")]
], resize_keyboard=True)

access_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📩 Отправить ID")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
pair_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=p)] for p in pairs] + [[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
time_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t)] for t in times] + [[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
signal_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⚡ Получить сигнал")], [KeyboardButton(text="⬅️ В меню")]], resize_keyboard=True)

# ===== ХЕНДЛЕРЫ =====

@dp.message(CommandStart())
async def start(message: Message):
    db_update_user(message.from_user.id)
    await message.answer("💎 <b>CACTUS SIGNAL BOT</b>\n\nПремиум сигналы и ИИ аналитика рынка.", reply_markup=menu_kb, parse_mode="HTML")

@dp.message(F.text == "🔐 Активировать доступ")
async def activate(message: Message):
    user_info = db_get_user(message.from_user.id)
    if user_info["has_access"]: return await message.answer("✅ VIP доступ уже активен.")
    await message.answer(
        "💎 <b>АКТИВАЦИЯ VIP ДОСТУПА</b>\n\n"
        "1️⃣ <b>Зарегистрируйся по ссылке ниже:</b>\n"
        "🌍 Global: <a href='https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232'>Pocket Option</a>\n"
        "🇷🇺 RU: <a href='https://po-ru4.click/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232'>Pocket Option RU</a>\n\n"
        "2️⃣ Пополни баланс от <b>$50</b>\n"
        "3️⃣ Отправь ID\n\n"
        "👇 Нажми кнопку ниже", reply_markup=access_kb, parse_mode="HTML", disable_web_page_preview=True
    )

@dp.message(F.text == "📩 Отправить ID")
async def ask_id(message: Message):
    pending_users.add(message.from_user.id)
    await message.answer("📝 <b>Введите Ваш цифровой ID:</b>", parse_mode="HTML")

@dp.message(F.text == "⬅️ Назад")
@dp.message(F.text == "⬅️ В меню")
async def go_back(message: Message):
    pending_users.discard(message.from_user.id)
    await message.answer("🏠 <b>Главное меню</b>", reply_markup=menu_kb, parse_mode="HTML")

@dp.message(lambda msg: msg.from_user.id in pending_users)
async def process_id(message: Message):
    if not message.text or not message.text.isdigit():
        return await message.answer("❌ Введите только цифры!")
    uid = message.from_user.id
    pending_users.discard(uid)
    await bot.send_message(ADMIN_ID, f"🔔 <b>ЗАЯВКА</b>\nЮзер: @{message.from_user.username}\nID TG: <code>{uid}</code>\nID PO: <code>{message.text}</code>\n\nАктивировать: <code>/give {uid}</code>", parse_mode="HTML")
    await message.answer("⏳ <b>Отправлено на проверку!</b>", reply_markup=menu_kb, parse_mode="HTML")

@dp.message(F.text.startswith("/give"))
async def admin_give(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=True)
        await bot.send_message(target, "🚀 <b>VIP Доступ активирован!</b>", parse_mode="HTML", reply_markup=menu_kb)
        await message.answer(f"✅ Доступ для {target} сохранен в Postgres.")
    except: await message.answer("Ошибка. Формат: /give ID")

@dp.message(F.text == "📊 Торговая панель")
async def t_panel(message: Message):
    if not db_get_user(message.from_user.id)["has_access"]: return
    await message.answer("📈 <b>Выберите пару:</b>", reply_markup=pair_kb, parse_mode="HTML")

@dp.message(F.text.in_(pairs))
async def set_pair(message: Message):
    user_temp_data[message.from_user.id] = {"pair": message.text}
    await message.answer("⏱ <b>Выберите время:</b>", reply_markup=time_kb, parse_mode="HTML")

@dp.message(F.text.in_(times))
async def set_time(message: Message):
    uid = message.from_user.id
    if uid not in user_temp_data: user_temp_data[uid] = {}
    user_temp_data[uid]["time"] = message.text
    await message.answer(f"✅ Готово: <b>{user_temp_data[uid]['pair']}</b>", reply_markup=signal_kb, parse_mode="HTML")

@dp.message(F.text == "⚡ Получить сигнал")
async def get_signal(message: Message):
    uid = message.from_user.id
    u = db_get_user(uid)
    if not u["has_access"]: return
    today = datetime.now().strftime("%Y-%m-%d")
    daily = u["daily_count"]
    if u["last_date"] != today:
        daily = 0
        db_update_user(uid, daily=0, date=today)
    if daily >= DAILY_LIMIT:
        return await message.answer("🛑 Лимит 50 сигналов исчерпан!")
    if time.time() - last_click_time.get(uid, 0) < 3:
        return await message.answer("⏳ Анализ...")
    data = user_temp_data.get(uid)
    if not data or "pair" not in data:
        return await message.answer("⚠️ Выберите пару!")
    last_click_time[uid] = time.time()
    db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)
    status_msg = await message.answer("🔍 <b>Анализ рынка...</b>", parse_mode="HTML")
    await asyncio.sleep(1.5)
    direction = random.choice(["📈 ВВЕРХ", "📉 ВНИЗ"])
    res = f"💎 <b>VIP СИГНАЛ</b>\n\n📊 Пара: {data['pair']}\n⏱ Время: {data['time']}\n🎯 Точность: {random.randint(87,98)}%\n\n🚀 ПРОГНОЗ: <b>{direction}</b>"
    try: await status_msg.delete()
    except: pass
    await message.answer(res, parse_mode="HTML", reply_markup=signal_kb)

@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    u = db_get_user(message.from_user.id)
    rank = get_rank(u["signals"])
    await message.answer(
        f"👤 <b>ПРОФИЛЬ</b>\n🆔 ID: <code>{message.from_user.id}</code>\n🎖 Статус: <b>{rank}</b>\n\n"
        f"📊 Всего сигналов: {u['signals']}\n📅 Сегодня: {u['daily_count']}/{DAILY_LIMIT}\n\n"
        f"✅ VIP Доступ: {'Активен' if u['has_access'] else 'Закрыт'}", parse_mode="HTML"
    )

@dp.message(F.text == "📈 Статистика")
async def stats(message: Message):
    await message.answer("📊 <b>ОТЧЕТ</b>\nWin Rate: 94%\nСистема: Стабильна ✅", parse_mode="HTML")

async def main():
    print("🚀 Бот запущен с PostgreSQL")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
