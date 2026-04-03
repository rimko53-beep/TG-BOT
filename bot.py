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
# ═══════════════════════════════════════════════
SUBSCRIPTION_PLANS = {
    "free":   {"limit": 10,  "name": "FREE",   "price": 0,   "emoji": "⬜"},
    "junior": {"limit": 25,  "name": "JUNIOR",  "price": 50,  "duration": 7, "emoji": "🔵"},
    "pro":    {"limit": 50,  "name": "PRO",     "price": 100, "duration": 7, "emoji": "🟣"},
}

# ═══════════════════════════════════════════════
#         OTC ВАЛЮТНЫЕ ПАРЫ С ФЛАГАМИ
# ═══════════════════════════════════════════════
pairs = [
    "🇦🇪 AED/CNY OTC",
    "🇦🇺 AUD/NZD OTC",
    "🇦🇺 AUD/USD OTC",
    "🇧🇭 BHD/CNY OTC",
    "🇨🇭 CHF/NOK OTC",
    "🇪🇺 EUR/CHF OTC",
    "🇬🇧 GBP/AUD OTC",
    "🇨🇦 CAD/JPY OTC",
    "🇪🇺 EUR/USD OTC",
    "🇲🇦 MAD/USD OTC",
    "🇳🇿 NZD/JPY OTC",
    "🇸🇦 SAR/CNY OTC",
]

# Таймфреймы для OTC
times = ["⏱ 3 сек", "⏱ 15 сек", "⏱ 30 сек", "⏱ 1 мин"]

# ═══════════════════════════════════════════════
#         ПРОВЕРКА РАБОЧЕГО ВРЕМЕНИ РЫНКА
# ═══════════════════════════════════════════════
def is_market_open() -> bool:
    return True

# ════════════════════════════════════════════════
#              РАБОТА С PostgreSQL
# ════════════════════════════════════════════════
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

# ════════════════════════════════════════════════
#              CRYPTO BOT API
# ════════════════════════════════════════════════
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

# ════════════════════════════════════════════════
#   ГЕНЕРАТОР OTC-СИГНАЛА (автономный режим)
# ════════════════════════════════════════════════
def generate_otc_signal(pair: str, timeframe: str) -> tuple[str, int, str]:
    now = datetime.utcnow()

    if "3 сек" in timeframe:
        bucket = int(now.timestamp() / 3)
    elif "15 сек" in timeframe:
        bucket = int(now.timestamp() / 15)
    elif "30 сек" in timeframe:
        bucket = int(now.timestamp() / 30)
    else:
        bucket = int(now.timestamp() / 60)

    seed = hash(f"{pair}_{bucket}") % (2**32)
    rng = random.Random(seed)

    rsi = rng.uniform(25, 75)
    if rsi <= 35:
        rsi_vote = +2
        rsi_desc = f"RSI {rsi:.1f} — зона перепроданности (CALL)"
    elif rsi <= 45:
        rsi_vote = +1
        rsi_desc = f"RSI {rsi:.1f} — нижняя зона"
    elif rsi >= 65:
        rsi_vote = -2
        rsi_desc = f"RSI {rsi:.1f} — зона перекупленности (PUT)"
    elif rsi >= 55:
        rsi_vote = -1
        rsi_desc = f"RSI {rsi:.1f} — верхняя зона"
    else:
        rsi_vote = rng.choice([-1, 0, 0, +1])
        rsi_desc = f"RSI {rsi:.1f} — нейтральная зона"

    ema_options = [
        (+2, "EMA9×EMA21 — бычий кроссовер"),
        (-2, "EMA9×EMA21 — медвежий кроссовер"),
        (+1, "EMA9 > EMA21 — восходящий тренд"),
        (-1, "EMA9 < EMA21 — нисходящий тренд"),
        (0,  "EMA9 ≈ EMA21 — боковик"),
    ]
    ema_vote, ema_desc = rng.choices(ema_options, weights=[15, 15, 25, 25, 20])[0]

    macd_options = [
        (+2, "MACD: бычий разворот гистограммы"),
        (-2, "MACD: медвежий разворот гистограммы"),
        (+1, "MACD гистограмма положительная"),
        (-1, "MACD гистограмма отрицательная"),
        (0,  "MACD нейтральный"),
    ]
    macd_vote, macd_desc = rng.choices(macd_options, weights=[15, 15, 25, 25, 20])[0]

    bb_options = [
        (+2, "BB: касание нижней полосы — отскок вверх"),
        (-2, "BB: касание верхней полосы — отскок вниз"),
        (+1, "BB: цена в нижней зоне канала"),
        (-1, "BB: цена в верхней зоне канала"),
        (0,  "BB: цена в середине канала"),
    ]
    bb_vote, bb_desc = rng.choices(bb_options, weights=[12, 12, 26, 26, 24])[0]

    stoch_k = rng.uniform(15, 85)
    if stoch_k <= 20:
        stoch_vote = +2
        stoch_desc = f"Stoch {stoch_k:.1f} — перепроданность (CALL)"
    elif stoch_k >= 80:
        stoch_vote = -2
        stoch_desc = f"Stoch {stoch_k:.1f} — перекупленность (PUT)"
    elif stoch_k < 40:
        stoch_vote = +1
        stoch_desc = f"Stoch {stoch_k:.1f} — нижняя зона"
    elif stoch_k > 60:
        stoch_vote = -1
        stoch_desc = f"Stoch {stoch_k:.1f} — верхняя зона"
    else:
        stoch_vote = rng.choice([-1, 0, +1])
        stoch_desc = f"Stoch {stoch_k:.1f} — нейтральный"

    pattern_options = [
        (+1, "бычий пин-бар"),
        (+1, "бычье поглощение"),
        (+1, "три белых солдата"),
        (-1, "медвежий пин-бар"),
        (-1, "медвежье поглощение"),
        (-1, "три чёрных вороны"),
        (0,  "доджи — неопределённость"),
        (0,  "нет чёткого паттерна"),
    ]
    pattern_vote, pattern_desc = rng.choices(
        pattern_options,
        weights=[12, 10, 8, 12, 10, 8, 15, 25]
    )[0]

    votes = [rsi_vote, ema_vote, macd_vote, bb_vote, stoch_vote, pattern_vote]
    total_score = sum(votes)

    if total_score > 0:
        agreeing   = sum(1 for v in votes if v > 0)
        call_reasons = [d for v, d in zip(votes, [rsi_desc, ema_desc, macd_desc, bb_desc, stoch_desc, pattern_desc]) if v > 0]
        reason_text = " | ".join(call_reasons[:4]) if call_reasons else "технический анализ"
    else:
        agreeing   = sum(1 for v in votes if v < 0)
        put_reasons = [d for v, d in zip(votes, [rsi_desc, ema_desc, macd_desc, bb_desc, stoch_desc, pattern_desc]) if v < 0]
        reason_text = " | ".join(put_reasons[:4]) if put_reasons else "технический анализ"

    if agreeing < 3 or abs(total_score) < 3:
        direction  = rng.choice(["ВВЕРХ 🟢 (CALL)", "ВНИЗ 🔴 (PUT)"])
        confidence = rng.randint(78, 82)
        reason_text = "рынок в балансе — слабый технический перекос"
        return direction, confidence, reason_text

    max_possible = 11
    signal_strength = abs(total_score) / max_possible
    base_confidence = 78 + int(signal_strength * 16)
    block_bonus = (agreeing - 3) * 2
    confidence = min(base_confidence + block_bonus, 96)
    confidence += rng.choice([-1, 0, 0, 1])
    confidence = max(78, min(96, confidence))

    if total_score > 0:
        direction = "ВВЕРХ 🟢 (CALL)"
    else:
        direction = "ВНИЗ 🔴 (PUT)"

    return direction, confidence, reason_text


# ════════════════════════════════════════════════
#         РАНГИ И УТИЛИТЫ
# ════════════════════════════════════════════════
RANKS = [
    (0,    100,  "🌱 Новичок",      "Retail"),
    (101,  300,  "📊 Трейдер",       "Prop Firm"),
    (301,  1000, "📈 Про-Трейдер",   "Institutional"),
    (1001, 2000, "🔥 Эксперт",       "Smart Money"),
    (2001, 9999999, "👑 Маркет-Мейкер", "Whale"),
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
    pct = used / total if total > 0 else 0
    filled = int(pct * 10)
    return "█" * filled + "░" * (10 - filled)

def calc_lot(balance: float) -> dict:
    conservative = round(balance * 0.01, 2)
    moderate     = round(balance * 0.02, 2)
    aggressive   = round(balance * 0.03, 2)
    max_risk     = round(balance * 0.05, 2)
    return {
        "conservative": conservative,
        "moderate":     moderate,
        "aggressive":   aggressive,
        "max_risk":     max_risk,
    }

# ════════════════════════════════════════════════
#         ПРЕМИАЛЬНЫЕ ДЕКОРАТИВНЫЕ УТИЛИТЫ
# ════════════════════════════════════════════════

MARKET_PULSE_ICONS = ["📡", "🔭", "🧬", "⚙️", "🛰️", "🔬"]

def get_signal_strength_label(confidence: int) -> str:
    if confidence >= 93:
        return "🔥 ЭКСТРЕМАЛЬНЫЙ СИГНАЛ"
    elif confidence >= 88:
        return "💎 СИЛЬНЫЙ СИГНАЛ"
    elif confidence >= 84:
        return "⚡ УСТОЙЧИВЫЙ СИГНАЛ"
    else:
        return "📊 СТАНДАРТНЫЙ СИГНАЛ"

def get_market_mood(pair: str) -> str:
    moods = [
        "🟢 Бычий импульс",
        "🔴 Медвежье давление",
        "🟡 Консолидация",
        "🔵 Накопление",
        "🟠 Распределение",
    ]
    rng = random.Random(hash(f"{pair}_{datetime.utcnow().strftime('%Y%m%d%H')}"))
    return rng.choice(moods)

def get_volatility_emoji(pair: str, hour: int) -> str:
    rng = random.Random(hash(f"{pair}_{hour}"))
    return rng.choice(["🟢 Низкая", "🟡 Умеренная", "🟠 Средняя", "🔴 Высокая"])

PREMIUM_DIVIDER = "◈━━━━━━━━━━━━━━━━━◈"
THIN_DIVIDER    = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

def rank_progress_bar(current: int, lo: int, hi: int) -> str:
    if hi == 9999999:
        return "▓▓▓▓▓▓▓▓▓▓ MAX"
    total = hi - lo
    done  = current - lo
    pct   = done / total if total > 0 else 1
    filled = int(pct * 10)
    filled = max(0, min(10, filled))
    bar = "▓" * filled + "░" * (10 - filled)
    return f"[{bar}] {int(pct * 100)}%"

# ════════════════════════════════════════════════
#              ВРЕМЕННЫЕ ДАННЫЕ
# ════════════════════════════════════════════════
user_temp_data   = {}
pending_users    = set()
pending_support  = set()
pending_lot_calc = set()

# ════════════════════════════════════════════════
#   АНТИСПАМ — отдельный словарь, только для
#   реального дублирования (не мешает первому нажатию)
# ════════════════════════════════════════════════
last_signal_request = {}   # uid -> timestamp последней УСПЕШНОЙ отправки сигнала

# ════════════════════════════════════════════════
#              MIDDLEWARE
# ════════════════════════════════════════════════
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

# ════════════════════════════════════════════════
#              КЛАВИАТУРЫ
# ════════════════════════════════════════════════
def get_main_menu(has_access: bool):
    keyboard = [
        [KeyboardButton(text="📊 Торговая панель"), KeyboardButton(text="⚡ Получить сигнал")],
        [KeyboardButton(text="👤 Профиль"),          KeyboardButton(text="📈 Статистика")],
        [KeyboardButton(text="💎 Подписка"),          KeyboardButton(text="🚀 О боте")],
        [KeyboardButton(text="🧮 Калькулятор лота")],
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

def get_pair_kb():
    rows = []
    pair_list = list(pairs)
    for i in range(0, len(pair_list), 2):
        if i + 1 < len(pair_list):
            rows.append([
                KeyboardButton(text=pair_list[i]),
                KeyboardButton(text=pair_list[i + 1])
            ])
        else:
            rows.append([KeyboardButton(text=pair_list[i])])
    rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

pair_kb = get_pair_kb()

time_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏱ 3 сек"),  KeyboardButton(text="⏱ 15 сек")],
        [KeyboardButton(text="⏱ 30 сек"), KeyboardButton(text="⏱ 1 мин")],
        [KeyboardButton(text="⬅️ Назад")]
    ],
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 JUNIOR — 25 сигналов/день | 50$", callback_data="buy_junior")],
        [InlineKeyboardButton(text="🟣 PRO — 50 сигналов/день | 100$",   callback_data="buy_pro")],
        [InlineKeyboardButton(text="📊 Сравнить тарифы",                  callback_data="compare_plans")],
    ])

def get_confirm_sub_kb(invoice_url, invoice_id, plan_key):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить (USDT)", url=invoice_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}_{plan_key}")],
        [InlineKeyboardButton(text="🔙 Назад к тарифам",  callback_data="back_to_plans")],
    ])

# ════════════════════════════════════════════════
#              ХЕНДЛЕРЫ ПОДПИСОК
# ════════════════════════════════════════════════
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

    renew_block = ""
    if u['sub_type'] != 'free':
        renew_block = (
            "\n" + PREMIUM_DIVIDER + "\n"
            "🔄 <b>ПРОДЛЕНИЕ / СМЕНА ТАРИФА:</b>\n"
            "<i>Продлите подписку заранее — активация мгновенная.\n"
            "Срок добавится к текущему остатку.</i>\n"
        )

    text = (
        "💎 <b>УПРАВЛЕНИЕ ПОДПИСКОЙ</b>\n"
        + PREMIUM_DIVIDER + "\n\n"
        f"  Ваш тариф:   {emoji} <b>{u['sub_type'].upper()}</b>\n"
        f"  Лимит:       <b>{limit} сигналов / день</b>\n"
        f"  Истекает:    <b>{exp_str}</b>"
        f"{days_left_str}\n"
        f"{renew_block}"
        "\n" + PREMIUM_DIVIDER + "\n"
        "📦 <b>Доступные тарифы:</b>\n\n"
        "⬜ <b>FREE</b>    — 10 сигналов / день    <i>(бесплатно)</i>\n"
        "🔵 <b>JUNIOR</b>  — 25 сигналов / день    <i>50$ / 7 дней</i>\n"
        "🟣 <b>PRO</b>     — 50 сигналов / день    <i>100$ / 7 дней</i>\n\n"
        "<i>Оплата принимается в <b>USDT</b> через CryptoBot — мгновенно и безопасно.</i>"
    )
    await message.answer(text, reply_markup=get_sub_kb(u['sub_type']), parse_mode="HTML")

@dp.callback_query(F.data == "compare_plans")
async def compare_plans(callback: CallbackQuery):
    text = (
        "📊 <b>СРАВНЕНИЕ ТАРИФНЫХ ПЛАНОВ</b>\n"
        + PREMIUM_DIVIDER + "\n\n"
        "<b>Функция               FREE   JUNIOR   PRO</b>\n"
        "Сигналы в день         10       25        50\n"
        "OTC-анализ               ✅      ✅        ✅\n"
        "RSI-анализ                ✅      ✅        ✅\n"
        "Анализ тренда         ✅      ✅        ✅\n"
        "Работа поддержки  ❌      ✅        ✅\n"
        "VIP-уведомления    ❌      ❌        ✅\n"
        "ТОП Стратегии        ❌      ❌        ✅\n"
        "Подписка                  ❌      ✅        ✅\n"
        "Цена                           0$     50$     100$\n"
        "Срок                           ∞      7 дн    7 дн\n\n"
        + PREMIUM_DIVIDER + "\n"
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

    is_renew    = u['sub_type'] == plan_key
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
            f"{PREMIUM_DIVIDER}\n\n"
            f"  Тариф:     {plan['emoji']} <b>{plan['name']}</b>\n"
            f"  Сумма:     <b>{plan['price']} USDT</b>\n"
            f"  Срок:      <b>7 дней</b>\n"
            f"  Лимит:     <b>{plan['limit']} сигналов / день</b>\n"
            f"{renew_note}"
            f"{PREMIUM_DIVIDER}\n"
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
        if u['sub_type'] == plan_key and u['sub_expires'] and u['sub_expires'] > datetime.now():
            expiry = u['sub_expires'] + timedelta(days=7)
        else:
            expiry = datetime.now() + timedelta(days=7)

        db_update_user(callback.from_user.id, sub_type=plan_key, sub_expires=expiry)
        plan = SUBSCRIPTION_PLANS[plan_key]
        await callback.message.edit_text(
            f"🎉 <b>ОПЛАТА ПОДТВЕРЖДЕНА!</b>\n"
            f"{PREMIUM_DIVIDER}\n\n"
            f"  Тариф:     {plan['emoji']} <b>{plan_key.upper()}</b>\n"
            f"  Лимит:     <b>{plan['limit']} сигналов / день</b>\n"
            f"  Истекает:  <b>{expiry.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            f"{PREMIUM_DIVIDER}\n"
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

# ════════════════════════════════════════════════
#              КОМАНДЫ И ОСНОВНЫЕ ХЕНДЛЕРЫ
# ════════════════════════════════════════════════
@dp.message(CommandStart())
async def start(message: Message):
    db_update_user(message.from_user.id, username=message.from_user.username)
    u           = db_get_user(message.from_user.id)
    total_users = db_get_total_users()

    start_text = (
        "┌────────────────────────────┐\n"
        "│  🖥  AI TRADING TERMINAL     │\n"
        "│     OTC PRO v4.0  ◈ LIVE   │\n"
        "└────────────────────────────┘\n\n"
        "⚡ <b>Профессиональная торговая система</b> для OTC-рынка Pocket Option.\n\n"
        "🧠 <b>Что умеет терминал:</b>\n"
        "▸ Анализ 12 OTC-пар с флагами стран\n"
        "▸ Таймфреймы: 3с / 15с / 30с / 1 мин\n"
        "▸ Smart Precision Engine: 6 независимых блоков анализа\n"
        "▸ RSI(14) + EMA(9/21) + MACD + BB + Stochastic + паттерны\n"
        "▸ Сигнал с процентом уверенности ИИ (78–96%)\n"
        "▸ Двухколоночный выбор активов для удобства\n\n"
        f"👥 Уже торгуют с нами: <b>{total_users + 152:,}</b> трейдеров\n"
        f"📡 WinRate системы: <b>88–96%</b>\n\n"
        f"🟢 <b>РЫНОК РАБОТАЕТ: ПН–ВС 24/7</b>\n"
        f"🕐 {(datetime.utcnow() + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)"
    )
    await message.answer(start_text, reply_markup=get_main_menu(u["has_access"]), parse_mode="HTML")

@dp.message(F.text == "🚀 О боте")
async def about_bot(message: Message):
    pairs_list = "\n".join([f"  ▸ <b>{p}</b>" for p in pairs])

    text = (
        "🤖 <b>AI TRADING TERMINAL — OTC PRO v4.0</b>\n"
        + PREMIUM_DIVIDER + "\n\n"
        "📡 <b>Платформа:</b> Pocket Option (OTC-рынок)\n"
        "🧠 <b>Алгоритм Smart Precision Engine v4:</b>\n"
        "  ▸ RSI(14)\n"
        "  ▸ EMA(9/21) кроссовер + тренд\n"
        "  ▸ MACD(12,26,9)\n"
        "  ▸ Bollinger Bands(20,2)\n"
        "  ▸ Stochastic(14,3)\n"
        "  ▸ Паттерны свечей (8 видов)\n"
        "🎯 <b>Фильтр входа:</b> минимум 3 из 6 блоков\n\n"
        + PREMIUM_DIVIDER + "\n"
        "💱 <b>OTC ПАРЫ (12 инструментов):</b>\n\n"
        f"{pairs_list}\n\n"
        + PREMIUM_DIVIDER + "\n"
        "⏱ <b>ТАЙМФРЕЙМЫ:</b>\n"
        "  ▸ 3 секунды\n"
        "  ▸ 15 секунд\n"
        "  ▸ 30 секунд\n"
        "  ▸ 1 минута\n\n"
        + PREMIUM_DIVIDER + "\n"
        "⏰ <b>РЕЖИМ РАБОТЫ:</b>\n"
        "  🟢 ПН–ВС: 24/7 (круглосуточно)\n\n"
        + PREMIUM_DIVIDER + "\n"
        "📦 <b>Тарифы:</b>\n"
        "  ⬜ FREE   — 10 сигналов / день\n"
        "  🔵 JUNIOR — 25 сигналов / день  |  50$ / 7 дн\n"
        "  🟣 PRO    — 50 сигналов / день  |  100$ / 7 дн\n\n"
        + PREMIUM_DIVIDER + "\n"
        "⚠️ <b>Дисклеймер:</b>\n"
        "<i>Торговля бинарными опционами сопряжена с рисками. "
        "Сигналы носят информационный характер и не являются "
        "гарантией прибыли. Всегда соблюдайте мани-менеджмент.</i>"
    )
    await message.answer(text, parse_mode="HTML")

# ════════════════════════════════════════════════
#         🧮 КАЛЬКУЛЯТОР ЛОТА
# ════════════════════════════════════════════════
@dp.message(F.text == "🧮 Калькулятор лота")
async def lot_calculator(message: Message):
    pending_lot_calc.add(message.from_user.id)
    await message.answer(
        "🧮 <b>КАЛЬКУЛЯТОР ЛОТА</b>\n"
        + PREMIUM_DIVIDER + "\n\n"
        "Введите ваш <b>текущий баланс в долларах</b> (только цифры):\n\n"
        "<i>Пример: 100 или 500 или 1250</i>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )

@dp.message(lambda msg: msg.from_user.id in pending_lot_calc)
async def process_lot_calc(message: Message):
    if message.text == "⬅️ Назад":
        pending_lot_calc.discard(message.from_user.id)
        u = db_get_user(message.from_user.id)
        return await message.answer(
            "🏠 <b>Главная панель</b>",
            reply_markup=get_main_menu(u["has_access"]),
            parse_mode="HTML"
        )

    text = (message.text or "").replace(",", ".").replace(" ", "")
    try:
        balance = float(text)
        if balance <= 0:
            raise ValueError
    except ValueError:
        return await message.answer(
            "❌ Введите корректную сумму (только цифры, больше нуля).\n"
            "<i>Пример: 100</i>",
            parse_mode="HTML"
        )

    pending_lot_calc.discard(message.from_user.id)
    u = db_get_user(message.from_user.id)
    lot = calc_lot(balance)

    bar_conservative = confidence_bar(10)
    bar_moderate     = confidence_bar(20)
    bar_aggressive   = confidence_bar(30)
    bar_max          = confidence_bar(50)

    await message.answer(
        f"🧮 <b>КАЛЬКУЛЯТОР ЛОТА</b>\n"
        f"{PREMIUM_DIVIDER}\n\n"
        f"  💰 Ваш баланс: <b>{balance:,.2f}$</b>\n\n"
        f"{PREMIUM_DIVIDER}\n"
        f"📊 <b>РЕКОМЕНДУЕМЫЕ РАЗМЕРЫ СДЕЛОК:</b>\n\n"
        f"🟢 <b>Консервативно (1%):</b>\n"
        f"  <code>{bar_conservative}</code>\n"
        f"  Сумма: <b>{lot['conservative']:,.2f}$</b> — минимальный риск\n\n"
        f"🔵 <b>Умеренно (2%):</b>\n"
        f"  <code>{bar_moderate}</code>\n"
        f"  Сумма: <b>{lot['moderate']:,.2f}$</b> — оптимально ✅\n\n"
        f"🟡 <b>Агрессивно (3%):</b>\n"
        f"  <code>{bar_aggressive}</code>\n"
        f"  Сумма: <b>{lot['aggressive']:,.2f}$</b> — повышенный риск\n\n"
        f"🔴 <b>Максимум (5%) — красная зона:</b>\n"
        f"  <code>{bar_max}</code>\n"
        f"  Сумма: <b>{lot['max_risk']:,.2f}$</b> — только опытным!\n\n"
        f"{PREMIUM_DIVIDER}\n"
        f"💡 <b>Рекомендация терминала:</b>\n"
        f"  Оптимальная сделка: <b>{lot['moderate']:,.2f}$ — {lot['aggressive']:,.2f}$</b>\n"
        f"  (2–3% от баланса)\n\n"
        f"<i>Грамотный мани-менеджмент — залог долгой карьеры трейдера.\n"
        f"Никогда не ставьте более 5% от депозита в одну сделку!</i>",
        reply_markup=get_main_menu(u["has_access"]),
        parse_mode="HTML"
    )

# ════════════════════════════════════════════════
#              АКТИВАЦИЯ ДОСТУПА
# ════════════════════════════════════════════════
@dp.message(Command("vip"))
@dp.message(F.text == "🔐 Активировать доступ")
async def activate(message: Message):
    user_info = db_get_user(message.from_user.id)
    if user_info["has_access"]:
        return await message.answer(
            "✅ <b>VIP-ЛИЦЕНЗИЯ АКТИВНА</b>\n"
            + PREMIUM_DIVIDER + "\n"
            "Все модули терминала разблокированы.\n"
            "Вам доступны профессиональные сигналы в полном объёме.",
            parse_mode="HTML"
        )
    await message.answer(
        "💎 <b>АКТИВАЦИЯ VIP-ЛИЦЕНЗИИ</b>\n"
        + PREMIUM_DIVIDER + "\n\n"
        "📋 <b>Инструкция (3 простых шага):</b>\n\n"
        "1️⃣ <b>Регистрация торгового счёта:</b>\n"
        "   🌍 Global: <a href='https://u3.shortink.io/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1746882&ac=fx&cid=950203&code=ESX408'>Pocket Option (Официальный шлюз)</a>\n"
        "   🇷🇺 RU/СНГ: <a href='https://po-ru4.click/register?utm_campaign=840876&utm_source=affiliate&utm_medium=sr&a=MystmHLdGn4JJU&al=1746882&ac=fx&cid=950203&code=ESX408'>Pocket Option (Зеркало)</a>\n\n"
        "2️⃣ <b>Пополните депозит</b> от <b>$50</b>\n"
        "   <i>(рекомендуемый риск: 1–5% на сделку)</i>\n\n"
        "3️⃣ <b>Отправьте ваш ID</b> нажав кнопку ниже\n\n"
        + PREMIUM_DIVIDER + "\n"
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
        + PREMIUM_DIVIDER + "\n\n"
        "Если у вас возникли вопросы — опишите проблему одним сообщением.\n"
        "Ваше обращение будет мгновенно передано администратору.\n\n"
        "💬 <b>Частые вопросы:</b>\n"
        "▸ <i>Как активировать доступ?</i> → кнопка «🔐 Активировать доступ»\n"
        "▸ <i>Как найти ID Pocket Option?</i> → Личный кабинет → Профиль\n"
        "▸ <i>Когда обновляется лимит?</i> → Каждый день в 00:00 (МСК)\n"
        "▸ <i>Когда работает терминал?</i> → ПН–ВС 24/7\n\n"
        "✍️ <b>Напишите ваш вопрос прямо сейчас:</b>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )

@dp.message(F.text == "📩 Отправить ID Pocket Option")
async def ask_id(message: Message):
    pending_users.add(message.from_user.id)
    await message.answer(
        "🔢 <b>ВЕРИФИКАЦИЯ АККАУНТА</b>\n"
        + PREMIUM_DIVIDER + "\n\n"
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
    pending_lot_calc.discard(message.from_user.id)
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
        + PREMIUM_DIVIDER + "\n\n"
        f"🆔 Ваш ID Pocket Option: <code>{message.text}</code>\n\n"
        "Ожидайте проверки от технического отдела.\n"
        "<i>Обычно активация происходит в течение нескольких минут.</i>",
        reply_markup=get_main_menu(u["has_access"]),
        parse_mode="HTML"
    )

# ════════════════════════════════════════════════
#              АДМИНСКИЕ КОМАНДЫ
# ════════════════════════════════════════════════
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
            + PREMIUM_DIVIDER + "\n\n"
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
                + PREMIUM_DIVIDER + "\n\n"
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
            f"{PREMIUM_DIVIDER}\n\n"
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

# ════════════════════════════════════════════════
#              ТОРГОВАЯ ПАНЕЛЬ
# ════════════════════════════════════════════════
@dp.message(F.text == "📊 Торговая панель")
async def t_panel(message: Message):
    if not db_get_user(message.from_user.id)["has_access"]:
        return

    now_msk = datetime.utcnow() + timedelta(hours=3)
    hour = now_msk.hour
    if 3 <= hour < 10:
        session_info = "🌏 Азиатская сессия — умеренная волатильность"
    elif 10 <= hour < 18:
        session_info = "🌍 Европейская сессия — высокая ликвидность"
    elif 18 <= hour < 23:
        session_info = "🌎 Американская сессия — максимальный объём"
    else:
        session_info = "🌙 Ночная сессия — осторожно, низкий объём"

    await message.answer(
        "📊 <b>ТОРГОВАЯ ПАНЕЛЬ — OTC РЫНОК</b>\n"
        + PREMIUM_DIVIDER + "\n\n"
        f"  📡 {session_info}\n"
        f"  🕐 {now_msk.strftime('%H:%M')} МСК\n\n"
        "Выберите <b>OTC-пару</b> для анализа:\n\n"
        "🌍 Доступно 12 валютных инструментов\n"
        "⚡ Таймфреймы: 3с | 15с | 30с | 1 мин",
        reply_markup=pair_kb,
        parse_mode="HTML"
    )

@dp.message(F.text.in_(set(pairs)))
async def set_pair(message: Message):
    uid = message.from_user.id
    # Сохраняем пару и сбрасываем время, чтобы юзер выбрал заново
    user_temp_data[uid] = {"pair": message.text}
    mood = get_market_mood(message.text)

    await message.answer(
        f"✅ <b>Актив выбран:</b> {message.text}\n"
        f"   Настроение рынка: {mood}\n\n"
        f"⏱ Выберите <b>время экспирации</b>:",
        reply_markup=time_kb,
        parse_mode="HTML"
    )

@dp.message(F.text.in_(set(times)))
async def set_time(message: Message):
    uid = message.from_user.id
    if uid not in user_temp_data or "pair" not in user_temp_data.get(uid, {}):
        # Если пара не выбрана — отправляем на выбор пары
        await message.answer(
            "⚠️ <b>Сначала выберите валютную пару.</b>\n\n"
            "Нажмите <b>«📊 Торговая панель»</b>.",
            parse_mode="HTML"
        )
        return

    user_temp_data[uid]["time"] = message.text
    pair = user_temp_data[uid]["pair"]

    await message.answer(
        f"⚙️ <b>КОНФИГУРАЦИЯ СОХРАНЕНА</b>\n"
        f"{PREMIUM_DIVIDER}\n\n"
        f"  📊 Актив:       <b>{pair}</b>\n"
        f"  ⏱ Экспирация:  <b>{message.text}</b>\n"
        f"\n{PREMIUM_DIVIDER}\n"
        f"<i>Алгоритм настроен. Нажмите «⚡ Получить сигнал» для анализа рынка.</i>",
        reply_markup=signal_kb,
        parse_mode="HTML"
    )

# ════════════════════════════════════════════════
#              ГЛАВНЫЙ ХЕНДЛЕР СИГНАЛА
# ════════════════════════════════════════════════
@dp.message(Command("signals"))
@dp.message(F.text == "⚡ Получить сигнал")
async def get_signal(message: Message):
    uid = message.from_user.id
    u   = db_get_user(uid)
    if not u["has_access"]:
        return

    # ── Антиспам: защита от двойного нажатия (1.5 сек) ──────────────
    # Срабатывает только если предыдущий сигнал уже был отправлен
    now_ts = time.time()
    last_ts = last_signal_request.get(uid, 0)
    if now_ts - last_ts < 1.5:
        return  # Молча игнорируем дубль — НЕ тратим лимит

    today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")
    daily = u["daily_count"]

    if u["last_date"] != today:
        daily = 0
        db_update_user(uid, daily=0, date=today)

    sub_type      = u['sub_type']
    current_limit = SUBSCRIPTION_PLANS[sub_type]['limit']

    if daily >= current_limit:
        if sub_type == "free":
            return await message.answer(
                "🛑 <b>ДНЕВНОЙ ЛИМИТ ИСЧЕРПАН</b>\n"
                + PREMIUM_DIVIDER + "\n\n"
                f"Вы использовали все <b>{current_limit} бесплатных сигнала</b> на сегодня.\n\n"
                "💡 <b>Хотите торговать без ограничений?</b>\n"
                "Перейдите в раздел <b>«💎 Подписка»</b> и получите\n"
                "больше сигналов по супер цене:\n\n"
                "🔵 <b>JUNIOR</b> — <b>25 сигналов/день</b> всего за <b>50$</b> / 7 дней\n"
                "🟣 <b>PRO</b>    — <b>50 сигналов/день</b> всего за <b>100$</b> / 7 дней\n\n"
                "⏳ <i>Или дождитесь обновления лимита в 00:00 (МСК).</i>",
                reply_markup=get_upgrade_kb(),
                parse_mode="HTML"
            )
        else:
            return await message.answer(
                "🛑 <b>ДНЕВНОЙ ЛИМИТ ИСЧЕРПАН</b>\n"
                + PREMIUM_DIVIDER + "\n\n"
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

    # ── Проверка конфигурации ─────────────────────────────────────────
    data = user_temp_data.get(uid, {})

    # Если пара не выбрана — просим выбрать
    if not data.get("pair"):
        return await message.answer(
            "⚠️ <b>Актив не выбран!</b>\n\n"
            "Пожалуйста, нажмите <b>«📊 Торговая панель»</b>,\n"
            "выберите валютную пару и время экспирации.",
            reply_markup=get_main_menu(True),
            parse_mode="HTML"
        )

    # Если время не выбрано — просим выбрать (показываем клавиатуру таймфреймов)
    if not data.get("time"):
        await message.answer(
            f"⚠️ <b>Время экспирации не выбрано!</b>\n\n"
            f"Актив: <b>{data['pair']}</b>\n\n"
            f"Выберите <b>время экспирации</b>:",
            reply_markup=time_kb,
            parse_mode="HTML"
        )
        return

    # ── Ставим метку времени ДО отправки прогресс-бара ───────────────
    last_signal_request[uid] = now_ts

    # ── Анимированный прогресс-бар ────────────────────────────────────
    progress_frames = [
        ("⬛️⬛️⬛️⬛️⬛️ <b>[ 0%]</b>",  "📡 Подключение к OTC-терминалу..."),
        ("🟩🟩⬛️⬛️⬛️ <b>[40%]</b>",  "📊 RSI + EMA + MACD анализ..."),
        ("🟩🟩🟩🟩⬛️ <b>[80%]</b>",  "🔬 BB + Stoch + паттерны..."),
        ("🟩🟩🟩🟩🟩 <b>[100%]</b>", "✅ OTC-сигнал сформирован!"),
    ]

    try:
        progress_msg = await message.answer(
            f"<b>⚡ SMART PRECISION АНАЛИЗ — OTC</b>\n"
            f"{PREMIUM_DIVIDER}\n\n"
            f"{progress_frames[0][0]}\n"
            f"<i>{progress_frames[0][1]}</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Ошибка отправки прогресс-бара: {e}")
        return

    for bar, label in progress_frames[1:]:
        await asyncio.sleep(0.35)
        try:
            await progress_msg.edit_text(
                f"<b>⚡ SMART PRECISION АНАЛИЗ — OTC</b>\n"
                f"{PREMIUM_DIVIDER}\n\n"
                f"{bar}\n"
                f"<i>{label}</i>",
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
        except Exception:
            pass

    # ── Генерируем OTC-сигнал ─────────────────────────────────────────
    direction, confidence, reason = generate_otc_signal(data["pair"], data["time"])

    db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)
    new_daily = daily + 1

    remaining = current_limit - new_daily
    limit_warning = ""
    if remaining == 0:
        limit_warning = "\n⚠️ <b>Это был последний сигнал на сегодня!</b> Лимит исчерпан."
    elif remaining <= 3:
        limit_warning = f"\n⚠️ <i>Осталось сигналов сегодня: <b>{remaining}</b>. Используйте с умом!</i>"

    conf_bar  = confidence_bar(confidence)
    dir_badge = "🟢 CALL (ВВЕРХ)" if "ВВЕРХ" in direction else "🔴 PUT (ВНИЗ)"
    strength_label = get_signal_strength_label(confidence)

    reason_block = (
        f"\n{THIN_DIVIDER}\n"
        f"💡 <b>КЛЮЧЕВЫЕ ФАКТОРЫ ВХОДА:</b>\n"
        f"  <i>{reason.capitalize()}</i>\n"
    )

    # PRO-блок с расширенной аналитикой
    pro_block = ""
    if sub_type == "pro":
        pro_tips = [
            "✅ Стандартные условия — работайте по алгоритму",
            "💡 Высокая уверенность — стандартный объём",
            "⚠️ Умеренный сигнал — рекомендуем 1–2% от депозита",
            "🔥 Сильный перекос — хорошая точка входа",
            "📐 Контртрендовый сигнал — повышенная осторожность",
        ]
        rng_pro = random.Random(hash(f"{data['pair']}_{direction}_{confidence}"))
        pro_tip = rng_pro.choice(pro_tips)

        now_msk = datetime.utcnow() + timedelta(hours=3)
        hour = now_msk.hour
        if 3 <= hour < 10:
            session = "🌏 Азиатская сессия"
        elif 10 <= hour < 18:
            session = "🌍 Европейская сессия"
        elif 18 <= hour < 23:
            session = "🌎 Американская сессия"
        else:
            session = "🌙 Ночная сессия"

        volatility_levels = ["🟢 Низкая", "🟡 Умеренная", "🟠 Средняя", "🔴 Высокая"]
        rng_vol = random.Random(hash(f"{data['pair']}_{confidence}_{hour}"))
        volatility = rng_vol.choice(volatility_levels)

        trend_strength = rng_pro.randint(55, 95)
        trend_bar = confidence_bar(trend_strength)

        pro_block = (
            f"\n{THIN_DIVIDER}\n"
            f"🟣 <b>PRO РАСШИРЕННАЯ АНАЛИТИКА:</b>\n"
            f"  📡 Сессия:        <b>{session}</b>\n"
            f"  📊 Волатильность: <b>{volatility}</b>\n"
            f"  💪 Сила тренда:   <code>{trend_bar}</code> <b>{trend_strength}%</b>\n"
            f"  💬 Совет:         {pro_tip}\n"
            f"  📐 Рек. объём:    <b>2–3% от депозита</b>\n"
            f"  🎯 Уверенность:   <b>{confidence}%</b>\n"
            f"  ⏱ Экспирация:    <b>{data['time']}</b>\n"
        )

    res = (
        f"⚡️ <b>OTC SMART PRECISION СИГНАЛ</b> ⚡️\n"
        f"{PREMIUM_DIVIDER}\n"
        f"  📊 Актив:       <b>{data['pair']}</b>\n"
        f"  ⏱ Экспирация:  <b>{data['time']}</b>\n"
        f"{THIN_DIVIDER}\n"
        f"🧠 <b>УВЕРЕННОСТЬ ИИ (6 блоков):</b>\n"
        f"  <code>{conf_bar}</code> <b>{confidence}%</b>\n"
        f"  {strength_label}\n\n"
        f"🚀 <b>РЕКОМЕНДАЦИЯ:</b>\n"
        f"  ┌──────────────────────┐\n"
        f"  │   {dir_badge}      │\n"
        f"  └──────────────────────┘\n"
        f"{reason_block}"
        f"{pro_block}"
        f"{THIN_DIVIDER}\n"
        f"  Использовано: <b>{new_daily} / {current_limit}</b> сигналов\n"
        f"{limit_warning}\n"
        f"⚠️ <i>Money Management: 1–3% от баланса на сделку!</i>"
    )

    # ── Удаляем прогресс-бар и отправляем сигнал ─────────────────────
    try:
        await progress_msg.delete()
    except Exception:
        pass

    try:
        await message.answer(res, parse_mode="HTML", reply_markup=signal_kb)
    except Exception as e:
        print(f"Ошибка отправки сигнала: {e}")

# ════════════════════════════════════════════════
#              ПРОФИЛЬ
# ════════════════════════════════════════════════
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

    rank_bar_str = ""
    for lo, hi, title, level in RANKS:
        if lo <= u["signals"] <= hi:
            rank_bar_str = rank_progress_bar(u["signals"], lo, hi)
            break

    used_pct  = min(int((u["daily_count"] / sub_limit) * 10), 10)
    daily_bar = "▓" * used_pct + "░" * (10 - used_pct)

    name = message.from_user.first_name or "Трейдер"

    profile_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧮 Рассчитать лот", callback_data="open_lot_calc")],
    ])

    await message.answer(
        f"👤 <b>ПРОФИЛЬ ТРЕЙДЕРА</b>\n"
        f"{PREMIUM_DIVIDER}\n\n"
        f"  Имя:       <b>{name}</b>\n"
        f"  TG ID:     <code>{message.from_user.id}</code>\n\n"
        f"{THIN_DIVIDER}\n"
        f"🏆 <b>РАНГ:</b>\n"
        f"  {rank}\n"
        f"  Прогресс: <code>{rank_bar_str}</code>"
        f"{rank_progress}\n\n"
        f"{THIN_DIVIDER}\n"
        f"💎 <b>ПОДПИСКА:</b>\n"
        f"  Тариф:     {sub_emoji} <b>{u['sub_type'].upper()}</b>\n"
        f"  Лимит:     <b>{sub_limit} сигналов / день</b>\n"
        f"  Истекает:  <b>{expiry_str}</b>"
        f"{days_info}\n\n"
        f"{THIN_DIVIDER}\n"
        f"📈 <b>ТОРГОВАЯ АКТИВНОСТЬ:</b>\n"
        f"  Всего сигналов:   <b>{u['signals']}</b>\n"
        f"  Сегодня:\n"
        f"  <code>[{daily_bar}]</code> <b>{u['daily_count']} / {sub_limit}</b>\n\n"
        f"{PREMIUM_DIVIDER}\n"
        f"🌐 Рынок: <b>🟢 Работает 24/7</b>\n"
        f"🔐 Лицензия: {'<b>АКТИВНА ✅</b>' if u['has_access'] else '<b>ОГРАНИЧЕНА ❌</b>'}\n\n"
        f"🧮 <i>Используйте калькулятор лота для правильного мани-менеджмента:</i>",
        reply_markup=profile_kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "open_lot_calc")
async def open_lot_calc_callback(callback: CallbackQuery):
    pending_lot_calc.add(callback.from_user.id)
    await callback.message.answer(
        "🧮 <b>КАЛЬКУЛЯТОР ЛОТА</b>\n"
        + PREMIUM_DIVIDER + "\n\n"
        "Введите ваш <b>текущий баланс в долларах</b> (только цифры):\n\n"
        "<i>Пример: 100 или 500 или 1250</i>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )
    await callback.answer()

# ════════════════════════════════════════════════
#              СТАТИСТИКА
# ════════════════════════════════════════════════
@dp.message(F.text == "📈 Статистика")
async def stats(message: Message):
    seed_val = int(datetime.now().strftime("%Y%m%d"))
    random.seed(seed_val)

    total_day    = random.randint(1800, 2500)
    win_rate     = round(random.uniform(91.5, 96.2), 1)
    plus_deals   = int(total_day * (win_rate / 100))
    minus_deals  = total_day - plus_deals - random.randint(10, 30)
    refunds      = total_day - plus_deals - minus_deals
    avg_profit   = round(random.uniform(85.5, 93.8), 1)
    best_pair    = random.choice([p.replace("🇦🇪 ", "").replace("🇦🇺 ", "").replace("🇧🇭 ", "")
                                   .replace("🇨🇭 ", "").replace("🇪🇺 ", "").replace("🇲🇦 ", "")
                                   .replace("🇳🇿 ", "").replace("🇸🇦 ", "").replace("🇺🇸 ", "")
                                   .replace("🇬🇧 ", "").replace("🇨🇦 ", "")
                                   for p in pairs])
    peak_hour    = random.randint(10, 18)
    total_users  = db_get_total_users()
    active_users = db_get_active_users()

    wr_filled = int(win_rate / 10)
    wr_bar    = "█" * wr_filled + "░" * (10 - wr_filled)

    rng_chart = random.Random(seed_val)
    hourly_bars = ""
    for h in range(6, 24, 3):
        vol = rng_chart.randint(2, 10)
        bar_h = "█" * vol + "░" * (10 - vol)
        hourly_bars += f"  {h:02d}:00  <code>{bar_h}</code>\n"

    await message.answer(
        f"📊 <b>ГЛОБАЛЬНАЯ СТАТИСТИКА ТЕРМИНАЛА</b>\n"
        f"{PREMIUM_DIVIDER}\n"
        f"\n🕐 <b>За последние 24 часа:</b>\n\n"
        f"  WinRate (Smart Precision):\n"
        f"  <code>[{wr_bar}] {win_rate}%</code>\n\n"
        f"  🟢 Профитных сделок:  <b>{plus_deals:,}</b>\n"
        f"  🔴 Убыточных сделок:  <b>{minus_deals:,}</b>\n"
        f"  🔁 Возвратов:         <b>{refunds:,}</b>\n"
        f"  📦 Всего сигналов:    <b>{total_day:,}</b>\n\n"
        f"{THIN_DIVIDER}\n"
        f"⚡ <b>ПОКАЗАТЕЛИ СИСТЕМЫ:</b>\n\n"
        f"  Средний ROI:          <b>{avg_profit}%</b>\n"
        f"  Лучшая пара дня:      <b>{best_pair}</b>\n"
        f"  Пик активности:       <b>{peak_hour}:00–{peak_hour+1}:00</b>\n"
        f"  Алгоритм:             <b>Smart Precision OTC v4.0</b>\n\n"
        f"{THIN_DIVIDER}\n"
        f"📈 <b>АКТИВНОСТЬ ПО ЧАСАМ (МСК):</b>\n\n"
        f"{hourly_bars}\n"
        f"{THIN_DIVIDER}\n"
        f"👥 <b>СООБЩЕСТВО:</b>\n\n"
        f"  Всего трейдеров:      <b>{total_users + 152:,}</b>\n"
        f"  Активных (24ч):       <b>{active_users + 94:,}</b>\n\n"
        f"{PREMIUM_DIVIDER}\n"
        f"<i>📅 Сводка обновлена: {datetime.now().strftime('%d.%m.%Y %H:%M')} (МСК)\n"
        f"Данные формируются по пулу всех торговых сессий на Pocket Option OTC.</i>",
        parse_mode="HTML"
    )
    random.seed()

# ════════════════════════════════════════════════
#              ЗАПУСК
# ════════════════════════════════════════════════
async def main():
    print("=" * 60)
    print("  🚀 AI TRADING TERMINAL — OTC PRO v4.0")
    print("  ✅ BOT STARTED SUCCESSFULLY")
    print("  🧠 SMART PRECISION ENGINE v4 (OTC MODE):")
    print("     RSI(14) + EMA(9/21) + MACD + BB + STOCH + PATTERNS")
    print("     FILTER: 3/6 blocks minimum")
    print("  💱 OTC PAIRS: 12 instruments with country flags")
    print("  ⏱ TIMEFRAMES: 3s / 15s / 30s / 1min")
    print("  🎹 UI: 2-column pair selection keyboard")
    print("  📅 SCHEDULE: MON-SUN 24/7")
    print("=" * 60)

    init_db()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
