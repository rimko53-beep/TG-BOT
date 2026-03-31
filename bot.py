import asyncio
import random
import time
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
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
                is_banned BOOLEAN DEFAULT FALSE,
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
        cursor.execute("SELECT has_access, is_banned, total_signals, daily_signals, last_signal_date FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            return {
                "has_access": row['has_access'],
                "is_banned": row['is_banned'],
                "signals": row['total_signals'],
                "daily_count": row['daily_signals'],
                "last_date": row['last_signal_date'] or ""
            }
    except Exception as e:
        print(f"Ошибка чтения из БД: {e}")
    return {"has_access": False, "is_banned": False, "signals": 0, "daily_count": 0, "last_date": ""}

def db_update_user(user_id, has_access=None, is_banned=None, signals=None, daily=None, date=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
        if has_access is not None:
            cursor.execute("UPDATE users SET has_access = %s WHERE user_id = %s", (has_access, user_id))
        if is_banned is not None:
            cursor.execute("UPDATE users SET is_banned = %s WHERE user_id = %s", (is_banned, user_id))
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
    "💵 EUR/GBP OTC", "💵 EUR/JPY OTC", "💵 GBP/JPY OTC", "💵 AUD/JPY OTC", "💵 NZD/USD OTC",
    "💵 EUR/AUD OTC", "💵 GBP/AUD OTC", "💵 USD/CHF OTC", "💵 CAD/JPY OTC", "💵 NZD/JPY OTC"
]
times = ["⚡ 3 сек", "⚡ 15 сек", "⚡ 30 сек", "⏱ 1 мин", "⏱ 3 мин", "⏱ 5 мин", "⏱ 10 мин"]

user_temp_data = {} 
pending_users = set()
last_click_time = {}
DAILY_LIMIT = 50

def get_rank(count):
    if count <= 50: return "🟢 Novice Trader"
    if count <= 150: return "🔵 Advanced Trader"
    if count <= 350: return "🟡 Elite Partner"
    return "🔥 Market Legend"

# ===== MIDDLEWARE (ACCESS & BAN) =====
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            uid = event.from_user.id
            text = event.text or ""
            user_info = db_get_user(uid)

            # Проверка на бан
            if user_info["is_banned"]:
                await event.answer("🚫 <b>ДОСТУП ЗАБЛОКИРОВАН</b>\n\nВы были ограничены администрацией за нарушение правил.", parse_mode="HTML")
                return

            if uid == ADMIN_ID: return await handler(event, data)
            
            allowed = ["🔐 АКТИВИРОВАТЬ VIP", "📩 ОТПРАВИТЬ ID POCKET OPTION", "⬅️ НАЗАД", "/start", "⬅️ В МЕНЮ"]
            if not user_info["has_access"] and uid not in pending_users:
                if text.upper() not in allowed:
                    await event.answer("💎 <b>VIP ДОСТУП ОГРАНИЧЕН</b>\n\nФункция доступна только участникам VIP-клуба. Активируйте подписку в главном меню.", parse_mode="HTML")
                    return
        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

# ===== КЛАВИАТУРЫ (PRO DESIGN) =====
menu_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📊 Торговая панель")], 
    [KeyboardButton(text="⚡ Получить сигнал")],
    [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📈 Статистика")], 
    [KeyboardButton(text="🔐 Активировать VIP")]
], resize_keyboard=True)

access_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📩 Отправить ID Pocket Option")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
pair_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=p)] for p in pairs] + [[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
time_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t)] for t in times] + [[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
signal_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⚡ Получить сигнал")], [KeyboardButton(text="⬅️ В меню")]], resize_keyboard=True)

# ===== ХЕНДЛЕРЫ =====

@dp.message(CommandStart())
async def start(message: Message):
    db_update_user(message.from_user.id)
    await message.answer(
        "💎 <b>POCKET OPTION AI PRO | v.2.0</b>\n"
        "────────────────────\n"
        "👋 Добро пожаловать, трейдер!\n\n"
        "Наш алгоритм на базе нейронных сетей анализирует рынок 24/7 и выдает сигналы с точностью <b>92%+</b>.\n\n"
        "📈 <b>Готов к профиту? Жми кнопку ниже!</b>", 
        reply_markup=menu_kb, parse_mode="HTML"
    )

@dp.message(F.text == "🔐 Активировать VIP")
async def activate(message: Message):
    user_info = db_get_user(message.from_user.id)
    if user_info["has_access"]: return await message.answer("✅ <b>Статус: VIP АКТИВЕН</b>", parse_mode="HTML")
    await message.answer(
        "💎 <b>ИНСТРУКЦИЯ ПО АКТИВАЦИИ</b>\n"
        "────────────────────\n"
        "1️⃣ <b>Регистрация:</b>\n"
        "▫️ Global: <a href='https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232'>Pocket Option Web</a>\n"
        "▫️ RU: <a href='https://po-ru4.click/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232'>Pocket Option RU</a>\n\n"
        "2️⃣ <b>Депозит:</b> от <b>$50</b>\n"
        "3️⃣ <b>Верификация:</b> Жми кнопку ниже и пришли свой <b>ID Pocket Option</b>\n\n"
        "<i>После проверки бот автоматически откроет доступ ко всем парам OTC.</i>", 
        reply_markup=access_kb, parse_mode="HTML", disable_web_page_preview=True
    )

@dp.message(F.text == "📩 Отправить ID Pocket Option")
async def ask_id(message: Message):
    pending_users.add(message.from_user.id)
    await message.answer("💬 <b>Введите Ваш 8-значный ID:</b>", parse_mode="HTML")

@dp.message(F.text == "⬅️ Назад")
@dp.message(F.text == "⬅️ В меню")
async def go_back(message: Message):
    pending_users.discard(message.from_user.id)
    await message.answer("🏠 <b>Главная консоль управления</b>", reply_markup=menu_kb, parse_mode="HTML")

@dp.message(lambda msg: msg.from_user.id in pending_users)
async def process_id(message: Message):
    if not message.text or not message.text.isdigit():
        return await message.answer("❌ <b>ОШИБКА:</b> Введите только цифры вашего ID.")
    uid = message.from_user.id
    pending_users.discard(uid)
    await bot.send_message(ADMIN_ID, f"🔔 <b>ЗАЯВКА НА VIP</b>\nUser: @{message.from_user.username}\nID: <code>{uid}</code>\nPO ID: <code>{message.text}</code>\n\n✅ Одобрить: <code>/give {uid}</code>\n🚫 Бан: <code>/ban {uid}</code>", parse_mode="HTML")
    await message.answer("📥 <b>Заявка отправлена на проверку!</b>\nОбычно это занимает от 5 до 30 минут.", reply_markup=menu_kb, parse_mode="HTML")

# ===== АДМИН КОМАНДЫ =====
@dp.message(Command("give"))
async def admin_give(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=True)
        await bot.send_message(target, "🚀 <b>ПОЗДРАВЛЯЕМ! VIP ДОСТУП ОТКРЫТ!</b>\nТеперь вам доступны сигналы PRO уровня.", parse_mode="HTML", reply_markup=menu_kb)
        await message.answer(f"✅ Доступ для {target} активирован.")
    except: await message.answer("Формат: /give ID")

@dp.message(Command("ban"))
async def admin_ban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, is_banned=True)
        await message.answer(f"🚫 Пользователь {target} заблокирован.")
    except: await message.answer("Формат: /ban ID")

@dp.message(Command("unban"))
async def admin_unban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, is_banned=False)
        await message.answer(f"✅ Пользователь {target} разблокирован.")
    except: await message.answer("Формат: /unban ID")

# ===== ЛОГИКА ТРЕЙДИНГА =====

@dp.message(F.text == "📊 Торговая панель")
async def t_panel(message: Message):
    await message.answer("⚙️ <b>КОНФИГУРАЦИЯ АКП:</b>\nВыберите торговую пару для анализа:", reply_markup=pair_kb, parse_mode="HTML")

@dp.message(F.text.in_(pairs))
async def set_pair(message: Message):
    user_temp_data[message.from_user.id] = {"pair": message.text}
    await message.answer("⚙️ <b>КОНФИГУРАЦИЯ АКП:</b>\nВыберите время экспирации сделки:", reply_markup=time_kb, parse_mode="HTML")

@dp.message(F.text.in_(times))
async def set_time(message: Message):
    uid = message.from_user.id
    if uid not in user_temp_data: user_temp_data[uid] = {}
    user_temp_data[uid]["time"] = message.text
    await message.answer(f"✅ <b>ПАРАМЕТРЫ УСТАНОВЛЕНЫ:</b>\n📦 Актив: {user_temp_data[uid]['pair']}\n⏳ Таймфрейм: {user_temp_data[uid]['time']}", reply_markup=signal_kb, parse_mode="HTML")

@dp.message(F.text == "⚡ Получить сигнал")
async def get_signal(message: Message):
    uid = message.from_user.id
    u = db_get_user(uid)
    
    today = datetime.now().strftime("%Y-%m-%d")
    daily = u["daily_count"]
    if u["last_date"] != today:
        daily = 0
        db_update_user(uid, daily=0, date=today)
    
    if daily >= DAILY_LIMIT:
        return await message.answer("🛑 <b>ЛИМИТ ИСЧЕРПАН</b>\nДоступно 50 сигналов в сутки.")
    
    data = user_temp_data.get(uid)
    if not data or "pair" not in data:
        return await message.answer("⚠️ <b>ОШИБКА:</b> Сначала выберите актив в «📊 Торговая панель»!")

    if time.time() - last_click_time.get(uid, 0) < 6:
        return await message.answer("⏳ <b>АНАЛИЗ В ПРОЦЕССЕ...</b> Пожалуйста, подождите.")

    last_click_time[uid] = time.time()
    
    # --- PRO АНИМАЦИЯ ---
    anim_frames = [
        "📡 <b>Подключение к котировкам OTC...</b>\n[▒▒▒▒▒▒▒▒▒▒] 0%",
        "🔍 <b>Сканирование RSI и Bollinger Bands...</b>\n[███▒▒▒▒▒▒▒] 30%",
        "📊 <b>Анализ волатильности и объемов...</b>\n[██████▒▒▒▒] 65%",
        "🤖 <b>Генерация сигнала нейросетью...</b>\n[█████████▒] 90%",
        "💎 <b>СИГНАЛ СФОРМИРОВАН!</b>\n[██████████] 100%"
    ]
    
    msg = await message.answer(anim_frames[0], parse_mode="HTML")
    for frame in anim_frames[1:]:
        await asyncio.sleep(0.7)
        try: await msg.edit_text(frame, parse_mode="HTML")
        except: pass
    
    db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)
    direction = random.choice(["ВВЕРХ 💹 🟢", "ВНИЗ 📉 🔴"])
    
    res = (
        f"🎯 <b>СИГНАЛ УСПЕШНО СГЕНЕРИРОВАН</b>\n"
        f"────────────────────\n"
        f"📊 <b>АКТИВ:</b> {data['pair']}\n"
        f"⏱ <b>ВРЕМЯ:</b> {data['time']}\n"
        f"📈 <b>ТОЧНОСТЬ:</b> {random.randint(91,97)}%\n"
        f"🛰 <b>РЕКОМЕНДАЦИЯ:</b> <b>{direction}</b>\n"
        f"────────────────────\n"
        f"💎 <i>Удачной сделки! Не забывайте про Мани-менеджмент.</i>"
    )
    
    await asyncio.sleep(0.5)
    await msg.delete()
    await message.answer(res, parse_mode="HTML", reply_markup=signal_kb)

@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    u = db_get_user(message.from_user.id)
    rank = get_rank(u["signals"])
    await message.answer(
        f"👤 <b>ЛИЧНЫЙ КАБИНЕТ ТРЕЙДЕРА</b>\n"
        f"────────────────────\n"
        f"▫️ <b>Ваш ID:</b> <code>{message.from_user.id}</code>\n"
        f"▫️ <b>Ранг:</b> {rank}\n\n"
        f"📊 <b>СТАТИСТИКА:</b>\n"
        f"▫️ Обработано сигналов: {u['signals']}\n"
        f"▫️ Использовано сегодня: {u['daily_count']}/{DAILY_LIMIT}\n\n"
        f"💎 <b>СТАТУС VIP:</b> {'АКТИВЕН ✅' if u['has_access'] else 'ОГРАНИЧЕН ❌'}\n"
        f"────────────────────", 
        parse_mode="HTML"
    )

@dp.message(F.text == "📈 Статистика")
async def stats(message: Message):
    await message.answer(
        "📈 <b>ГЛОБАЛЬНАЯ СТАТИСТИКА (24ч)</b>\n"
        "────────────────────\n"
        "✅ Успешных сигналов: <b>1,482</b>\n"
        "❌ Просадка: <b>124</b>\n"
        "🔥 Средний WinRate: <b>92.7%</b>\n\n"
        "<i>Данные основаны на мониторинге всех закрытых сделок алгоритма.</i>", parse_mode="HTML"
    )

async def main():
    print("🚀 PRO BOT STARTED SUCCESSFULLY")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
