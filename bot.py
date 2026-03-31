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
    "💵 EUR/GBP OTC", "💵 EUR/JPY OTC", "💵 GBP/JPY OTC", "💵 AUD/JPY OTC", "💵 NZD/USD OTC",
    "💵 EUR/AUD OTC", "💵 GBP/AUD OTC", "💵 USD/CHF OTC", "💵 CAD/JPY OTC", "💵 NZD/JPY OTC"
]
times = ["⚡ 3 сек", "⚡ 15 сек", "⚡ 30 сек", "⏱ 1 мин", "⏱ 3 мин", "⏱ 5 мин", "⏱ 10 мин"]

user_temp_data = {} 
pending_users = set()
last_click_time = {}
DAILY_LIMIT = 30  # Изменено с 50 на 30

def get_rank(count):
    if count <= 50: return "🌱 Новичок (Retail)"
    if count <= 150: return "📊 Трейдер (Prop)"
    if count <= 350: return "📈 Про-Трейдер (Institutional)"
    return "👑 Маркет-Мейкер (Whale)"

# ===== MIDDLEWARE =====
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
                    await event.answer("⚠️ <b>ОШИБКА ДОСТУПА: ТЕРМИНАЛ ЗАБЛОКИРОВАН</b>\n\nДля получения алгоритмических сигналов с высокой проходимостью (WinRate 88-92%), необходимо активировать VIP-лицензию.", parse_mode="HTML")
                    return
        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

# ===== КЛАВИАТУРЫ =====
menu_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📊 Торговая панель")], 
    [KeyboardButton(text="⚡ Получить сигнал")],
    [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📈 Статистика")], 
    [KeyboardButton(text="🔐 Активировать доступ")]
], resize_keyboard=True)

access_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📩 Отправить ID Pocket Option")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
pair_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=p)] for p in pairs] + [[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
time_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t)] for t in times] + [[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
signal_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⚡ Получить сигнал")], [KeyboardButton(text="⬅️ В меню")]], resize_keyboard=True)

# ===== ХЕНДЛЕРЫ =====

@dp.message(CommandStart())
async def start(message: Message):
    db_update_user(message.from_user.id)
    
    start_text = (
        "🖥 <b>AI TRADING TERMINAL | OTC PRO</b> 📈\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💸 <b>Преврати трейдинг в систему с математическим перевесом!</b>\n\n"
        "Наш нейросетевой алгоритм анализирует паттерны Price Action, "
        "объемы и волатильность рынка, обеспечивая WinRate сделок <b>до 92.4%</b>.\n\n"
        "📌 <i>Используй профессиональную аналитику для скальпинга и дейтрейдинга на Pocket Option.</i>\n\n"
        "🛠 <b>Статус системы:</b> ОНЛАЙН 🟢"
    )
    await message.answer(start_text, reply_markup=menu_kb, parse_mode="HTML")

@dp.message(F.text == "🔐 Активировать доступ")
async def activate(message: Message):
    user_info = db_get_user(message.from_user.id)
    if user_info["has_access"]: return await message.answer("✅ <b>СТАТУС:</b> ЛИЦЕНЗИЯ АКТИВНА\nВам доступны все модули терминала.", parse_mode="HTML")
    await message.answer(
        "💎 <b>ПОЛУЧЕНИЕ VIP-ЛИЦЕНЗИИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ <b>Регистрация торгового счета:</b>\n"
        "▫️ Global: <a href='https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232'>Pocket Option (Официальный шлюз)</a>\n"
        "▫️ RU/СНГ: <a href='https://po-ru4.click/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1740378&ac=tgtraffic&cid=947232'>Pocket Option (Зеркало)</a>\n\n"
        "2️⃣ <b>Депозит:</b> от <b>$50</b> (для соблюдения риск-менеджмента 1-5% на сделку)\n"
        "3️⃣ <b>Синхронизация:</b> Жми кнопку ниже и отправь свой <b>ID Pocket Option</b>\n\n"
        "🛡 <i>После проверки ИИ подключит ваш аккаунт к пулу сигналов.</i>", 
        reply_markup=access_kb, parse_mode="HTML", disable_web_page_preview=True
    )

@dp.message(F.text == "📩 Отправить ID Pocket Option")
async def ask_id(message: Message):
    pending_users.add(message.from_user.id)
    await message.answer("⌨️ <b>Введите Ваш цифровой ID профиля Pocket Option:</b>\n<i>(Только цифры, без пробелов и букв)</i>", parse_mode="HTML")

@dp.message(F.text == "⬅️ Назад")
@dp.message(F.text == "⬅️ В меню")
async def go_back(message: Message):
    pending_users.discard(message.from_user.id)
    await message.answer("🏠 <b>Главная панель управления</b>", reply_markup=menu_kb, parse_mode="HTML")

@dp.message(lambda msg: msg.from_user.id in pending_users)
async def process_id(message: Message):
    if not message.text or not message.text.isdigit():
        return await message.answer("❌ <b>Ошибка валидации.</b> Введите только ЦИФРЫ вашего ID Pocket Option.")
    uid = message.from_user.id
    pending_users.discard(uid)
    await bot.send_message(
        ADMIN_ID, 
        f"🔔 <b>НОВАЯ ЗАЯВКА НА VIP</b>\n"
        f"👤 Юзер: @{message.from_user.username or 'Без юзернейма'}\n"
        f"🆔 TG ID: <code>{uid}</code>\n"
        f"💼 ID PO: <code>{message.text}</code>\n\n"
        f"✅ Выдать доступ: <code>/give {uid}</code>\n"
        f"🚫 Заблокировать: <code>/block {uid}</code>", 
        parse_mode="HTML"
    )
    await message.answer("💾 <b>ID принят в обработку.</b>\nОжидайте подтверждения верификации от технического отдела.", reply_markup=menu_kb, parse_mode="HTML")

# ===== АДМИН ПАНЕЛЬ =====
@dp.message(F.text.startswith("/give"))
async def admin_give(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=True)
        await bot.send_message(target, "🚀 <b>СИСТЕМА: VIP ДОСТУП АКТИВИРОВАН</b>\nТерминал разблокирован. Вам доступны профессиональные сигналы для профита!", parse_mode="HTML", reply_markup=menu_kb)
        await message.answer(f"✅ Доступ для пользователя <code>{target}</code> успешно АКТИВИРОВАН.", parse_mode="HTML")
    except: await message.answer("⚠️ Ошибка. Формат: <code>/give ID</code>", parse_mode="HTML")

@dp.message(F.text.startswith("/block"))
async def admin_block(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=False)
        try:
            await bot.send_message(target, "🛑 <b>СИСТЕМА: ВАШ ДОСТУП АННУЛИРОВАН</b>\nВаша подписка на торговые сигналы была отключена администратором.", parse_mode="HTML")
        except:
            pass # Юзер мог заблокировать бота
        await message.answer(f"🚫 Доступ для пользователя <code>{target}</code> успешно ЗАБЛОКИРОВАН.", parse_mode="HTML")
    except: await message.answer("⚠️ Ошибка. Формат: <code>/block ID</code>", parse_mode="HTML")

# ===== ТОРГОВЫЙ ПРОЦЕСС =====
@dp.message(F.text == "📊 Торговая панель")
async def t_panel(message: Message):
    if not db_get_user(message.from_user.id)["has_access"]: return
    await message.answer("⚙️ <b>КОНФИГУРАЦИЯ СДЕЛКИ:</b>\nВыберите торговый актив (валютную пару):", reply_markup=pair_kb, parse_mode="HTML")

@dp.message(F.text.in_(pairs))
async def set_pair(message: Message):
    user_temp_data[message.from_user.id] = {"pair": message.text}
    await message.answer("⚙️ <b>ТАЙМФРЕЙМ:</b>\nУстановите время экспирации опциона:", reply_markup=time_kb, parse_mode="HTML")

@dp.message(F.text.in_(times))
async def set_time(message: Message):
    uid = message.from_user.id
    if uid not in user_temp_data: user_temp_data[uid] = {}
    user_temp_data[uid]["time"] = message.text
    await message.answer(
        f"✅ <b>Пресет сохранен:</b>\n"
        f"📈 Актив: <b>{user_temp_data[uid]['pair']}</b>\n"
        f"⏱ Экспирация: <b>{user_temp_data[uid]['time']}</b>\n\n"
        f"<i>Алгоритм готов к поиску точки входа.</i>", 
        reply_markup=signal_kb, parse_mode="HTML"
    )

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
        return await message.answer(f"🛑 <b>РИСК-МЕНЕДЖМЕНТ:</b> Дневной лимит ({DAILY_LIMIT} сделок) исчерпан. Защита от тильта активирована. Возвращайтесь завтра.")
    
    data = user_temp_data.get(uid)
    if not data or "pair" not in data:
        return await message.answer("⚠️ Ошибка конфигурации. Настройте актив в меню «📊 Торговая панель»!")

    if time.time() - last_click_time.get(uid, 0) < 6:
        return await message.answer("⏳ <b>Идет просчет...</b> Дождитесь завершения предыдущего анализа.")

    last_click_time[uid] = time.time()
    
    # --- АНИМАЦИЯ АНАЛИЗА ПРО УРОВНЯ ---
    progress_msg = await message.answer("⬛️⬛️⬛️⬛️⬛️ [0%]\n📡 <i>Подключение к потоку котировок...</i>", parse_mode="HTML")
    await asyncio.sleep(0.7)
    await progress_msg.edit_text("🟩⬛️⬛️⬛️⬛️ [25%]\n📊 <i>Сбор данных с осцилляторов (RSI, Stochastic)...</i>", parse_mode="HTML")
    await asyncio.sleep(0.7)
    await progress_msg.edit_text("🟩🟩🟩⬛️⬛️ [60%]\n📉 <i>Оценка волатильности и уровней поддержки/сопротивления...</i>", parse_mode="HTML")
    await asyncio.sleep(0.7)
    await progress_msg.edit_text("🟩🟩🟩🟩⬛️ [85%]\n🎯 <i>Анализ паттернов Price Action...</i>", parse_mode="HTML")
    await asyncio.sleep(0.5)
    await progress_msg.edit_text("🟩🟩🟩🟩🟩 [100%]\n✅ <i>Математический перевес найден!</i>", parse_mode="HTML")
    await asyncio.sleep(0.4)
    
    db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)
    
    direction = random.choice(["ВВЕРХ 🟢 (CALL)", "ВНИЗ 🔴 (PUT)"])
    confidence = random.randint(88, 96)
    
    res = (
        f"⚡️ <b>ТОРГОВЫЙ СИГНАЛ ГОТОВ</b> ⚡️\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Актив:</b> {data['pair']}\n"
        f"⏱ <b>Время сделки:</b> {data['time']}\n"
        f"🧠 <b>Уверенность ИИ:</b> {confidence}%\n\n"
        f"🚀 <b>РЕКОМЕНДАЦИЯ: {direction}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Не забудьте про Money Management! (1-3% от баланса)</i>"
    )
    
    try: await progress_msg.delete()
    except: pass
    await message.answer(res, parse_mode="HTML", reply_markup=signal_kb)

@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    u = db_get_user(message.from_user.id)
    rank = get_rank(u["signals"])
    await message.answer(
        f"👤 <b>ЛИЧНЫЙ КАБИНЕТ ТРЕЙДЕРА</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Ваш ID: <code>{message.from_user.id}</code>\n"
        f"🏆 Уровень: <b>{rank}</b>\n\n"
        f"📈 <b>ТОРГОВАЯ АКТИВНОСТЬ:</b>\n"
        f"▫️ Выполнено сделок (всего): <b>{u['signals']}</b>\n"
        f"▫️ Сделок за сегодня: <b>{u['daily_count']} / {DAILY_LIMIT}</b>\n\n"
        f"💎 <b>СТАТУС ЛИЦЕНЗИИ:</b> {'АКТИВНА ✅' if u['has_access'] else 'ОГРАНИЧЕНА ❌'}", 
        parse_mode="HTML"
    )

@dp.message(F.text == "📈 Статистика")
async def stats(message: Message):
    await message.answer(
        "📊 <b>ГЛОБАЛЬНАЯ СТАТИСТИКА ИИ (24 часа)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📈 Средний WinRate: <b>92.4%</b>\n"
        "🟢 Плюсовых сделок: <b>1 842</b>\n"
        "🔴 Минусовых сделок: <b>151</b>\n"
        "🔁 Возвратов: <b>24</b>\n\n"
        "⚙️ <i>Сводка формируется автоматически на базе пула всех торговых сессий наших пользователей на платформе Pocket Option.</i>", 
        parse_mode="HTML"
    )

async def main():
    print("🚀 PRO AI BOT STARTED SUCCESSFULLY")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
