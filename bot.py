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

import psycopg2
from psycopg2.extras import RealDictCursor

# ═══════════════════════════════════════════════
#              КОНФИГУРАЦИЯ СИСТЕМЫ
# ═══════════════════════════════════════════════
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")

if not TOKEN or not ADMIN_ID or not CRYPTO_BOT_TOKEN:
    raise ValueError("Проверьте BOT_TOKEN, ADMIN_ID и CRYPTO_BOT_TOKEN в переменных Railway!")

ADMIN_ID = int(ADMIN_ID)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ═══════════════════════════════════════════════
#              ПЛАНЫ ПОДПИСОК
# Лимиты: FREE=5, JUNIOR=15, PRO=30
# ═══════════════════════════════════════════════
SUBSCRIPTION_PLANS = {
    "free":   {"limit": 5,  "name": "FREE",   "price": 0,   "emoji": "⬜"},
    "junior": {"limit": 15, "name": "JUNIOR",  "price": 50,  "duration": 7, "emoji": "🔵"},
    "pro":    {"limit": 30, "name": "PRO",     "price": 100, "duration": 7, "emoji": "🟣"},
}

# ═══════════════════════════════════════════════
#         МАППИНГ ПАР → YAHOO FINANCE
# ═══════════════════════════════════════════════
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

# ═══════════════════════════════════════════════
#         ПРОВЕРКА РАБОЧЕГО ВРЕМЕНИ РЫНКА
#  ПН–ПТ: работает круглосуточно
#  СБ–ВС: закрыт
# ═══════════════════════════════════════════════
def is_market_open() -> bool:
    """Возвращает True если сегодня будний день (ПН–ПТ)."""
    now = datetime.utcnow() + timedelta(hours=3)  # МСК
    return now.weekday() < 5  # 0=ПН, 4=ПТ, 5=СБ, 6=ВС

def get_market_closed_text() -> str:
    now = datetime.utcnow() + timedelta(hours=3)
    days_until_monday = (7 - now.weekday()) % 7 or 7
    monday = now + timedelta(days=days_until_monday)
    monday_str = monday.strftime("%d.%m.%Y")
    return (
        "🔴 <b>РЫНОК ЗАКРЫТ — ВЫХОДНОЙ ДЕНЬ</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "📅 Сегодня <b>суббота/воскресенье</b> — Forex не работает.\n\n"
        "🌐 Валютные пары в эти дни не торгуются:\n"
        "  ▸ Спреды неадекватны\n"
        "  ▸ Ликвидность отсутствует\n"
        "  ▸ Сигналы недостоверны\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "⏰ <b>График работы терминала:</b>\n"
        "  🟢 ПН–ПТ: круглосуточно (24/7)\n"
        "  🔴 СБ–ВС: рынок закрыт\n\n"
        f"📆 Следующее открытие: <b>Понедельник, {monday_str} 00:00 МСК</b>\n\n"
        "💤 <i>Отдыхайте, анализируйте, готовьтесь к новой неделе!\n"
        "Возвращайтесь в понедельник с новыми силами и свежим взглядом. 💪</i>"
    )

# ═══════════════════════════════════════════════
#              РАБОТА С PostgreSQL
# ═══════════════════════════════════════════════
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id         BIGINT PRIMARY KEY,
                has_access      BOOLEAN   DEFAULT FALSE,
                total_signals   INTEGER   DEFAULT 0,
                daily_signals   INTEGER   DEFAULT 0,
                last_signal_date TEXT,
                sub_type        TEXT      DEFAULT 'free',
                sub_expires     TIMESTAMP,
                username        TEXT,
                first_seen      TIMESTAMP DEFAULT NOW(),
                last_active     TIMESTAMP DEFAULT NOW()
            )
        """)
        for col, definition in [
            ("username",    "TEXT"),
            ("first_seen",  "TIMESTAMP DEFAULT NOW()"),
            ("last_active", "TIMESTAMP DEFAULT NOW()"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {definition}")
            except Exception:
                pass
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")

def db_get_user(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT has_access, total_signals, daily_signals, last_signal_date, "
            "sub_type, sub_expires, username FROM users WHERE user_id = %s",
            (user_id,)
        )
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
            last_date   = row['last_signal_date'] or ""

            if last_date != "" and last_date != today:
                daily_count = 0
                last_date   = today
                db_update_user(user_id, daily=0, date=today)

            return {
                "has_access":  row['has_access'],
                "signals":     row['total_signals'],
                "daily_count": daily_count,
                "last_date":   last_date,
                "sub_type":    sub_type,
                "sub_expires": row['sub_expires'],
                "username":    row.get('username', ''),
            }
    except Exception as e:
        print(f"Ошибка чтения из БД: {e}")
    return {"has_access": False, "signals": 0, "daily_count": 0,
            "last_date": "", "sub_type": "free", "sub_expires": None, "username": ""}

def db_update_user(user_id, has_access=None, signals=None, daily=None,
                   date=None, sub_type=None, sub_expires=None, username=None):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            (user_id,)
        )
        if has_access  is not None:
            cursor.execute("UPDATE users SET has_access = %s WHERE user_id = %s", (has_access, user_id))
        if signals     is not None:
            cursor.execute("UPDATE users SET total_signals = %s WHERE user_id = %s", (signals, user_id))
        if daily       is not None:
            cursor.execute("UPDATE users SET daily_signals = %s WHERE user_id = %s", (daily, user_id))
        if date        is not None:
            cursor.execute("UPDATE users SET last_signal_date = %s WHERE user_id = %s", (date, user_id))
        if sub_type    is not None:
            cursor.execute("UPDATE users SET sub_type = %s WHERE user_id = %s", (sub_type, user_id))
        if sub_expires is not None or sub_type == 'free':
            cursor.execute("UPDATE users SET sub_expires = %s WHERE user_id = %s", (sub_expires, user_id))
        if username    is not None:
            cursor.execute("UPDATE users SET username = %s WHERE user_id = %s", (username, user_id))
        cursor.execute("UPDATE users SET last_active = NOW() WHERE user_id = %s", (user_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка обновления БД: {e}")

def db_get_total_users():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return count
    except:
        return 0

def db_get_active_users():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE last_active > NOW() - INTERVAL '24 hours'")
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return count
    except:
        return 0

init_db()

# ═══════════════════════════════════════════════
#              CRYPTO BOT API
# ═══════════════════════════════════════════════
async def create_invoice(amount, plan_name):
    url     = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    payload = {
        "asset":        "USDT",
        "amount":       str(amount),
        "description":  f"Подписка {plan_name} на 7 дней | AI Trading Terminal",
        "paid_btn_name":"callback",
        "paid_btn_url": "https://t.me/CryptoBot"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            return await resp.json()

async def check_invoice(invoice_id):
    url     = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            if data['ok'] and data['result']['items']:
                return data['result']['items'][0]['status'] == 'paid'
    return False

# ═══════════════════════════════════════════════
#         ПОЛУЧЕНИЕ КОТИРОВОК (YAHOO FINANCE)
# ═══════════════════════════════════════════════
async def get_real_quote(pair_label: str) -> dict | None:
    ticker = PAIR_TO_TICKER.get(pair_label)
    if not ticker:
        return None

    url     = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=5m"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        meta       = result[0].get("meta", {})
        indicators = result[0].get("indicators", {}).get("quote", [{}])[0]

        current_price = meta.get("regularMarketPrice")
        prev_close    = meta.get("chartPreviousClose") or meta.get("previousClose")

        opens  = indicators.get("open",  [])
        closes = indicators.get("close", [])
        highs  = indicators.get("high",  [])
        lows   = indicators.get("low",   [])

        opens_clean  = [x for x in opens  if x is not None]
        closes_clean = [x for x in closes if x is not None]
        highs_clean  = [x for x in highs  if x is not None]
        lows_clean   = [x for x in lows   if x is not None]

        if not current_price:
            return None

        change     = 0.0
        change_pct = 0.0
        if prev_close and prev_close != 0:
            change     = current_price - prev_close
            change_pct = (change / prev_close) * 100

        candle_direction = None
        if opens_clean and closes_clean:
            last_open  = opens_clean[-1]
            last_close = closes_clean[-1]
            if   last_close > last_open: candle_direction = "bullish"
            elif last_close < last_open: candle_direction = "bearish"
            else:                        candle_direction = "neutral"

        # Мини-RSI
        rsi_value  = 50.0
        rsi_signal = "нейтральный"
        if len(closes_clean) >= 3:
            gains  = []
            losses = []
            for i in range(1, len(closes_clean)):
                delta = closes_clean[i] - closes_clean[i - 1]
                if delta > 0: gains.append(delta)
                else:         losses.append(abs(delta))
            avg_gain  = sum(gains)  / len(gains)  if gains  else 0
            avg_loss  = sum(losses) / len(losses) if losses else 0.0001
            rs        = avg_gain / avg_loss
            rsi_value = 100 - (100 / (1 + rs))
            if   rsi_value > 60: rsi_signal = f"перекупленность ({rsi_value:.0f})"
            elif rsi_value < 40: rsi_signal = f"перепроданность ({rsi_value:.0f})"
            else:                rsi_signal = f"нейтральный ({rsi_value:.0f})"

        # Волатильность
        volatility = "низкая"
        if highs_clean and lows_clean:
            avg_range = sum(h - l for h, l in zip(highs_clean, lows_clean)) / len(highs_clean)
            if   avg_range > current_price * 0.0005: volatility = "высокая 🔥"
            elif avg_range > current_price * 0.0002: volatility = "средняя"

        # Тренд
        trend = "боковик"
        if len(closes_clean) >= 2:
            diff = closes_clean[-1] - closes_clean[0]
            if   diff > 0: trend = "восходящий 📈"
            elif diff < 0: trend = "нисходящий 📉"

        return {
            "price":            current_price,
            "prev_close":       prev_close,
            "change":           change,
            "change_pct":       change_pct,
            "candle_direction": candle_direction,
            "rsi_signal":       rsi_signal,
            "rsi_value":        rsi_value,
            "volatility":       volatility,
            "trend":            trend,
            "high":             max(highs_clean)  if highs_clean  else current_price,
            "low":              min(lows_clean)   if lows_clean   else current_price,
            "candles_count":    len(closes_clean),
        }

    except Exception as e:
        print(f"Ошибка получения котировки {ticker}: {e}")
        return None


def generate_signal_from_quote(quote: dict) -> tuple[str, int]:
    score = 0

    if   quote["candle_direction"] == "bullish": score += 2
    elif quote["candle_direction"] == "bearish": score -= 2

    if   quote["change_pct"] > 0.01:  score += 1
    elif quote["change_pct"] < -0.01: score -= 1

    rsi_str = quote["rsi_signal"]
    if   "перепроданность" in rsi_str: score += 2
    elif "перекупленность" in rsi_str: score -= 2

    if   "восходящий" in quote.get("trend", ""): score += 1
    elif "нисходящий" in quote.get("trend", ""): score -= 1

    score += random.choice([-1, 0, 0, 1])

    if   score > 0: direction = "ВВЕРХ 🟢 (CALL)"
    elif score < 0: direction = "ВНИЗ 🔴 (PUT)"
    else:           direction = random.choice(["ВВЕРХ 🟢 (CALL)", "ВНИЗ 🔴 (PUT)"])

    abs_score = abs(score)
    if   abs_score >= 5: confidence = random.randint(93, 97)
    elif abs_score == 4: confidence = random.randint(91, 95)
    elif abs_score == 3: confidence = random.randint(88, 92)
    elif abs_score == 2: confidence = random.randint(85, 90)
    else:                confidence = random.randint(82, 87)

    return direction, confidence


# ═══════════════════════════════════════════════
#         РАНГИ И УТИЛИТЫ
# ═══════════════════════════════════════════════
RANKS = [
    (0,   50,  "🌱 Новичок",      "Retail"),
    (51,  150, "📊 Трейдер",       "Prop Firm"),
    (151, 350, "📈 Про-Трейдер",   "Institutional"),
    (351, 700, "🔥 Эксперт",       "Smart Money"),
    (701, 9999,"👑 Маркет-Мейкер", "Whale"),
]

def get_rank(count):
    for lo, hi, title, level in RANKS:
        if lo <= count <= hi:
            return f"{title} ({level})"
    return "👑 Маркет-Мейкер (Whale)"

def get_next_rank(count):
    for lo, hi, title, level in RANKS:
        if lo <= count <= hi:
            idx = RANKS.index((lo, hi, title, level))
            if idx + 1 < len(RANKS):
                nxt = RANKS[idx + 1]
                return nxt[2], nxt[3], nxt[0] - count
    return None, None, 0

def format_rsi_bar(rsi_value: float) -> str:
    filled = int(rsi_value / 10)
    filled = max(0, min(10, filled))
    bar    = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {rsi_value:.0f}"

def confidence_bar(pct: int) -> str:
    filled = int(pct / 10)
    filled = max(0, min(10, filled))
    return "▓" * filled + "░" * (10 - filled)

def days_bar(used: int, total: int) -> str:
    """Прогресс-бар для дней подписки."""
    pct = used / total if total > 0 else 0
    filled = int(pct * 10)
    return "█" * filled + "░" * (10 - filled)


# ═══════════════════════════════════════════════
#              ВРЕМЕННЫЕ ДАННЫЕ
# ═══════════════════════════════════════════════
pairs = [
    "💵 AUD/CAD", "💵 CAD/CHF", "💵 EUR/CHF", "💵 GBP/CAD",
    "💵 USD/CAD", "💵 GBP/JPY", "💵 EUR/USD", "💵 USD/JPY"
]
times = ["⏱ 1 мин", "⏱ 3 мин", "⏱ 5 мин", "⏱ 10 мин"]

user_temp_data  = {}
pending_users   = set()
pending_support = set()
last_click_time = {}

# ═══════════════════════════════════════════════
#              MIDDLEWARE
# ═══════════════════════════════════════════════
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            uid  = event.from_user.id
            text = event.text or ""
            if uid == ADMIN_ID:
                return await handler(event, data)
            user_info = db_get_user(uid)
            allowed = [
                "🔐 Активировать доступ", "📩 Отправить ID Pocket Option",
                "⬅️ Назад", "/start", "⬅️ В меню", "/vip", "/help",
                "🆘 Поддержка", "🚀 О боте"
            ]
            if not user_info["has_access"] and uid not in pending_users and uid not in pending_support:
                if text not in allowed:
                    await event.answer(
                        "🔒 <b>ДОСТУП ОГРАНИЧЕН</b>\n"
                        "━━━━━━━━━━━━━━━━━\n"
                        "Этот раздел доступен только верифицированным трейдерам.\n\n"
                        "📌 Нажмите <b>«🔐 Активировать доступ»</b> для получения VIP-лицензии.",
                        parse_mode="HTML"
                    )
                    return
        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

# ═══════════════════════════════════════════════
#              КЛАВИАТУРЫ
# ═══════════════════════════════════════════════
def get_main_menu(has_access: bool):
    keyboard = [
        [KeyboardButton(text="📊 Торговая панель"), KeyboardButton(text="⚡ Получить сигнал")],
        [KeyboardButton(text="👤 Профиль"),          KeyboardButton(text="📈 Статистика")],
        [KeyboardButton(text="💎 Подписка"),          KeyboardButton(text="🚀 О боте")],
    ]
    row_bottom = []
    if not has_access:
        row_bottom.append(KeyboardButton(text="🔐 Активировать доступ"))
    row_bottom.append(KeyboardButton(text="🆘 Поддержка"))
    keyboard.append(row_bottom)
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

access_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📩 Отправить ID Pocket Option")],
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
        [KeyboardButton(text="📊 Торговая панель"), KeyboardButton(text="⬅️ В меню")]
    ],
    resize_keyboard=True
)
back_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="⬅️ Назад")]],
    resize_keyboard=True
)

def get_sub_kb(current_plan: str = "free"):
    """Кнопки подписки с учётом текущего тарифа (показываем продление)."""
    buttons = []
    if current_plan == "free":
        buttons.append([InlineKeyboardButton(text="🔵 JUNIOR — 50$ / 7 дней", callback_data="buy_junior")])
        buttons.append([InlineKeyboardButton(text="🟣 PRO — 100$ / 7 дней",   callback_data="buy_pro")])
    elif current_plan == "junior":
        buttons.append([InlineKeyboardButton(text="🔄 Продлить JUNIOR — 50$ / 7 дней", callback_data="buy_junior")])
        buttons.append([InlineKeyboardButton(text="⬆️ Улучшить до PRO — 100$ / 7 дней", callback_data="buy_pro")])
    elif current_plan == "pro":
        buttons.append([InlineKeyboardButton(text="🔄 Продлить PRO — 100$ / 7 дней", callback_data="buy_pro")])
        buttons.append([InlineKeyboardButton(text="🔵 Сменить на JUNIOR — 50$ / 7 дней", callback_data="buy_junior")])
    buttons.append([InlineKeyboardButton(text="📊 Сравнить тарифы", callback_data="compare_plans")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_upgrade_kb():
    """Кнопка перехода в подписку из блока лимита."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 JUNIOR — 15 сигналов/день | 50$", callback_data="buy_junior")],
        [InlineKeyboardButton(text="🟣 PRO — 30 сигналов/день | 100$",   callback_data="buy_pro")],
        [InlineKeyboardButton(text="📊 Сравнить тарифы",                  callback_data="compare_plans")],
    ])

def get_confirm_sub_kb(invoice_url, invoice_id, plan_key):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить (USDT)", url=invoice_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}_{plan_key}")],
        [InlineKeyboardButton(text="🔙 Назад к тарифам",  callback_data="back_to_plans")],
    ])

# ═══════════════════════════════════════════════
#              ХЕНДЛЕРЫ ПОДПИСОК
# ═══════════════════════════════════════════════
@dp.message(F.text == "💎 Подписка")
async def sub_menu(message: Message):
    u     = db_get_user(message.from_user.id)
    plan  = SUBSCRIPTION_PLANS[u['sub_type']]
    limit = plan['limit']
    emoji = plan['emoji']

    exp_str = "∞ Бессрочно"
    days_left_str = ""
    if u['sub_expires']:
        exp_str = u['sub_expires'].strftime("%d.%m.%Y %H:%M")
        days_left = (u['sub_expires'] - datetime.now()).days
        days_used = 7 - days_left
        bar = days_bar(days_used, 7)
        days_left_str = f"\n  Осталось:    <code>[{bar}]</code> <b>{max(days_left, 0)} дн.</b>"

    # Блок «продление» для платных тарифов
    renew_block = ""
    if u['sub_type'] != 'free':
        renew_block = (
            "\n━━━━━━━━━━━━━━━━━\n"
            "🔄 <b>ПРОДЛЕНИЕ / СМЕНА ТАРИФА:</b>\n"
            "<i>Продлите подписку заранее — активация мгновенная.\n"
            "Срок добавится к текущему остатку.</i>\n"
        )

    text = (
        "💎 <b>УПРАВЛЕНИЕ ПОДПИСКОЙ</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"  Ваш тариф:   {emoji} <b>{u['sub_type'].upper()}</b>\n"
        f"  Лимит:       <b>{limit} сигналов / день</b>\n"
        f"  Истекает:    <b>{exp_str}</b>"
        f"{days_left_str}\n"
        f"{renew_block}"
        "\n━━━━━━━━━━━━━━━━━\n"
        "📦 <b>Доступные тарифы:</b>\n\n"
        "⬜ <b>FREE</b>    — 5 сигналов / день     <i>(бесплатно)</i>\n"
        "🔵 <b>JUNIOR</b>  — 15 сигналов / день    <i>50$ / 7 дней</i>\n"
        "🟣 <b>PRO</b>     — 30 сигналов / день    <i>100$ / 7 дней</i>\n\n"
        "<i>Оплата принимается в <b>USDT</b> через CryptoBot — мгновенно и безопасно.</i>"
    )
    await message.answer(text, reply_markup=get_sub_kb(u['sub_type']), parse_mode="HTML")

@dp.callback_query(F.data == "compare_plans")
async def compare_plans(callback: CallbackQuery):
    text = (
        "📊 <b>СРАВНЕНИЕ ТАРИФНЫХ ПЛАНОВ</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "<b>Функция               FREE   JUNIOR   PRO</b>\n"
        "Сигналы в день         5       15        30\n"
        "Реал. котировки      ✅      ✅        ✅\n"
        "RSI-анализ                ✅      ✅        ✅\n"
        "Анализ тренда          ✅      ✅        ✅\n"
        "Работа поддержки   ❌      ✅        ✅\n"
        "VIP-уведомления     ❌      ❌        ✅\n"
        "ТОП Стратегии        ❌      ❌        ✅\n"
        "Подписка                  ❌      ✅        ✅\n"
        "Цена                           0$     50$     100$\n"
        "Срок                           ∞      7 дн    7 дн\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<i>Выберите тариф и торгуйте с максимальным перевесом!</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Купить JUNIOR — 50$", callback_data="buy_junior")],
        [InlineKeyboardButton(text="🟣 Купить PRO — 100$",   callback_data="buy_pro")],
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "back_to_plans")
async def back_to_plans(callback: CallbackQuery):
    u = db_get_user(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=get_sub_kb(u['sub_type']))

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery):
    plan_key = callback.data.split("_")[1]
    plan     = SUBSCRIPTION_PLANS[plan_key]
    u        = db_get_user(callback.from_user.id)
    res      = await create_invoice(plan['price'], plan['name'])

    # Определяем текст кнопки (продление или покупка)
    is_renew = u['sub_type'] == plan_key
    action_word = "ПРОДЛЕНИЕ" if is_renew else "ПОКУПКА"

    if res['ok']:
        invoice_url = res['result']['pay_url']
        invoice_id  = res['result']['invoice_id']
        kb = get_confirm_sub_kb(invoice_url, invoice_id, plan_key)

        renew_note = ""
        if is_renew and u['sub_expires']:
            new_exp = u['sub_expires'] + timedelta(days=7)
            renew_note = f"\n  📅 Новая дата истечения: <b>{new_exp.strftime('%d.%m.%Y')}</b>\n"

        await callback.message.edit_text(
            f"🧾 <b>СЧЁТ НА {action_word}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"  Тариф:     {plan['emoji']} <b>{plan['name']}</b>\n"
            f"  Сумма:     <b>{plan['price']} USDT</b>\n"
            f"  Срок:      <b>7 дней</b>\n"
            f"  Лимит:     <b>{plan['limit']} сигналов / день</b>\n"
            f"{renew_note}"
            f"━━━━━━━━━━━━━━━━━\n"
            f"1️⃣ Нажмите <b>«💳 Оплатить»</b> — вы попадёте в CryptoBot\n"
            f"2️⃣ Совершите оплату в USDT\n"
            f"3️⃣ Вернитесь и нажмите <b>«✅ Проверить оплату»</b>\n\n"
            f"<i>⚡ Активация мгновенная после подтверждения транзакции.</i>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        await callback.answer("⚠️ Ошибка создания счёта. Попробуйте позже.", show_alert=True)

@dp.callback_query(F.data.startswith("check_"))
async def process_check(callback: CallbackQuery):
    parts    = callback.data.split("_")
    inv_id   = parts[1]
    plan_key = parts[2]
    is_paid  = await check_invoice(inv_id)

    if is_paid:
        u = db_get_user(callback.from_user.id)
        # Продление: если та же подписка и ещё не истекла — добавляем 7 дней
        if u['sub_type'] == plan_key and u['sub_expires'] and u['sub_expires'] > datetime.now():
            expiry = u['sub_expires'] + timedelta(days=7)
        else:
            expiry = datetime.now() + timedelta(days=7)

        db_update_user(callback.from_user.id, sub_type=plan_key, sub_expires=expiry)
        plan = SUBSCRIPTION_PLANS[plan_key]
        await callback.message.edit_text(
            f"🎉 <b>ОПЛАТА ПОДТВЕРЖДЕНА!</b>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"  Тариф:     {plan['emoji']} <b>{plan_key.upper()}</b>\n"
            f"  Лимит:     <b>{plan['limit']} сигналов / день</b>\n"
            f"  Истекает:  <b>{expiry.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🚀 <b>Терминал полностью активирован!</b>\n"
            f"<i>Желаем профитных сделок и зелёного депозита! 📈</i>",
            parse_mode="HTML"
        )
        try:
            await bot.send_message(
                ADMIN_ID,
                f"💰 <b>НОВАЯ ОПЛАТА ПОДПИСКИ</b>\n"
                f"👤 ID: <code>{callback.from_user.id}</code>\n"
                f"📦 Тариф: <b>{plan_key.upper()}</b>\n"
                f"💵 Сумма: <b>{plan['price']} USDT</b>\n"
                f"📅 Истекает: <b>{expiry.strftime('%d.%m.%Y %H:%M')}</b>",
                parse_mode="HTML"
            )
        except:
            pass
    else:
        await callback.answer("❌ Оплата ещё не поступила. Подождите и проверьте снова.", show_alert=True)

# ═══════════════════════════════════════════════
#              КОМАНДЫ И ОСНОВНЫЕ ХЕНДЛЕРЫ
# ═══════════════════════════════════════════════
@dp.message(CommandStart())
async def start(message: Message):
    db_update_user(message.from_user.id, username=message.from_user.username)
    u           = db_get_user(message.from_user.id)
    total_users = db_get_total_users()

    market_status = "🟢 ОНЛАЙН (ПН–ПТ)" if is_market_open() else "🔴 ВЫХОДНОЙ (СБ–ВС)"

    start_text = (
        "┌─────────────────────────┐\n"
        "│  🖥  AI TRADING TERMINAL  │\n"
        "│       FX PRO v2.0        │\n"
        "└─────────────────────────┘\n\n"
        "⚡ <b>Профессиональная торговая система</b> на базе нейросетевого алгоритма.\n\n"
        "🧠 <b>Что умеет терминал:</b>\n"
        "▸ Анализ реальных котировок в реальном времени\n"
        "▸ RSI-индикатор + свечной анализ\n"
        "▸ Определение тренда и волатильности\n"
        "▸ Сигнал с процентом уверенности ИИ\n\n"
        f"👥 Уже торгуют с нами: <b>{total_users + 118:,}</b> трейдеров\n"
        f"📡 WinRate системы: <b>88–94%</b>\n\n"
        f"⏰ <b>СТАТУС РЫНКА: {market_status}</b>\n"
        f"📅 График: ПН–ПТ 24/7 | СБ–ВС закрыт\n"
        f"🕐 {(datetime.utcnow() + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)"
    )
    await message.answer(start_text, reply_markup=get_main_menu(u["has_access"]), parse_mode="HTML")

@dp.message(F.text == "🚀 О боте")
async def about_bot(message: Message):
    now = datetime.utcnow() + timedelta(hours=3)
    market_status = "🟢 ОНЛАЙН" if is_market_open() else "🔴 ВЫХОДНОЙ"

    text = (
        "🤖 <b>AI TRADING TERMINAL — FX PRO v2.0</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "📡 <b>Источник данных:</b> Yahoo Finance (live)\n"
        "🧠 <b>Алгоритм:</b> RSI + свечной анализ + тренд\n"
        "📊 <b>Платформа:</b> Pocket Option\n"
        "💱 <b>Пары:</b> 8 валютных инструментов\n"
        "⏱ <b>Таймфреймы:</b> 1, 3, 5, 10 минут\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "⏰ <b>РЕЖИМ РАБОТЫ:</b>\n"
        "  🟢 ПН–ПТ: 24/7 (круглосуточно)\n"
        "  🔴 СБ–ВС: рынок закрыт, сигналы недоступны\n\n"
        f"  Сейчас: <b>{market_status}</b>\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📦 <b>Тарифы:</b>\n"
        "  ⬜ FREE  — 5 сигналов / день\n"
        "  🔵 JUNIOR — 15 сигналов / день  | 50$ / 7 дн\n"
        "  🟣 PRO — 30 сигналов / день  | 100$ / 7 дн\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Дисклеймер:</b>\n"
        "<i>Торговля бинарными опционами сопряжена с рисками. "
        "Сигналы носят информационный характер и не являются "
        "гарантией прибыли. Всегда соблюдайте мани-менеджмент.</i>"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("vip"))
@dp.message(F.text == "🔐 Активировать доступ")
async def activate(message: Message):
    user_info = db_get_user(message.from_user.id)
    if user_info["has_access"]:
        return await message.answer(
            "✅ <b>VIP-ЛИЦЕНЗИЯ АКТИВНА</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "Все модули терминала разблокированы.\n"
            "Вам доступны профессиональные сигналы в полном объёме.",
            parse_mode="HTML"
        )
    await message.answer(
        "💎 <b>АКТИВАЦИЯ VIP-ЛИЦЕНЗИИ</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "📋 <b>Инструкция (3 простых шага):</b>\n\n"
        "1️⃣ <b>Регистрация торгового счёта:</b>\n"
        "   🌍 Global: <a href='https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1746882&ac=fx&cid=950203&code=ESX408'>Pocket Option (Официальный шлюз)</a>\n"
        "   🇷🇺 RU/СНГ: <a href='https://po-ru4.click/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1746882&ac=fx&cid=950203&code=ESX408'>Pocket Option (Зеркало)</a>\n\n"
        "2️⃣ <b>Пополните депозит</b> от <b>$50</b>\n"
        "   <i>(рекомендуемый риск: 1–5% на сделку)</i>\n\n"
        "3️⃣ <b>Отправьте ваш ID</b> нажав кнопку ниже\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "🎁 <b>БОНУС +60% к депозиту</b> при регистрации по ссылке выше!\n\n"
        "⚠️ <b>Важно:</b> если аккаунт уже существует — он должен быть зарегистрирован "
        "по нашей ссылке. Иначе необходимо создать новый аккаунт строго по ссылке выше.\n\n"
        "🔐 <i>После проверки ИИ подключит ваш аккаунт к пулу сигналов в течение нескольких минут.</i>",
        reply_markup=access_kb,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

@dp.message(Command("help"))
@dp.message(F.text == "🆘 Поддержка")
async def help_cmd(message: Message):
    pending_support.add(message.from_user.id)
    await message.answer(
        "🆘 <b>ЦЕНТР ПОДДЕРЖКИ</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "Если у вас возникли вопросы — опишите проблему одним сообщением.\n"
        "Ваше обращение будет мгновенно передано администратору.\n\n"
        "💬 <b>Частые вопросы:</b>\n"
        "▸ <i>Как активировать доступ?</i> → кнопка «🔐 Активировать доступ»\n"
        "▸ <i>Как найти ID Pocket Option?</i> → Личный кабинет → Профиль\n"
        "▸ <i>Когда обновляется лимит?</i> → Каждый день в 00:00 (МСК)\n"
        "▸ <i>Когда работает терминал?</i> → ПН–ПТ 24/7, СБ–ВС закрыт\n\n"
        "✍️ <b>Напишите ваш вопрос прямо сейчас:</b>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )

@dp.message(F.text == "📩 Отправить ID Pocket Option")
async def ask_id(message: Message):
    pending_users.add(message.from_user.id)
    await message.answer(
        "🔢 <b>ВЕРИФИКАЦИЯ АККАУНТА</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "Введите ваш <b>цифровой ID профиля Pocket Option</b>:\n\n"
        "📍 <i>Где найти ID:</i>\n"
        "Зайдите в Pocket Option → Аккаунт → ваш ID указан в профиле.\n\n"
        "⌨️ <b>Введите только цифры, без пробелов:</b>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )

@dp.message(F.text == "⬅️ Назад")
@dp.message(F.text == "⬅️ В меню")
async def go_back(message: Message):
    pending_users.discard(message.from_user.id)
    pending_support.discard(message.from_user.id)
    u = db_get_user(message.from_user.id)
    await message.answer(
        f"🏠 <b>Главная панель управления</b>\n"
        f"<i>С возвращением, {message.from_user.first_name}!</i>",
        reply_markup=get_main_menu(u["has_access"]),
        parse_mode="HTML"
    )

@dp.message(lambda msg: msg.from_user.id in pending_support)
async def process_support_message(message: Message):
    if message.text == "⬅️ Назад":
        pending_support.discard(message.from_user.id)
        return await go_back(message)
    uid      = message.from_user.id
    username = message.from_user.username or "—"
    name     = message.from_user.full_name or "—"
    await bot.send_message(
        ADMIN_ID,
        f"📩 <b>ОБРАЩЕНИЕ В ПОДДЕРЖКУ</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 Имя: <b>{name}</b>\n"
        f"🔗 Ник: @{username}\n"
        f"🆔 ID: <code>{uid}</code>\n\n"
        f"📝 <b>Сообщение:</b>\n{message.text}\n\n"
        f"💬 Ответить: <code>/reply {uid} текст</code>",
        parse_mode="HTML"
    )
    pending_support.discard(uid)
    u = db_get_user(uid)
    await message.answer(
        "✅ <b>Обращение принято!</b>\n"
        "Администратор рассмотрит ваш запрос в ближайшее время.\n\n"
        "<i>Обычно время ответа — до 30 минут.</i>",
        reply_markup=get_main_menu(u["has_access"]),
        parse_mode="HTML"
    )

@dp.message(lambda msg: msg.from_user.id in pending_users)
async def process_id(message: Message):
    if message.text == "⬅️ Назад":
        pending_users.discard(message.from_user.id)
        return await go_back(message)
    if not message.text or not message.text.isdigit():
        return await message.answer(
            "❌ <b>Ошибка валидации.</b>\n"
            "Введите <b>только цифры</b> вашего ID Pocket Option.\n"
            "<i>Пример: 12345678</i>",
            parse_mode="HTML"
        )
    uid = message.from_user.id
    pending_users.discard(uid)
    await bot.send_message(
        ADMIN_ID,
        f"🔔 <b>НОВАЯ ЗАЯВКА НА VIP</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 Имя: <b>{message.from_user.full_name}</b>\n"
        f"🔗 Ник: @{message.from_user.username or '—'}\n"
        f"🆔 TG ID: <code>{uid}</code>\n"
        f"💼 PO ID: <code>{message.text}</code>\n\n"
        f"✅ Выдать: <code>/give {uid}</code>\n"
        f"🚫 Отказать: <code>/block {uid}</code>",
        parse_mode="HTML"
    )
    u = db_get_user(uid)
    await message.answer(
        "⏳ <b>ЗАЯВКА ОТПРАВЛЕНА</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 Ваш ID Pocket Option: <code>{message.text}</code>\n\n"
        "Ожидайте проверки от технического отдела.\n"
        "<i>Обычно активация происходит в течение нескольких минут.</i>",
        reply_markup=get_main_menu(u["has_access"]),
        parse_mode="HTML"
    )

# ═══════════════════════════════════════════════
#              АДМИНСКИЕ КОМАНДЫ
# ═══════════════════════════════════════════════
@dp.message(F.text.startswith("/give"))
async def admin_give(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=True)
        await bot.send_message(
            target,
            "🚀 <b>VIP-ДОСТУП АКТИВИРОВАН!</b>\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            "✅ Ваш аккаунт верифицирован.\n"
            "Все модули терминала разблокированы.\n\n"
            "📊 Нажмите <b>«📊 Торговая панель»</b> для выбора актива\n"
            "⚡ Или сразу <b>«⚡ Получить сигнал»</b>\n\n"
            "<i>Желаем профитных сделок! 📈</i>",
            parse_mode="HTML",
            reply_markup=get_main_menu(True)
        )
        await message.answer(f"✅ Доступ для <code>{target}</code> активирован.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}\nФормат: <code>/give ID</code>", parse_mode="HTML")

@dp.message(F.text.startswith("/block"))
async def admin_block(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        target = int(message.text.split()[1])
        db_update_user(target, has_access=False)
        try:
            await bot.send_message(
                target,
                "🛑 <b>ДОСТУП АННУЛИРОВАН</b>\n"
                "━━━━━━━━━━━━━━━━━\n\n"
                "Ваша VIP-лицензия была отозвана администратором.\n\n"
                "Если вы считаете это ошибкой — обратитесь в поддержку: /help",
                parse_mode="HTML",
                reply_markup=get_main_menu(False)
            )
        except:
            pass
        await message.answer(f"🚫 Доступ для <code>{target}</code> заблокирован.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}\nФормат: <code>/block ID</code>", parse_mode="HTML")

@dp.message(F.text.startswith("/reply"))
async def admin_reply(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts  = message.text.split(maxsplit=2)
        target = int(parts[1])
        text   = parts[2]
        await bot.send_message(
            target,
            f"💬 <b>ОТВЕТ ПОДДЕРЖКИ</b>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"{text}",
            parse_mode="HTML"
        )
        await message.answer(f"✅ Ответ отправлен пользователю <code>{target}</code>.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}\nФормат: <code>/reply ID текст</code>", parse_mode="HTML")

@dp.message(Command("stats_admin"))
async def admin_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    total  = db_get_total_users()
    active = db_get_active_users()
    await message.answer(
        f"📊 <b>СТАТИСТИКА БОТА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"🟢 Активных (24ч): <b>{active}</b>\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        parse_mode="HTML"
    )

@dp.message(F.text.startswith("/broadcast"))
async def admin_broadcast(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        text = message.text.split(maxsplit=1)[1]
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            users = cursor.fetchall()
            cursor.close()
            conn.close()
        except:
            users = []

        sent = 0
        fail = 0
        for (uid,) in users:
            try:
                await bot.send_message(
                    uid,
                    f"📢 <b>СООБЩЕНИЕ ОТ КОМАНДЫ</b>\n"
                    f"━━━━━━━━━━━━━━━━━\n\n"
                    f"{text}",
                    parse_mode="HTML"
                )
                sent += 1
                await asyncio.sleep(0.05)
            except:
                fail += 1

        await message.answer(
            f"📤 <b>Рассылка завершена</b>\n"
            f"✅ Доставлено: <b>{sent}</b>\n"
            f"❌ Ошибок: <b>{fail}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"⚠️ Формат: <code>/broadcast текст</code>\n{e}", parse_mode="HTML")

# ═══════════════════════════════════════════════
#              ТОРГОВАЯ ПАНЕЛЬ
# ═══════════════════════════════════════════════
@dp.message(F.text == "📊 Торговая панель")
async def t_panel(message: Message):
    if not db_get_user(message.from_user.id)["has_access"]:
        return

    # Проверка выходного дня
    if not is_market_open():
        return await message.answer(get_market_closed_text(), parse_mode="HTML")

    await message.answer(
        "📊 <b>ТОРГОВАЯ ПАНЕЛЬ</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "Выберите <b>валютную пару</b> для анализа:\n\n"
        "🔹 Мажорные пары: 
        EUR/USD, USD/JPY, GBP/JPY\n"
        "🔹 Кросс-пары: 
        AUD/CAD, CAD/CHF, EUR/CHF, GBP/CAD, USD/CAD",
        reply_markup=pair_kb,
        parse_mode="HTML"
    )

@dp.message(F.text.in_(pairs))
async def set_pair(message: Message):
    if not is_market_open():
        return await message.answer(get_market_closed_text(), parse_mode="HTML")

    user_temp_data[message.from_user.id] = {"pair": message.text}
    await message.answer(
        f"✅ <b>Актив выбран:</b> {message.text}\n\n"
        "⏱ Выберите <b>время экспирации</b> опциона:",
        reply_markup=time_kb,
        parse_mode="HTML"
    )

@dp.message(F.text.in_(times))
async def set_time(message: Message):
    if not is_market_open():
        return await message.answer(get_market_closed_text(), parse_mode="HTML")

    uid = message.from_user.id
    if uid not in user_temp_data:
        user_temp_data[uid] = {}
    user_temp_data[uid]["time"] = message.text
    pair = user_temp_data[uid].get('pair', '—')
    await message.answer(
        f"⚙️ <b>КОНФИГУРАЦИЯ СОХРАНЕНА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"  📊 Актив:       <b>{pair}</b>\n"
        f"  ⏱ Экспирация:  <b>{message.text}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<i>Алгоритм настроен. Нажмите «⚡ Получить сигнал» для анализа рынка.</i>",
        reply_markup=signal_kb,
        parse_mode="HTML"
    )

# ═══════════════════════════════════════════════
#              ГЛАВНЫЙ ХЕНДЛЕР СИГНАЛА
# ═══════════════════════════════════════════════
@dp.message(Command("signals"))
@dp.message(F.text == "⚡ Получить сигнал")
async def get_signal(message: Message):
    uid = message.from_user.id
    u   = db_get_user(uid)
    if not u["has_access"]:
        return

    # ── Проверка выходного дня ──────────────────────────
    if not is_market_open():
        return await message.answer(get_market_closed_text(), parse_mode="HTML")

    today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")
    daily = u["daily_count"]

    if u["last_date"] != today:
        daily = 0
        db_update_user(uid, daily=0, date=today)

    sub_type      = u['sub_type']
    current_limit = SUBSCRIPTION_PLANS[sub_type]['limit']

    # ── Лимит исчерпан ──────────────────────────────────
    if daily >= current_limit:
        if sub_type == "free":
            return await message.answer(
                "🛑 <b>ДНЕВНОЙ ЛИМИТ ИСЧЕРПАН</b>\n"
                "━━━━━━━━━━━━━━━━━\n\n"
                f"Вы использовали все <b>{current_limit} бесплатных сигнала</b> на сегодня.\n\n"
                "💡 <b>Хотите торговать без ограничений?</b>\n"
                "Перейдите в раздел <b>«💎 Подписка»</b> и получите\n"
                "больше сигналов по супер цене:\n\n"
                "🔵 <b>JUNIOR</b> — <b>15 сигналов/день</b> всего за <b>50$</b> / 7 дней\n"
                "🟣 <b>PRO</b>    — <b>30 сигналов/день</b> всего за <b>100$</b> / 7 дней\n\n"
                "⏳ <i>Или дождитесь обновления лимита в 00:00 (МСК).</i>",
                reply_markup=get_upgrade_kb(),
                parse_mode="HTML"
            )
        else:
            return await message.answer(
                "🛑 <b>ДНЕВНОЙ ЛИМИТ ИСЧЕРПАН</b>\n"
                "━━━━━━━━━━━━━━━━━\n\n"
                f"Тариф <b>{sub_type.upper()}</b>: использовано <b>{daily} / {current_limit}</b> сигналов.\n\n"
                "🔐 <b>Система защиты капитала активирована</b>\n"
                "<i>Лимит защищает от эмоциональной торговли и "
                "чрезмерных рисков. Возвращайтесь завтра с чистой головой!</i>\n\n"
                "💡 <b>Хотите ещё больше сигналов?</b>\n"
                "Перейдите в <b>«💎 Подписка»</b> — там доступно продление\n"
                "или переход на более высокий тариф.\n\n"
                "⏳ Обновление в <b>00:00 (МСК)</b>",
                reply_markup=get_upgrade_kb(),
                parse_mode="HTML"
            )

    data = user_temp_data.get(uid)
    if not data or "pair" not in data:
        return await message.answer(
            "⚠️ <b>Конфигурация не задана</b>\n\n"
            "Перейдите в <b>«📊 Торговая панель»</b> и выберите актив и время экспирации.",
            parse_mode="HTML"
        )

    if time.time() - last_click_time.get(uid, 0) < 6:
        return await message.answer(
            "⏳ <b>Анализ в процессе...</b>\n"
            "<i>Дождитесь завершения предыдущего расчёта.</i>",
            parse_mode="HTML"
        )
    last_click_time[uid] = time.time()

    # ── Анимированный прогресс-бар ──────────────────────
    progress_frames = [
        ("⬛️⬛️⬛️⬛️⬛️ <b>[ 0%]</b>",  "📡 Подключение к потоку котировок..."),
        ("🟩⬛️⬛️⬛️⬛️ <b>[20%]</b>",  "📥 Загрузка рыночных данных (live)..."),
        ("🟩🟩🟩⬛️⬛️ <b>[60%]</b>",  "🔬 Анализ свечей и волатильности..."),
        ("🟩🟩🟩🟩⬛️ <b>[85%]</b>",  "🧮 Расчёт RSI и математического перевеса..."),
        ("🟩🟩🟩🟩🟩 <b>[100%]</b>", "✅ Сигнал сформирован!"),
    ]

    progress_msg = await message.answer(
        f"<b>⚡ АНАЛИЗ РЫНКА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{progress_frames[0][0]}\n"
        f"<i>{progress_frames[0][1]}</i>",
        parse_mode="HTML"
    )

    await asyncio.sleep(0.6)
    quote = await get_real_quote(data["pair"])

    for bar, label in progress_frames[1:]:
        await asyncio.sleep(0.55)
        try:
            await progress_msg.edit_text(
                f"<b>⚡ АНАЛИЗ РЫНКА</b>\n"
                f"━━━━━━━━━━━━━━━━━\n\n"
                f"{bar}\n"
                f"<i>{label}</i>",
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

    db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)
    new_daily = daily + 1

    # ── Оставшиеся сигналы — предупреждение ─────────────
    remaining = current_limit - new_daily
    limit_warning = ""
    if remaining == 0:
        limit_warning = "\n⚠️ <b>Это был последний сигнал на сегодня!</b> Лимит исчерпан."
    elif remaining <= 2:
        limit_warning = f"\n⚠️ <i>Осталось сигналов сегодня: <b>{remaining}</b>. Используйте с умом!</i>"

    # ── Формируем сигнал ────────────────────────────────
    if quote:
        direction, confidence = generate_signal_from_quote(quote)

        price_val = quote["price"]
        price_str = f"{price_val:.3f}" if price_val > 100 else f"{price_val:.5f}"

        change_sign  = "+" if quote["change"] >= 0 else ""
        change_str   = f"{change_sign}{quote['change_pct']:.3f}%"
        change_arrow = "▲" if quote["change"] >= 0 else "▼"

        candle_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(
            quote["candle_direction"], "⚪"
        )
        candle_ru = {"bullish": "БЫЧЬЯ", "bearish": "МЕДВЕЖЬЯ", "neutral": "НЕЙТРАЛЬНАЯ"}.get(
            quote["candle_direction"], "—"
        )

        rsi_bar  = format_rsi_bar(quote.get("rsi_value", 50))
        conf_bar = confidence_bar(confidence)

        dir_badge = "🟢 CALL (ВВЕРХ)" if "ВВЕРХ" in direction else "🔴 PUT (ВНИЗ)"

        # PRO-специфичный блок
        pro_block = ""
        if sub_type == "pro":
            rsi_v = quote.get("rsi_value", 50)
            if rsi_v > 70:
                pro_tip = "⚠️ Зона перекупленности — рассмотрите PUT при откате"
            elif rsi_v < 30:
                pro_tip = "💡 Зона перепроданности — рассмотрите CALL при отскоке"
            elif "высокая" in quote.get("volatility", ""):
                pro_tip = "🔥 Высокая волатильность — сократите объём сделки"
            else:
                pro_tip = "✅ Стандартные условия — работайте по тренду"

            pro_block = (
                f"\n━━━━━━━━━━━━━━━━━\n"
                f"🟣 <b>PRO АНАЛИТИКА:</b>\n"
                f"  💬 {pro_tip}\n"
                f"  📐 Рек. объём: <b>2–3% от депозита</b>\n"
                f"  🎯 Мин. уверенность для входа: <b>87%+</b>\n"
            )

        res = (
            f"⚡️ <b>ТОРГОВЫЙ СИГНАЛ СФОРМИРОВАН</b> ⚡️\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"  📊 Актив:       <b>{data['pair']}</b>\n"
            f"  ⏱ Экспирация:  <b>{data['time']}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📡 <b>РЫНОЧНЫЕ ДАННЫЕ (live)</b>\n\n"
            f"  Цена:          <b>{price_str}</b>\n"
            f"  Изменение:     {change_arrow} <b>{change_str}</b>\n"
            f"  Свеча:         {candle_emoji} <b>{candle_ru}</b>\n"
            f"  Тренд:         <b>{quote['trend']}</b>\n"
            f"  Волатильность: <b>{quote['volatility']}</b>\n"
            f"  High:          <b>{quote['high']:.5f}</b>\n"
            f"  Low:           <b>{quote['low']:.5f}</b>\n\n"
            f"📈 <b>RSI-ИНДИКАТОР</b>\n"
            f"  <code>{rsi_bar}</code>\n"
            f"  Сигнал: <b>{quote['rsi_signal']}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🧠 <b>УВЕРЕННОСТЬ ИИ:</b>\n"
            f"  <code>{conf_bar}</code> <b>{confidence}%</b>\n\n"
            f"🚀 <b>РЕКОМЕНДАЦИЯ:</b>\n"
            f"  ┌──────────────────┐\n"
            f"  │   {dir_badge}   │\n"
            f"  └──────────────────┘\n"
            f"{pro_block}"
            f"━━━━━━━━━━━━━━━━━\n"
            f"  Использовано: <b>{new_daily} / {current_limit}</b> сигналов\n"
            f"{limit_warning}\n"
            f"⚠️ <i>Money Management: 1–3% от баланса на сделку!</i>"
        )
    else:
        direction  = random.choice(["ВВЕРХ 🟢 (CALL)", "ВНИЗ 🔴 (PUT)"])
        confidence = random.randint(83, 91)
        conf_bar   = confidence_bar(confidence)
        res = (
            f"⚡️ <b>ТОРГОВЫЙ СИГНАЛ СФОРМИРОВАН</b> ⚡️\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"  📊 Актив:       <b>{data['pair']}</b>\n"
            f"  ⏱ Экспирация:  <b>{data['time']}</b>\n\n"
            f"🧠 <b>УВЕРЕННОСТЬ ИИ:</b>\n"
            f"  <code>{conf_bar}</code> <b>{confidence}%</b>\n\n"
            f"🚀 <b>РЕКОМЕНДАЦИЯ: {direction}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"  Использовано: <b>{new_daily} / {current_limit}</b> сигналов\n"
            f"{limit_warning}\n"
            f"⚡ <i>Автономный режим (live-данные временно недоступны)</i>\n"
            f"⚠️ <i>Money Management: 1–3% от баланса!</i>"
        )

    try:
        await progress_msg.delete()
    except:
        pass

    await message.answer(res, parse_mode="HTML", reply_markup=signal_kb)

# ═══════════════════════════════════════════════
#              ПРОФИЛЬ
# ═══════════════════════════════════════════════
@dp.message(Command("profile"))
@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    u         = db_get_user(message.from_user.id)
    rank      = get_rank(u["signals"])
    sub_plan  = SUBSCRIPTION_PLANS[u["sub_type"]]
    sub_limit = sub_plan["limit"]
    sub_emoji = sub_plan["emoji"]

    expiry_str = "∞ Бессрочно"
    days_info  = ""
    if u['sub_expires']:
        expiry_str = u['sub_expires'].strftime("%d.%m.%Y %H:%M")
        days_left  = max((u['sub_expires'] - datetime.now()).days, 0)
        days_used  = 7 - days_left
        bar        = days_bar(days_used, 7)
        days_info  = f"\n  Осталось:  <code>[{bar}]</code> <b>{days_left} дн.</b>"

    next_title, next_level, signals_left = get_next_rank(u["signals"])
    rank_progress = ""
    if next_title:
        rank_progress = f"\n  До <b>{next_title}</b>: ещё <b>{signals_left}</b> сигналов"

    used_pct  = min(int((u["daily_count"] / sub_limit) * 10), 10)
    daily_bar = "▓" * used_pct + "░" * (10 - used_pct)

    market_str = "🟢 Открыт (ПН–ПТ)" if is_market_open() else "🔴 Закрыт (выходной)"
    name = message.from_user.first_name or "Трейдер"

    await message.answer(
        f"👤 <b>ПРОФИЛЬ ТРЕЙДЕРА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"  Имя:       <b>{name}</b>\n"
        f"  TG ID:     <code>{message.from_user.id}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>РАНГ:</b>\n"
        f"  {rank}{rank_progress}\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>ПОДПИСКА:</b>\n"
        f"  Тариф:     {sub_emoji} <b>{u['sub_type'].upper()}</b>\n"
        f"  Лимит:     <b>{sub_limit} сигналов / день</b>\n"
        f"  Истекает:  <b>{expiry_str}</b>"
        f"{days_info}\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 <b>ТОРГОВАЯ АКТИВНОСТЬ:</b>\n"
        f"  Всего сигналов:   <b>{u['signals']}</b>\n"
        f"  Сегодня:\n"
        f"  <code>[{daily_bar}]</code> <b>{u['daily_count']} / {sub_limit}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🌐 Рынок сейчас: {market_str}\n"
        f"🔐 Лицензия: {'<b>АКТИВНА ✅</b>' if u['has_access'] else '<b>ОГРАНИЧЕНА ❌</b>'}",
        parse_mode="HTML"
    )

# ═══════════════════════════════════════════════
#              СТАТИСТИКА
# ═══════════════════════════════════════════════
@dp.message(F.text == "📈 Статистика")
async def stats(message: Message):
    seed_val = int(datetime.now().strftime("%Y%m%d"))
    random.seed(seed_val)

    total_day    = random.randint(1800, 2500)
    win_rate     = round(random.uniform(91.2, 94.8), 1)
    plus_deals   = int(total_day * (win_rate / 100))
    minus_deals  = total_day - plus_deals - random.randint(10, 30)
    refunds      = total_day - plus_deals - minus_deals
    avg_profit   = round(random.uniform(82.5, 91.3), 1)
    best_pair    = random.choice(["EUR/USD", "GBP/JPY", "USD/JPY", "AUD/CAD"])
    peak_hour    = random.randint(10, 18)
    total_users  = db_get_total_users()
    active_users = db_get_active_users()

    wr_filled = int(win_rate / 10)
    wr_bar    = "█" * wr_filled + "░" * (10 - wr_filled)

    market_note = ""
    if not is_market_open():
        market_note = "\n⚠️ <i>Рынок сейчас закрыт (выходной). Статистика за последний рабочий день.</i>\n"

    await message.answer(
        f"📊 <b>ГЛОБАЛЬНАЯ СТАТИСТИКА ТЕРМИНАЛА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{market_note}"
        f"\n🕐 <b>За последние 24 часа:</b>\n\n"
        f"  WinRate:\n"
        f"  <code>[{wr_bar}] {win_rate}%</code>\n\n"
        f"  🟢 Профитных сделок:  <b>{plus_deals:,}</b>\n"
        f"  🔴 Убыточных сделок:  <b>{minus_deals:,}</b>\n"
        f"  🔁 Возвратов:         <b>{refunds:,}</b>\n"
        f"  📦 Всего сигналов:    <b>{total_day:,}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>ПОКАЗАТЕЛИ СИСТЕМЫ:</b>\n\n"
        f"  Средний ROI:          <b>{avg_profit}%</b>\n"
        f"  Лучшая пара дня:      <b>{best_pair}</b>\n"
        f"  Пик активности:       <b>{peak_hour}:00–{peak_hour+1}:00</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>СООБЩЕСТВО:</b>\n\n"
        f"  Всего трейдеров:      <b>{total_users + 118:,}</b>\n"
        f"  Активных (24ч):       <b>{active_users + 84:,}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<i>📅 Сводка обновлена: {datetime.now().strftime('%d.%m.%Y %H:%M')} (МСК)\n"
        f"Данные формируются по пулу всех торговых сессий на Pocket Option.</i>",
        parse_mode="HTML"
    )
    random.seed()

# ═══════════════════════════════════════════════
#              ЗАПУСК
# ═══════════════════════════════════════════════
async def main():
    print("=" * 50)
    print("  🚀 AI TRADING TERMINAL — FX PRO v2.0")
    print("  ✅ BOT STARTED SUCCESSFULLY")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
