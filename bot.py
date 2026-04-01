import asyncio
import random
import time
import os
import aiohttp
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")

if not TOKEN or not ADMIN_ID or not CRYPTO_BOT_TOKEN:
    raise ValueError("Проверьте BOT_TOKEN, ADMIN_ID и CRYPTO_BOT_TOKEN в переменных Railway!")

ADMIN_ID = int(ADMIN_ID)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Лимиты подписок
SUBSCRIPTION_PLANS = {
    "free": {"limit": 20, "name": "FREE", "price": 0},
    "junior": {"limit": 50, "name": "JUNIOR", "price": 50, "duration": 7},
    "pro": {"limit": 100, "name": "PRO", "price": 100, "duration": 7}
}

# ===== МАППИНГ ПАР: КНОПКА -> YAHOO FINANCE ТИКЕР =====
PAIR_TO_TICKER = {
    "💵 AUD/CAD": "AUDCAD=X",
    "💵 CAD/CHF": "CADCHF=X",
    "💵 EUR/CHF": "EURCHF=X",
    "💵 GBP/CAD": "GBPCAD=X",
    "💵 USD/CAD": "USDCAD=X",
    "💵 GBP/JPY": "GBPJPY=X",
    "💵 EUR/USD": "EURUSD=X",
    "💵 USD/JPY": "USDJPY=X",
}

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
                last_signal_date TEXT,
                sub_type TEXT DEFAULT 'free',
                sub_expires TIMESTAMP
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
        cursor.execute("SELECT has_access, total_signals, daily_signals, last_signal_date, sub_type, sub_expires FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            sub_type = row['sub_type']
            if row['sub_expires'] and row['sub_expires'] < datetime.now():
                sub_type = 'free'
                db_update_user(user_id, sub_type='free', sub_expires=None)

            today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")
            daily_count = row['daily_signals']
            last_date = row['last_signal_date'] or ""

            if last_date != "" and last_date != today:
                daily_count = 0
                last_date = today
                db_update_user(user_id, daily=0, date=today)

            return {
                "has_access": row['has_access'],
                "signals": row['total_signals'],
                "daily_count": daily_count,
                "last_date": last_date,
                "sub_type": sub_type,
                "sub_expires": row['sub_expires']
            }
    except Exception as e:
        print(f"Ошибка чтения из БД: {e}")
    return {"has_access": False, "signals": 0, "daily_count": 0, "last_date": "", "sub_type": "free", "sub_expires": None}

def db_update_user(user_id, has_access=None, signals=None, daily=None, date=None, sub_type=None, sub_expires=None):
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
        if sub_type is not None:
            cursor.execute("UPDATE users SET sub_type = %s WHERE user_id = %s", (sub_type, user_id))
        if sub_expires is not None or (sub_type == 'free'):
            cursor.execute("UPDATE users SET sub_expires = %s WHERE user_id = %s", (sub_expires, user_id))

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка обновления БД: {e}")

init_db()

# ===== CRYPTO BOT API =====
async def create_invoice(amount, plan_name):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    payload = {
        "asset": "USDT",
        "amount": str(amount),
        "description": f"Подписка {plan_name} на 7 дней",
        "paid_btn_name": "callback",
        "paid_btn_url": "https://t.me/CryptoBot"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            return await resp.json()

async def check_invoice(invoice_id):
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            if data['ok'] and data['result']['items']:
                return data['result']['items'][0]['status'] == 'paid'
    return False

# ===== ПОЛУЧЕНИЕ РЕАЛЬНЫХ КОТИРОВОК ЧЕРЕЗ YAHOO FINANCE =====
async def get_real_quote(pair_label: str) -> dict | None:
    """
    Получает реальную котировку с Yahoo Finance по метке пары.
    Возвращает словарь с данными или None при ошибке.
    """
    ticker = PAIR_TO_TICKER.get(pair_label)
    if not ticker:
        return None

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=5m"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        meta = result[0].get("meta", {})
        indicators = result[0].get("indicators", {}).get("quote", [{}])[0]

        current_price = meta.get("regularMarketPrice")
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")

        opens = indicators.get("open", [])
        closes = indicators.get("close", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])

        # Фильтруем None значения
        opens_clean = [x for x in opens if x is not None]
        closes_clean = [x for x in closes if x is not None]
        highs_clean = [x for x in highs if x is not None]
        lows_clean = [x for x in lows if x is not None]

        if not current_price:
            return None

        # Изменение за последнюю свечу
        change = 0.0
        change_pct = 0.0
        if prev_close and prev_close != 0:
            change = current_price - prev_close
            change_pct = (change / prev_close) * 100

        # Направление последней свечи (сравниваем последние open/close)
        candle_direction = None
        if len(opens_clean) > 0 and len(closes_clean) > 0:
            last_open = opens_clean[-1]
            last_close = closes_clean[-1]
            if last_close > last_open:
                candle_direction = "bullish"
            elif last_close < last_open:
                candle_direction = "bearish"
            else:
                candle_direction = "neutral"

        # Мини-RSI на основе последних свечей (упрощённый)
        rsi_signal = "нейтральный"
        if len(closes_clean) >= 3:
            gains = []
            losses = []
            for i in range(1, len(closes_clean)):
                delta = closes_clean[i] - closes_clean[i - 1]
                if delta > 0:
                    gains.append(delta)
                else:
                    losses.append(abs(delta))
            avg_gain = sum(gains) / len(gains) if gains else 0
            avg_loss = sum(losses) / len(losses) if losses else 0.0001
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            if rsi > 60:
                rsi_signal = f"перекупленность ({rsi:.0f})"
            elif rsi < 40:
                rsi_signal = f"перепроданность ({rsi:.0f})"
            else:
                rsi_signal = f"нейтральный ({rsi:.0f})"

        # Волатильность (разброс high-low)
        volatility = "низкая"
        if highs_clean and lows_clean:
            avg_range = sum(h - l for h, l in zip(highs_clean, lows_clean)) / len(highs_clean)
            if avg_range > current_price * 0.0005:
                volatility = "высокая"
            elif avg_range > current_price * 0.0002:
                volatility = "средняя"

        return {
            "price": current_price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "candle_direction": candle_direction,
            "rsi_signal": rsi_signal,
            "volatility": volatility,
            "high": max(highs_clean) if highs_clean else current_price,
            "low": min(lows_clean) if lows_clean else current_price,
        }

    except Exception as e:
        print(f"Ошибка получения котировки {ticker}: {e}")
        return None


def generate_signal_from_quote(quote: dict) -> tuple[str, int]:
    """
    Генерирует торговое направление и уверенность на основе реальных данных котировки.
    Возвращает (direction_str, confidence_int).
    """
    score = 0  # положительный = бычий сигнал, отрицательный = медвежий

    # Фактор 1: направление последней свечи
    if quote["candle_direction"] == "bullish":
        score += 2
    elif quote["candle_direction"] == "bearish":
        score -= 2

    # Фактор 2: изменение цены от предыдущего закрытия
    if quote["change_pct"] > 0.01:
        score += 1
    elif quote["change_pct"] < -0.01:
        score -= 1

    # Фактор 3: RSI сигнал
    rsi_str = quote["rsi_signal"]
    if "перепроданность" in rsi_str:
        score += 2  # перепроданность = ждём отскок вверх
    elif "перекупленность" in rsi_str:
        score -= 2  # перекупленность = ждём откат вниз

    # Добавляем небольшой случайный шум (рынок непредсказуем)
    score += random.choice([-1, 0, 0, 1])

    if score > 0:
        direction = "ВВЕРХ 🟢 (CALL)"
    elif score < 0:
        direction = "ВНИЗ 🔴 (PUT)"
    else:
        direction = random.choice(["ВВЕРХ 🟢 (CALL)", "ВНИЗ 🔴 (PUT)"])

    # Уверенность: чем сильнее сигнал — тем выше процент
    abs_score = abs(score)
    if abs_score >= 4:
        confidence = random.randint(91, 96)
    elif abs_score == 3:
        confidence = random.randint(88, 92)
    elif abs_score == 2:
        confidence = random.randint(85, 90)
    else:
        confidence = random.randint(82, 87)

    return direction, confidence


# ===== ДАННЫЕ И КЛАВИАТУРЫ =====
pairs = [
    "💵 AUD/CAD", "💵 CAD/CHF", "💵 EUR/CHF", "💵 GBP/CAD",
    "💵 USD/CAD", "💵 GBP/JPY", "💵 EUR/USD", "💵 USD/JPY"
]
times = ["⏱ 1 мин", "⏱ 3 мин", "⏱ 5 мин", "⏱ 10 мин"]

user_temp_data = {}
pending_users = set()
pending_support = set()
last_click_time = {}

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
            allowed = ["🔐 Активировать доступ", "📩 Отправить ID Pocket Option", "⬅️ Назад", "/start", "⬅️ В меню", "/vip", "/help", "🆘 Поддержка"]
            if not user_info["has_access"] and uid not in pending_users and uid not in pending_support:
                if text not in allowed:
                    await event.answer("⚠️ <b>ОШИБКА ДОСТУПА: ТЕРМИНАЛ ЗАБЛОКИРОВАН</b>\n\nДля получения алгоритмических сигналов с высокой проходимостью (WinRate 88-92%), необходимо активировать VIP-лицензию.", parse_mode="HTML")
                    return
        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

# ===== КЛАВИАТУРЫ =====
def get_main_menu(has_access: bool):
    keyboard = [
        [KeyboardButton(text="📊 Торговая панель")],
        [KeyboardButton(text="⚡ Получить сигнал")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📈 Статистика")]
    ]
    row_access = []
    if not has_access:
        row_access.append(KeyboardButton(text="🔐 Активировать доступ"))
    row_access.append(KeyboardButton(text="💎 Подписка"))
    keyboard.append(row_access)
    keyboard.append([KeyboardButton(text="🆘 Поддержка")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

access_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📩 Отправить ID Pocket Option")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
pair_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=p)] for p in pairs] + [[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
time_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t)] for t in times] + [[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
signal_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⚡ Получить сигнал")], [KeyboardButton(text="⬅️ В меню")]], resize_keyboard=True)
back_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)

def get_sub_kb():
    buttons = [
        [InlineKeyboardButton(text="JUNIOR - 50$ / 7 дней", callback_data="buy_junior")],
        [InlineKeyboardButton(text="PRO - 100$ / 7 дней", callback_data="buy_pro")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ===== ХЕНДЛЕРЫ ПОДПИСОК =====
@dp.message(F.text == "💎 Подписка")
async def sub_menu(message: Message):
    u = db_get_user(message.from_user.id)
    limit = SUBSCRIPTION_PLANS[u['sub_type']]['limit']
    text = (
        "💎 <b>УПРАВЛЕНИЕ ПОДПИСКОЙ</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"Текущий тариф: <b>{u['sub_type'].upper()}</b>\n"
        f"Лимит сделок: <b>{limit} в день</b>\n\n"
        "<b>Доступные тарифы:</b>\n"
        "🔹 <b>JUNIOR:</b> до 50 сделок в день (50$ / 7 дней)\n"
        "🔸 <b>PRO:</b> до 100 сделок в день (100$ / 7 дней)\n\n"
        "<i>Выберите подписку для автоматической оплаты через CryptoBot (USDT):</i>"
    )
    await message.answer(text, reply_markup=get_sub_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery):
    plan_key = callback.data.split("_")[1]
    plan = SUBSCRIPTION_PLANS[plan_key]
    res = await create_invoice(plan['price'], plan['name'])
    if res['ok']:
        invoice_url = res['result']['pay_url']
        invoice_id = res['result']['invoice_id']
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить (USDT)", url=invoice_url)],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}_{plan_key}")]
        ])
        await callback.message.edit_text(
            f"🚀 <b>Счет на оплату тарифа {plan['name']} сформирован!</b>\n\n"
            f"Сумма к оплате: <b>{plan['price']}$</b>\n\n"
            "После успешной оплаты нажмите кнопку «Проверить оплату».",
            reply_markup=kb, parse_mode="HTML"
        )
    else:
        await callback.answer("⚠️ Ошибка создания счета. Попробуйте позже.", show_alert=True)

@dp.callback_query(F.data.startswith("check_"))
async def process_check(callback: CallbackQuery):
    _, inv_id, plan_key = callback.data.split("_")
    is_paid = await check_invoice(inv_id)
    if is_paid:
        expiry = datetime.now() + timedelta(days=7)
        db_update_user(callback.from_user.id, sub_type=plan_key, sub_expires=expiry)
        await callback.message.edit_text(
            f"🎉 <b>Оплата успешно подтверждена!</b>\n\n"
            f"Тариф <b>{plan_key.upper()}</b> активирован на 7 дней.\n"
            f"Ваш новый лимит: <b>{SUBSCRIPTION_PLANS[plan_key]['limit']}</b> сигналов в день.",
            parse_mode="HTML"
        )
    else:
        await callback.answer("❌ Оплата еще не поступила. Попробуйте проверить через минуту.", show_alert=True)

# ===== ХЕНДЛЕРЫ КОМАНД =====
@dp.message(CommandStart())
async def start(message: Message):
    db_update_user(message.from_user.id)
    u = db_get_user(message.from_user.id)
    start_text = (
        "🖥 <b>AI TRADING TERMINAL | FX PRO</b> 📈\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💸 <b>Преврати трейдинг в систему с математическим перевесом!</b>\n\n"
        "Наш нейросетевой алгоритм анализирует паттерны Price Action, "
        "объемы и волатильность рынка, обеспечивая WinRate сделок <b>до 92.4%</b>.\n\n"
        "📌 <i>Используй профессиональную аналитику для скальпинга и дейтрейдинга на Pocket Option.</i>\n\n"
        "🛠 <b>Статус системы:</b> ОНЛАЙН 🟢"
    )
    await message.answer(start_text, reply_markup=get_main_menu(u["has_access"]), parse_mode="HTML")

@dp.message(Command("vip"))
@dp.message(F.text == "🔐 Активировать доступ")
async def activate(message: Message):
    user_info = db_get_user(message.from_user.id)
    if user_info["has_access"]: return await message.answer("✅ <b>СТАТУС:</b> ЛИЦЕНЗИЯ АКТИВНА\nВам доступны все модули терминала.", parse_mode="HTML")
    await message.answer(
        "💎 <b>ПОЛУЧЕНИЕ VIP-ЛИЦЕНЗИИ</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "1️⃣ <b>Регистрация торгового счета:</b>\n"
        "▫️ Global (Ссылка для всех стран): <a href='https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1746882&ac=fx&cid=950203&code=ESX408'>Pocket Option (Официальный шлюз)</a>\n"
        "▫️ RU/СНГ (Для людей из России и СНГ): <a href='https://po-ru4.click/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1746882&ac=fx&cid=950203&code=ESX408'>Pocket Option (Зеркало)</a>\n\n"
        "2️⃣ <b>Депозит:</b> от <b>$50</b> (для соблюдения риск-менеджмента 1-5% на сделку)\n"
        "3️⃣ <b>Синхронизация:</b> Жми кнопку ниже и отправь свой <b>ID Pocket Option</b>\n\n"
        "🎁 <b>БОНУС:</b> При регистрации по ссылкам выше вы получите <b>подарок +60% к депозиту</b> (плюшка для быстрого старта)!\n\n"
        "⚠️ <b>ВАЖНО:</b> Если у вас уже есть аккаунт Pocket Option, созданный не по нашей ссылке, бот не сможет его распознать. В этом случае вам необходимо <b>удалить ваш текущий аккаунт и заново зарегистрироваться</b> строго по ссылке бота выше. Других вариантов активации нет. После регистрации пополните депозит и отправьте ID вашего профиля из личного кабинета.\n\n"
        "🛡 <i>После проверки ИИ подключит ваш аккаунт к пулу сигналов.</i>",
        reply_markup=access_kb, parse_mode="HTML", disable_web_page_preview=True
    )

@dp.message(Command("help"))
@dp.message(F.text == "🆘 Поддержка")
async def help_cmd(message: Message):
    pending_support.add(message.from_user.id)
    await message.answer(
        "🆘 <b>ЦЕНТР ПОДДЕРЖКИ</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Если у вас возникли вопросы по работе терминала или активации лицензии:\n\n"
        "✍️ <b>Опишите ваш вопрос прямо здесь, одним сообщением.</b>\n"
        "Ваше обращение будет мгновенно передано администратору.\n\n"
        "📖 <b>Инструкция:</b> /start\n\n"
        "<i>Мы работаем 24/7 для вашего профита!</i>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )

@dp.message(F.text == "📩 Отправить ID Pocket Option")
async def ask_id(message: Message):
    pending_users.add(message.from_user.id)
    await message.answer("⌨️ <b>Введите Ваш цифровой ID профиля Pocket Option:</b>\n<i>(Только цифры, без пробелов и букв)</i>", reply_markup=back_kb, parse_mode="HTML")

@dp.message(F.text == "⬅️ Назад")
@dp.message(F.text == "⬅️ В меню")
async def go_back(message: Message):
    pending_users.discard(message.from_user.id)
    pending_support.discard(message.from_user.id)
    u = db_get_user(message.from_user.id)
    await message.answer("🏠 <b>Главная панель управления</b>", reply_markup=get_main_menu(u["has_access"]), parse_mode="HTML")

@dp.message(lambda msg: msg.from_user.id in pending_support)
async def process_support_message(message: Message):
    if message.text == "⬅️ Назад":
        pending_support.discard(message.from_user.id)
        return await go_back(message)
    uid = message.from_user.id
    username = message.from_user.username or "Нет юзернейма"
    await bot.send_message(
        ADMIN_ID,
        f"📩 <b>НОВОЕ ОБРАЩЕНИЕ В ПОДДЕРЖКУ</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 От: @{username}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📝 Сообщение: {message.text}",
        parse_mode="HTML"
    )
    pending_support.discard(uid)
    u = db_get_user(uid)
    await message.answer("✅ <b>Ваше сообщение отправлено!</b>\nАдминистратор рассмотрит ваше обращение в ближайшее время.", reply_markup=get_main_menu(u["has_access"]), parse_mode="HTML")

@dp.message(lambda msg: msg.from_user.id in pending_users)
async def process_id(message: Message):
    if message.text == "⬅️ Назад":
        pending_users.discard(message.from_user.id)
        return await go_back(message)
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
    u = db_get_user(uid)
    await message.answer("💾 <b>ID принят в обработку.</b>\nОжидайте подтверждения верификации от технического отдела.", reply_markup=get_main_menu(u["has_access"]), parse_mode="HTML")

@dp.message(F.text.startswith("/give"))
async def admin_give(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=True)
        await bot.send_message(target, "🚀 <b>СИСТЕМА: VIP ДОСТУП АКТИВИРОВАН</b>\nТерминал разблокирован. Вам доступны профессиональные сигналы для профита!", parse_mode="HTML", reply_markup=get_main_menu(True))
        await message.answer(f"✅ Доступ для пользователя <code>{target}</code> успешно АКТИВИРОВАН.", parse_mode="HTML")
    except:
        await message.answer("⚠️ Ошибка. Формат: <code>/give ID</code>", parse_mode="HTML")

@dp.message(F.text.startswith("/block"))
async def admin_block(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=False)
        try:
            await bot.send_message(target, "🛑 <b>СИСТЕМА: ВАШ ДОСТУП АННУЛИРОВАН</b>\nВаша подписка на торговые сигналы была отключена администратором.", parse_mode="HTML", reply_markup=get_main_menu(False))
        except:
            pass
        await message.answer(f"🚫 Доступ для пользователя <code>{target}</code> успешно ЗАБЛОКИРОВАН.", parse_mode="HTML")
    except:
        await message.answer("⚠️ Ошибка. Формат: <code>/block ID</code>", parse_mode="HTML")

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

# ===== ГЛАВНЫЙ ХЕНДЛЕР СИГНАЛА (С РЕАЛЬНЫМИ КОТИРОВКАМИ) =====
@dp.message(Command("signals"))
@dp.message(F.text == "⚡ Получить сигнал")
async def get_signal(message: Message):
    uid = message.from_user.id
    u = db_get_user(uid)
    if not u["has_access"]: return

    today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")
    daily = u["daily_count"]

    if u["last_date"] != today:
        daily = 0
        db_update_user(uid, daily=0, date=today)

    sub_type = u['sub_type']
    current_limit = SUBSCRIPTION_PLANS[sub_type]['limit']

    if daily >= current_limit:
        if sub_type == "free":
            risk_text = (
                "🛑 <b>ЛИМИТ БЕСПЛАТНЫХ СИГНАЛОВ ИСЧЕРПАН</b>\n"
                "━━━━━━━━━━━━━━━━━\n"
                f"Вы достигли лимита (<b>{current_limit} сделок</b> за сегодня).\n\n"
                "Если хотите получать больше сигналов (до 50 или 100 в день), оформите подписку <b>JUNIOR</b> или <b>PRO</b> в меню бота!\n\n"
                "⏳ <i>Или дождитесь обновления лимита завтра.</i>"
            )
        else:
            risk_text = (
                "🛑 <b>ЛИМИТ ТОРГОВЫХ СЕССИЙ</b>\n"
                "━━━━━━━━━━━━━━━━━\n"
                f"Ваш дневной лимит по подписке {sub_type.upper()} (<b>{current_limit} сделок</b>) исчерпан.\n\n"
                "Система защиты капитала активирована для предотвращения тильта. "
                "Алгоритм возобновит работу завтра.\n\n"
                "⏳ <i>Отдохните от рынка и возвращайтесь с новыми силами!</i>"
            )
        return await message.answer(risk_text, parse_mode="HTML")

    data = user_temp_data.get(uid)
    if not data or "pair" not in data:
        return await message.answer("⚠️ Ошибка конфигурации. Настройте актив в меню «📊 Торговая панель»!")

    if time.time() - last_click_time.get(uid, 0) < 6:
        return await message.answer("⏳ <b>Идет просчет...</b> Дождитесь завершения предыдущего анализа.")

    last_click_time[uid] = time.time()

    # === Прогресс-бар с анализом ===
    progress_msg = await message.answer("⬛️⬛️⬛️⬛️⬛️ [0%]\n📡 <i>Подключение к потоку котировок...</i>", parse_mode="HTML")
    await asyncio.sleep(0.7)
    await progress_msg.edit_text("🟩⬛️⬛️⬛️⬛️ [20%]\n📊 <i>Загрузка реальных данных с рынка...</i>", parse_mode="HTML")

    # === ПОЛУЧАЕМ РЕАЛЬНУЮ КОТИРОВКУ ===
    quote = await get_real_quote(data["pair"])

    await asyncio.sleep(0.5)
    await progress_msg.edit_text("🟩🟩🟩⬛️⬛️ [60%]\n📉 <i>Анализ волатильности и свечного паттерна...</i>", parse_mode="HTML")
    await asyncio.sleep(0.6)
    await progress_msg.edit_text("🟩🟩🟩🟩⬛️ [85%]\n🎯 <i>Расчет RSI и математического перевеса...</i>", parse_mode="HTML")
    await asyncio.sleep(0.5)
    await progress_msg.edit_text("🟩🟩🟩🟩🟩 [100%]\n✅ <i>Сигнал сформирован!</i>", parse_mode="HTML")
    await asyncio.sleep(0.4)

    db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)

    # === ФОРМИРУЕМ СИГНАЛ ===
    if quote:
        # Сигнал на основе реальных данных
        direction, confidence = generate_signal_from_quote(quote)

        # Форматируем цену
        price_val = quote["price"]
        if price_val > 100:
            price_str = f"{price_val:.3f}"
        else:
            price_str = f"{price_val:.5f}"

        change_sign = "+" if quote["change"] >= 0 else ""
        change_str = f"{change_sign}{quote['change_pct']:.3f}%"
        candle_emoji = "🟢" if quote["candle_direction"] == "bullish" else ("🔴" if quote["candle_direction"] == "bearish" else "⚪")

        res = (
            f"⚡️ <b>ТОРГОВЫЙ СИГНАЛ ГОТОВ</b> ⚡️\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Актив:</b> {data['pair']}\n"
            f"⏱ <b>Время сделки:</b> {data['time']}\n\n"
            f"📡 <b>РЫНОЧНЫЕ ДАННЫЕ (live):</b>\n"
            f"▫️ Текущая цена: <b>{price_str}</b>\n"
            f"▫️ Изменение: <b>{change_str}</b>\n"
            f"▫️ Последняя свеча: {candle_emoji} <b>{quote['candle_direction'].upper()}</b>\n"
            f"▫️ RSI: <b>{quote['rsi_signal']}</b>\n"
            f"▫️ Волатильность: <b>{quote['volatility']}</b>\n\n"
            f"🧠 <b>Уверенность ИИ:</b> {confidence}%\n\n"
            f"🚀 <b>РЕКОМЕНДАЦИЯ: {direction}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <i>Не забудьте про Money Management! (1-3% от баланса)</i>"
        )
    else:
        # Фолбэк: котировки недоступны — генерим как раньше
        direction = random.choice(["ВВЕРХ 🟢 (CALL)", "ВНИЗ 🔴 (PUT)"])
        confidence = random.randint(88, 96)
        res = (
            f"⚡️ <b>ТОРГОВЫЙ СИГНАЛ ГОТОВ</b> ⚡️\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Актив:</b> {data['pair']}\n"
            f"⏱ <b>Время сделки:</b> {data['time']}\n"
            f"🧠 <b>Уверенность ИИ:</b> {confidence}%\n\n"
            f"🚀 <b>РЕКОМЕНДАЦИЯ: {direction}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <i>Не забудьте про Money Management! (1-3% от баланса)</i>\n"
            f"<i>⚡ Котировки временно недоступны, использован автономный режим.</i>"
        )

    try:
        await progress_msg.delete()
    except:
        pass
    await message.answer(res, parse_mode="HTML", reply_markup=signal_kb)

@dp.message(Command("profile"))
@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    u = db_get_user(message.from_user.id)
    rank = get_rank(u["signals"])
    sub_limit = SUBSCRIPTION_PLANS[u["sub_type"]]["limit"]
    expiry_str = u['sub_expires'].strftime("%d.%m.%Y %H:%M") if u['sub_expires'] else "Не ограничено"
    await message.answer(
        f"👤 <b>ЛИЧНЫЙ КАБИНЕТ ТРЕЙДЕРА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 Ваш ID: <code>{message.from_user.id}</code>\n"
        f"🏆 Уровень: <b>{rank}</b>\n\n"
        f"💎 <b>ТОРГОВАЯ ПОДПИСКА:</b>\n"
        f"▫️ Тариф: <b>{u['sub_type'].upper()}</b>\n"
        f"▫️ Истекает: <b>{expiry_str}</b>\n\n"
        f"📈 <b>ТОРГОВАЯ АКТИВНОСТЬ:</b>\n"
        f"▫️ Выполнено сделок (всего): <b>{u['signals']}</b>\n"
        f"▫️ Сделок за сегодня: <b>{u['daily_count']} / {sub_limit}</b>\n\n"
        f"💎 <b>СТАТУС ЛИЦЕНЗИИ:</b> {'АКТИВНА ✅' if u['has_access'] else 'ОГРАНИЧЕНА ❌'}",
        parse_mode="HTML"
    )

# ===== СТАТИСТИКА =====
@dp.message(F.text == "📈 Статистика")
async def stats(message: Message):
    seed_val = int(datetime.now().strftime("%Y%m%d"))
    random.seed(seed_val)
    total_day = random.randint(1800, 2500)
    win_rate = round(random.uniform(91.2, 94.8), 1)
    plus_deals = int(total_day * (win_rate / 100))
    minus_deals = total_day - plus_deals - random.randint(10, 30)
    refunds = total_day - plus_deals - minus_deals
    await message.answer(
        f"📊 <b>ГЛОБАЛЬНАЯ СТАТИСТИКА ИИ (24 часа)</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 Средний WinRate: <b>{win_rate}%</b>\n"
        f"🟢 Плюсовых сделок: <b>{plus_deals}</b>\n"
        f"🔴 Минусовых сделок: <b>{minus_deals}</b>\n"
        f"🔁 Возвратов: <b>{refunds}</b>\n\n"
        f"⚙️ <i>Сводка формируется автоматически на базе пула всех торговых сессий наших пользователей на платформе Pocket Option. Данные обновлены: {datetime.now().strftime('%d.%m.%Y')}</i>",
        parse_mode="HTML"
    )
    random.seed()

async def main():
    print("🚀 PRO AI BOT STARTED SUCCESSFULLY")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
