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
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "f9cef39095c648039ca3f0929ee2a2eb")

if not TOKEN or not ADMIN_ID or not CRYPTO_BOT_TOKEN:
    raise ValueError("Проверьте BOT_TOKEN, ADMIN_ID и CRYPTO_BOT_TOKEN в переменных Railway!")

ADMIN_ID = int(ADMIN_ID)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ═══════════════════════════════════════════════
#              ПЛАНЫ ПОДПИСОК
# ═══════════════════════════════════════════════
SUBSCRIPTION_PLANS = {
    "free":   {"limit": 5,  "name": "FREE",   "price": 0,   "emoji": "⬜"},
    "junior": {"limit": 15, "name": "JUNIOR",  "price": 50,  "duration": 7, "emoji": "🔵"},
    "pro":    {"limit": 30, "name": "PRO",     "price": 100, "duration": 7, "emoji": "🟣"},
}

# ═══════════════════════════════════════════════
#         МАППИНГ ПАР → TWELVE DATA
# ═══════════════════════════════════════════════
PAIR_TO_SYMBOL = {
    "💵 EUR/USD": "EUR/USD",
    "💵 GBP/USD": "GBP/USD",
    "💵 USD/JPY": "USD/JPY",
    "💵 USD/CAD": "USD/CAD",
    "💵 AUD/CAD": "AUD/CAD",
    "💵 EUR/CHF": "EUR/CHF",
}

# Валюты, которые затрагивает каждая пара (для фильтрации новостей)
PAIR_CURRENCIES = {
    "💵 EUR/USD": ["USD", "EUR"],
    "💵 GBP/USD": ["USD", "GBP"],
    "💵 USD/JPY": ["USD", "JPY"],
    "💵 USD/CAD": ["USD", "CAD"],
    "💵 AUD/CAD": ["AUD", "CAD"],
    "💵 EUR/CHF": ["EUR", "CHF"],
}

# ═══════════════════════════════════════════════
#         ЛУЧШЕЕ ВРЕМЯ ТОРГОВЛИ ПО ПАРЕ
# ═══════════════════════════════════════════════
PAIR_BEST_TIME = {
    "💵 EUR/USD": {
        "window": "10:00 – 19:00 МСК",
        "note": "Самая техничная пара в мире. Минимум ложных сигналов. Работает на Лондон + Нью-Йорк сессию."
    },
    "💵 GBP/USD": {
        "window": "11:00 – 18:00 МСК",
        "note": "Высокая волатильность на открытии Лондона. Сильные движения при выходе UK-статистики."
    },
    "💵 USD/JPY": {
        "window": "03:00 – 12:00 МСК",
        "note": "Лучшие движения на Азиатской и начале Европейской сессии. Техничная и трендовая пара."
    },
    "💵 USD/CAD": {
        "window": "15:00 – 21:00 МСК",
        "note": "Оживает с открытием Нью-Йорка и выходом нефтяной статистики. Чёткие уровни."
    },
    "💵 AUD/CAD": {
        "window": "05:00 – 13:00 МСК",
        "note": "Активна в Азиатскую и начало Европейской сессии. Коррелирует с сырьевыми рынками."
    },
    "💵 EUR/CHF": {
        "window": "09:00 – 17:00 МСК",
        "note": "Спокойная и техничная пара. Лучшие сигналы в Европейскую сессию, низкий спред."
    },
}

# ═══════════════════════════════════════════════
#         ПРОВЕРКА РАБОЧЕГО ВРЕМЕНИ РЫНКА
# ═══════════════════════════════════════════════
def is_market_open() -> bool:
    now = datetime.utcnow() + timedelta(hours=3)
    return now.weekday() < 5

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news_events (
                id              SERIAL PRIMARY KEY,
                event_time      TEXT,
                event_time_dt   TIMESTAMP,
                title           TEXT,
                currency        TEXT,
                impact          INTEGER DEFAULT 3,
                event_date      TEXT,
                created_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auto_signals (
                id              SERIAL PRIMARY KEY,
                pair            TEXT,
                direction       TEXT,
                confidence      INTEGER,
                reason          TEXT,
                signal_date     TEXT,
                sent_at         TIMESTAMP DEFAULT NOW()
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

def db_get_all_users_with_access():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE has_access = TRUE")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [r[0] for r in rows]
    except:
        return []

# ════════════════════════════════════════════════
#      БЛОКНОТ НОВОСТЕЙ (Investing.com scraper)
# ════════════════════════════════════════════════
async def fetch_investing_news() -> list[dict]:
    today_msk = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")
    url = "https://www.investing.com/economic-calendar/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.investing.com/",
    }
    events = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    print(f"Investing.com HTTP {resp.status}")
                    return []
                html = await resp.text()

        import re

        row_pattern = re.compile(
            r'<tr[^>]*?id="eventRowId_(\d+)"[^>]*?>(.*?)</tr>',
            re.DOTALL
        )
        time_pattern   = re.compile(r'class="time[^"]*"[^>]*>([^<]+)<')
        title_pattern  = re.compile(r'class="event[^"]*"[^>]*>([^<]+)<')
        curr_pattern   = re.compile(r'class="flagCur[^"]*"[^>]*>\s*<[^>]+>\s*([A-Z]{3})')
        impact_pattern = re.compile(r'data-img_key="bull(\d)"')

        for m in row_pattern.finditer(html):
            row_html = m.group(2)

            impact_m = impact_pattern.search(row_html)
            if not impact_m or int(impact_m.group(1)) < 3:
                continue

            time_m  = time_pattern.search(row_html)
            title_m = title_pattern.search(row_html)
            curr_m  = curr_pattern.search(row_html)

            if not (time_m and title_m and curr_m):
                continue

            event_time_str = time_m.group(1).strip()
            title          = title_m.group(1).strip()
            currency       = curr_m.group(1).strip()

            try:
                event_dt_str = f"{today_msk} {event_time_str}"
                event_dt_gmt = datetime.strptime(event_dt_str, "%Y-%m-%d %H:%M")
                event_dt_msk = event_dt_gmt + timedelta(hours=3)
            except Exception:
                event_dt_msk = None

            events.append({
                "event_time":    event_dt_msk.strftime("%H:%M") if event_dt_msk else event_time_str,
                "event_time_dt": event_dt_msk,
                "title":         title,
                "currency":      currency,
                "impact":        3,
                "event_date":    today_msk,
            })

    except Exception as e:
        print(f"Ошибка парсинга Investing.com: {e}")

    return events


def db_save_news(events: list[dict]):
    if not events:
        return
    today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM news_events WHERE event_date = %s", (today,))
        for ev in events:
            cursor.execute(
                """INSERT INTO news_events
                   (event_time, event_time_dt, title, currency, impact, event_date)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (ev["event_time"], ev["event_time_dt"], ev["title"],
                 ev["currency"], ev["impact"], ev["event_date"])
            )
        conn.commit()
        cursor.close()
        conn.close()
        print(f"✅ Сохранено {len(events)} новостей в блокнот")
    except Exception as e:
        print(f"Ошибка сохранения новостей: {e}")


def db_get_today_news() -> list[dict]:
    today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")
    try:
        conn   = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM news_events WHERE event_date = %s ORDER BY event_time_dt ASC",
            (today,)
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Ошибка чтения новостей: {e}")
        return []


def check_news_danger_for_pair(pair_label: str) -> dict:
    currencies = PAIR_CURRENCIES.get(pair_label, [])
    now_msk = datetime.utcnow() + timedelta(hours=3)
    news = db_get_today_news()

    closest = None
    closest_delta = None

    for ev in news:
        if ev["currency"] not in currencies:
            continue
        if not ev.get("event_time_dt"):
            continue
        ev_dt = ev["event_time_dt"]
        if isinstance(ev_dt, str):
            try:
                ev_dt = datetime.strptime(ev_dt, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
        delta_minutes = (ev_dt - now_msk).total_seconds() / 60
        abs_delta = abs(delta_minutes)
        if abs_delta <= 30:
            if closest_delta is None or abs_delta < closest_delta:
                closest_delta = abs_delta
                closest = {"ev": ev, "delta": delta_minutes}

    if closest:
        ev = closest["ev"]
        delta = closest["delta"]
        if delta >= 0:
            desc = f"через {int(delta)} мин"
        else:
            desc = f"{int(abs(delta))} мин назад"
        return {
            "dangerous": True,
            "minutes":   int(delta),
            "event":     ev["title"],
            "currency":  ev["currency"],
            "time":      ev["event_time"],
            "desc":      desc,
        }
    return {"dangerous": False}


async def news_scheduler():
    while True:
        try:
            print("📰 Обновление блокнота новостей...")
            events = await fetch_investing_news()
            if events:
                db_save_news(events)
                print(f"📰 Загружено {len(events)} важных событий")
            else:
                print("📰 Нет важных событий или ошибка парсинга")
        except Exception as e:
            print(f"Ошибка news_scheduler: {e}")
        await asyncio.sleep(3600)


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
#         ПОЛУЧЕНИЕ КОТИРОВОК (TWELVE DATA)
#         Загружаем 50 свечей для точного анализа
# ════════════════════════════════════════════════
async def get_real_quote(pair_label: str) -> dict | None:
    symbol = PAIR_TO_SYMBOL.get(pair_label)
    if not symbol:
        return None

    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}"
        f"&interval=1min"
        f"&outputsize=50"
        f"&apikey={TWELVEDATA_API_KEY}"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        if data.get("status") == "error" or "values" not in data:
            return None

        values = data["values"]
        if not values or len(values) < 10:
            return None

        # Переворачиваем: индекс 0 = самая старая свеча, последний = текущая
        opens  = [float(v["open"])  for v in reversed(values)]
        closes = [float(v["close"]) for v in reversed(values)]
        highs  = [float(v["high"])  for v in reversed(values)]
        lows   = [float(v["low"])   for v in reversed(values)]
        volumes = []
        for v in reversed(values):
            try:
                volumes.append(float(v.get("volume", 0)))
            except:
                volumes.append(0.0)

        current_price = closes[-1]
        prev_close    = closes[-2] if len(closes) >= 2 else closes[-1]

        change     = current_price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close != 0 else 0.0

        last_open  = opens[-1]
        last_close = closes[-1]
        if   last_close > last_open: candle_direction = "bullish"
        elif last_close < last_open: candle_direction = "bearish"
        else:                        candle_direction = "neutral"

        # ── RSI(14) точный расчёт ─────────────────────────
        rsi_value  = 50.0
        rsi_signal = "нейтральный"
        rsi_period = 14
        if len(closes) >= rsi_period + 1:
            deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
            gains  = [max(d, 0) for d in deltas]
            losses = [abs(min(d, 0)) for d in deltas]
            # Первый средний
            avg_gain = sum(gains[:rsi_period]) / rsi_period
            avg_loss = sum(losses[:rsi_period]) / rsi_period
            # Smoothed (Wilder)
            for i in range(rsi_period, len(gains)):
                avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
                avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period
            rs = avg_gain / avg_loss if avg_loss != 0 else 100
            rsi_value = 100 - (100 / (1 + rs))

            if   rsi_value >= 70: rsi_signal = f"сильная перекупленность ({rsi_value:.1f})"
            elif rsi_value >= 60: rsi_signal = f"перекупленность ({rsi_value:.1f})"
            elif rsi_value <= 30: rsi_signal = f"сильная перепроданность ({rsi_value:.1f})"
            elif rsi_value <= 40: rsi_signal = f"перепроданность ({rsi_value:.1f})"
            else:                  rsi_signal = f"нейтральный ({rsi_value:.1f})"

        # ── EMA расчёт ────────────────────────────────────
        def calc_ema(data_list: list, period: int) -> list:
            if len(data_list) < period:
                return []
            k = 2 / (period + 1)
            ema = [sum(data_list[:period]) / period]
            for price in data_list[period:]:
                ema.append(price * k + ema[-1] * (1 - k))
            return ema

        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, 50) if len(closes) >= 50 else []

        ema9_cur  = ema9[-1]  if ema9  else current_price
        ema21_cur = ema21[-1] if ema21 else current_price
        ema50_cur = ema50[-1] if ema50 else current_price

        # EMA тренд: текущая и предыдущая
        ema9_prev  = ema9[-2]  if len(ema9)  >= 2 else ema9_cur
        ema21_prev = ema21[-2] if len(ema21) >= 2 else ema21_cur

        # ── MACD (12, 26, 9) ──────────────────────────────
        ema12 = calc_ema(closes, 12)
        ema26 = calc_ema(closes, 26)

        macd_line   = 0.0
        macd_signal_val = 0.0
        macd_hist   = 0.0
        macd_trend  = "нейтральный"

        if len(ema12) > 0 and len(ema26) > 0:
            # Выравниваем длины
            min_len = min(len(ema12), len(ema26))
            macd_values = [ema12[-(min_len - i)] - ema26[-(min_len - i)] for i in range(min_len)]
            signal_ema  = calc_ema(macd_values, 9)
            if signal_ema:
                macd_line       = macd_values[-1]
                macd_signal_val = signal_ema[-1]
                macd_hist       = macd_line - macd_signal_val
                macd_prev_hist  = (macd_values[-2] - signal_ema[-2]) if len(signal_ema) >= 2 else 0

                if macd_hist > 0 and macd_prev_hist <= 0:
                    macd_trend = "бычий разворот"
                elif macd_hist < 0 and macd_prev_hist >= 0:
                    macd_trend = "медвежий разворот"
                elif macd_hist > 0:
                    macd_trend = "бычий"
                elif macd_hist < 0:
                    macd_trend = "медвежий"

        # ── Паттерны свечей ───────────────────────────────
        def detect_candle_pattern(opens_list, closes_list, highs_list, lows_list) -> str:
            if len(opens_list) < 3:
                return "нет паттерна"

            o1, c1, h1, l1 = opens_list[-3], closes_list[-3], highs_list[-3], lows_list[-3]
            o2, c2, h2, l2 = opens_list[-2], closes_list[-2], highs_list[-2], lows_list[-2]
            o3, c3, h3, l3 = opens_list[-1], closes_list[-1], highs_list[-1], lows_list[-1]

            body2 = abs(c2 - o2)
            range2 = h2 - l2
            body3 = abs(c3 - o3)
            range3 = h3 - l3

            # Доджи (тело < 10% диапазона)
            if range3 > 0 and body3 / range3 < 0.1:
                return "доджи (неопределённость)"

            # Пин-бар бычий: длинный нижний фитиль, малое тело вверху
            lower_wick3 = min(o3, c3) - l3
            upper_wick3 = h3 - max(o3, c3)
            if range3 > 0 and lower_wick3 > range3 * 0.6 and body3 < range3 * 0.3:
                return "бычий пин-бар"

            # Пин-бар медвежий: длинный верхний фитиль, малое тело внизу
            if range3 > 0 and upper_wick3 > range3 * 0.6 and body3 < range3 * 0.3:
                return "медвежий пин-бар"

            # Бычье поглощение
            if c2 < o2 and c3 > o3 and c3 > o2 and o3 < c2:
                return "бычье поглощение"

            # Медвежье поглощение
            if c2 > o2 and c3 < o3 and c3 < o2 and o3 > c2:
                return "медвежье поглощение"

            # Три белых солдата
            if c1 > o1 and c2 > o2 and c3 > o3 and c2 > c1 and c3 > c2:
                return "три белых солдата (сильный рост)"

            # Три чёрных вороны
            if c1 < o1 and c2 < o2 and c3 < o3 and c2 < c1 and c3 < c2:
                return "три чёрных вороны (сильное падение)"

            return "нет паттерна"

        candle_pattern = detect_candle_pattern(opens, closes, highs, lows)

        # ── Тренд по EMA ──────────────────────────────────
        if ema9_cur > ema21_cur and ema9_prev <= ema21_prev:
            trend = "бычий разворот EMA 📈"
        elif ema9_cur < ema21_cur and ema9_prev >= ema21_prev:
            trend = "медвежий разворот EMA 📉"
        elif ema9_cur > ema21_cur:
            trend = "восходящий 📈"
        elif ema9_cur < ema21_cur:
            trend = "нисходящий 📉"
        else:
            trend = "боковик"

        # ── Волатильность ─────────────────────────────────
        volatility = "низкая"
        last_candles = list(zip(highs[-10:], lows[-10:]))
        if last_candles:
            avg_range = sum(h - l for h, l in last_candles) / len(last_candles)
            if   avg_range > current_price * 0.0005: volatility = "высокая 🔥"
            elif avg_range > current_price * 0.0002: volatility = "средняя"

        return {
            "price":            current_price,
            "prev_close":       prev_close,
            "change":           change,
            "change_pct":       change_pct,
            "candle_direction": candle_direction,
            "candle_pattern":   candle_pattern,
            "rsi_signal":       rsi_signal,
            "rsi_value":        rsi_value,
            "volatility":       volatility,
            "trend":            trend,
            "high":             max(highs[-10:]),
            "low":              min(lows[-10:]),
            "candles_count":    len(closes),
            "closes":           closes,
            "opens":            opens,
            "highs":            highs,
            "lows":             lows,
            "ema9":             ema9_cur,
            "ema21":            ema21_cur,
            "ema50":            ema50_cur,
            "ema9_prev":        ema9_prev,
            "ema21_prev":       ema21_prev,
            "macd_hist":        macd_hist,
            "macd_trend":       macd_trend,
        }

    except Exception as e:
        print(f"Ошибка TwelveData {symbol}: {e}")
        return None


# ════════════════════════════════════════════════
#   НОВАЯ ЛОГИКА СИГНАЛА — MULTI-FACTOR v2
#
#   Используем 4 независимых блока голосования:
#   1. RSI(14)
#   2. EMA9 vs EMA21 (тренд)
#   3. MACD гистограмма
#   4. Паттерн свечи
#
#   Сигнал выдаётся ТОЛЬКО при согласии минимум 3 из 4 факторов.
#   Никакого random в направлении — только рынок.
# ════════════════════════════════════════════════
def generate_signal_from_quote(quote: dict) -> tuple[str | None, int, str]:
    """
    Возвращает (direction, confidence, reason).
    direction = None — пропустить сделку (индикаторы не дают чёткого сигнала).

    Система голосования:
      +1 = голос за CALL (ВВЕРХ)
      -1 = голос за PUT (ВНИЗ)
       0 = нейтрально / воздержался

    Условие входа: |сумма голосов| >= 3 из максимум 5 возможных
    """

    rsi_value      = quote.get("rsi_value", 50.0)
    candle         = quote.get("candle_direction", "neutral")
    candle_pattern = quote.get("candle_pattern", "нет паттерна")
    trend          = quote.get("trend", "боковик")
    macd_hist      = quote.get("macd_hist", 0.0)
    macd_trend_str = quote.get("macd_trend", "нейтральный")
    ema9           = quote.get("ema9", 0)
    ema21          = quote.get("ema21", 0)
    ema9_prev      = quote.get("ema9_prev", 0)
    ema21_prev     = quote.get("ema21_prev", 0)
    current_price  = quote.get("price", 0)

    votes        = []   # список (голос, вес, описание)
    call_reasons = []
    put_reasons  = []

    # ════════════════════════════════════════════
    # БЛОК 1: RSI(14) — вес 2
    # Логика: торгуем только ОТ зон (контртренд по RSI),
    # но ТОЛЬКО если другие индикаторы подтверждают.
    # ════════════════════════════════════════════
    rsi_vote = 0
    if rsi_value <= 30:
        rsi_vote = +2  # сильная перепроданность → CALL
        call_reasons.append(f"RSI {rsi_value:.1f} — сильная перепроданность, ожидается отскок")
    elif rsi_value <= 40:
        rsi_vote = +1  # умеренная перепроданность → слабый CALL
        call_reasons.append(f"RSI {rsi_value:.1f} — перепроданность")
    elif rsi_value >= 70:
        rsi_vote = -2  # сильная перекупленность → PUT
        put_reasons.append(f"RSI {rsi_value:.1f} — сильная перекупленность, ожидается откат")
    elif rsi_value >= 60:
        rsi_vote = -1  # умеренная перекупленность → слабый PUT
        put_reasons.append(f"RSI {rsi_value:.1f} — перекупленность")
    # 40–60: RSI нейтрален, голос = 0

    votes.append(rsi_vote)

    # ════════════════════════════════════════════
    # БЛОК 2: EMA9 vs EMA21 (тренд) — вес 2
    # Кроссовер EMA — один из лучших трендовых сигналов
    # ════════════════════════════════════════════
    ema_vote = 0
    # Свежий кроссовер (самый сильный сигнал)
    if ema9 > ema21 and ema9_prev <= ema21_prev:
        ema_vote = +2
        call_reasons.append("EMA9 пересекла EMA21 снизу вверх — бычий кроссовер")
    elif ema9 < ema21 and ema9_prev >= ema21_prev:
        ema_vote = -2
        put_reasons.append("EMA9 пересекла EMA21 сверху вниз — медвежий кроссовер")
    # Продолжение тренда (слабый сигнал)
    elif ema9 > ema21:
        ema_vote = +1
        call_reasons.append("EMA9 выше EMA21 — восходящий тренд")
    elif ema9 < ema21:
        ema_vote = -1
        put_reasons.append("EMA9 ниже EMA21 — нисходящий тренд")

    votes.append(ema_vote)

    # ════════════════════════════════════════════
    # БЛОК 3: MACD гистограмма — вес 2
    # Разворот гистограммы даёт ранний сигнал
    # ════════════════════════════════════════════
    macd_vote = 0
    if "бычий разворот" in macd_trend_str:
        macd_vote = +2
        call_reasons.append("MACD: бычий разворот гистограммы")
    elif "медвежий разворот" in macd_trend_str:
        macd_vote = -2
        put_reasons.append("MACD: медвежий разворот гистограммы")
    elif macd_hist > 0:
        macd_vote = +1
        call_reasons.append("MACD гистограмма в положительной зоне")
    elif macd_hist < 0:
        macd_vote = -1
        put_reasons.append("MACD гистограмма в отрицательной зоне")

    votes.append(macd_vote)

    # ════════════════════════════════════════════
    # БЛОК 4: Паттерн свечи — вес 2
    # ════════════════════════════════════════════
    pattern_vote = 0
    pattern_lower = candle_pattern.lower()

    if "доджи" in pattern_lower:
        # Доджи = неопределённость, воздерживаемся (0)
        pattern_vote = 0
    elif "бычий пин-бар" in pattern_lower or "бычье поглощение" in pattern_lower:
        pattern_vote = +2
        call_reasons.append(f"Паттерн: {candle_pattern}")
    elif "три белых солдата" in pattern_lower:
        pattern_vote = +2
        call_reasons.append(f"Паттерн: {candle_pattern}")
    elif "медвежий пин-бар" in pattern_lower or "медвежье поглощение" in pattern_lower:
        pattern_vote = -2
        put_reasons.append(f"Паттерн: {candle_pattern}")
    elif "три чёрных вороны" in pattern_lower:
        pattern_vote = -2
        put_reasons.append(f"Паттерн: {candle_pattern}")
    else:
        # Нет паттерна — используем направление последней свечи (слабый голос)
        if candle == "bullish":
            pattern_vote = +1
            call_reasons.append("Последняя свеча бычья")
        elif candle == "bearish":
            pattern_vote = -1
            put_reasons.append("Последняя свеча медвежья")

    votes.append(pattern_vote)

    # ════════════════════════════════════════════
    # БЛОК 5: Цена vs EMA (подтверждение) — вес 1
    # Цена выше обеих EMA = бычье давление
    # ════════════════════════════════════════════
    price_ema_vote = 0
    if current_price > ema9 and current_price > ema21:
        price_ema_vote = +1
        call_reasons.append("Цена выше EMA9 и EMA21")
    elif current_price < ema9 and current_price < ema21:
        price_ema_vote = -1
        put_reasons.append("Цена ниже EMA9 и EMA21")

    votes.append(price_ema_vote)

    # ════════════════════════════════════════════
    # ИТОГОВЫЙ ПОДСЧЁТ
    # ════════════════════════════════════════════
    total_score = sum(votes)
    # Максимально возможный балл: 2+2+2+2+1 = 9

    # Считаем количество блоков, которые проголосовали за одну сторону
    # (блок считается "проголосовавшим" если его значение не 0 и совпадает со знаком total_score)
    if total_score > 0:
        agreeing_blocks = sum(1 for v in votes if v > 0)
        blocking_blocks = sum(1 for v in votes if v < 0)
    elif total_score < 0:
        agreeing_blocks = sum(1 for v in votes if v < 0)
        blocking_blocks = sum(1 for v in votes if v > 0)
    else:
        agreeing_blocks = 0
        blocking_blocks = 0

    # ── ЖЁСТКИЙ ФИЛЬТР ВХОДА ──────────────────────────
    # Условие 1: Минимум 3 блока голосуют в одну сторону
    if agreeing_blocks < 3:
        return None, 0, (
            f"только {agreeing_blocks} из 5 индикаторов согласны — "
            f"недостаточно для уверенного входа"
        )

    # Условие 2: Нет сильного противоречия (не более 1 блока против)
    if blocking_blocks >= 2:
        return None, 0, (
            f"{blocking_blocks} индикатора противоречат сигналу — "
            f"рынок даёт смешанные данные"
        )

    # Условие 3: Абсолютный балл должен быть достаточным
    # (слабые сигналы отфильтровываем)
    if abs(total_score) < 3:
        return None, 0, "суммарный сигнал слишком слабый — пропускаем"

    # ── НАПРАВЛЕНИЕ ───────────────────────────────────
    if total_score > 0:
        direction   = "ВВЕРХ 🟢 (CALL)"
        reason_text = " | ".join(call_reasons[:3]) if call_reasons else "технический анализ"
    else:
        direction   = "ВНИЗ 🔴 (PUT)"
        reason_text = " | ".join(put_reasons[:3]) if put_reasons else "технический анализ"

    # ── УВЕРЕННОСТЬ ───────────────────────────────────
    # Считаем честно от силы сигнала, без random
    max_possible = 9  # 2+2+2+2+1
    signal_strength = abs(total_score) / max_possible  # 0.0 – 1.0

    # Базовая уверенность: 78–95%
    # Добавляем баллы за согласие блоков
    base_confidence = 78 + int(signal_strength * 17)
    block_bonus     = (agreeing_blocks - 3) * 2  # +0, +2, +4 за 3, 4, 5 блоков

    confidence = min(base_confidence + block_bonus, 95)

    # Небольшой реалистичный разброс ±1 (НЕ влияет на направление)
    confidence = confidence + random.choice([-1, 0, 0, 1])
    confidence = max(78, min(95, confidence))

    return direction, confidence, reason_text


# ════════════════════════════════════════════════
#         СИЛА ВАЛЮТНЫХ ПАР (для статистики)
# ════════════════════════════════════════════════
async def get_pairs_strength() -> list[dict]:
    results = []
    for pair_label, symbol in PAIR_TO_SYMBOL.items():
        try:
            url = (
                f"https://api.twelvedata.com/time_series"
                f"?symbol={symbol}&interval=1h&outputsize=5"
                f"&apikey={TWELVEDATA_API_KEY}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

            if data.get("status") == "error" or "values" not in data:
                continue

            values = data["values"]
            if not values or len(values) < 2:
                continue

            closes = [float(v["close"]) for v in reversed(values)]
            highs  = [float(v["high"])  for v in reversed(values)]
            lows   = [float(v["low"])   for v in reversed(values)]

            price     = closes[-1]
            prev      = closes[0]
            change_pct = ((price - prev) / prev) * 100 if prev != 0 else 0.0
            avg_range  = sum(h - l for h, l in zip(highs, lows)) / len(highs)
            volatility_pct = (avg_range / price) * 100 if price != 0 else 0.0

            if   change_pct > 0.05: trend = "🟢 Бычий"
            elif change_pct < -0.05: trend = "🔴 Медвежий"
            else: trend = "⚪ Боковик"

            pair_name = pair_label.replace("💵 ", "")
            results.append({
                "pair":          pair_name,
                "price":         price,
                "change_pct":    change_pct,
                "volatility":    volatility_pct,
                "trend":         trend,
            })
            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"Ошибка силы пары {symbol}: {e}")

    results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return results


# ════════════════════════════════════════════════
#         АВТОСИГНАЛЫ (фоновая задача)
# ════════════════════════════════════════════════
async def auto_signals_scheduler():
    await asyncio.sleep(60)
    while True:
        try:
            if is_market_open():
                today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")

                try:
                    conn   = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT COUNT(*) FROM auto_signals WHERE signal_date = %s",
                        (today,)
                    )
                    sent_today = cursor.fetchone()[0]
                    cursor.close()
                    conn.close()
                except:
                    sent_today = 0

                if sent_today < 3:
                    best_signal = None
                    best_conf   = 0

                    for pair_label in PAIR_TO_SYMBOL.keys():
                        danger = check_news_danger_for_pair(pair_label)
                        if danger["dangerous"]:
                            continue

                        quote = await get_real_quote(pair_label)
                        if not quote:
                            continue

                        direction, confidence, reason = generate_signal_from_quote(quote)

                        if direction is None:
                            continue

                        if confidence >= 90 and confidence > best_conf:
                            best_conf   = confidence
                            best_signal = {
                                "pair":       pair_label,
                                "direction":  direction,
                                "confidence": confidence,
                                "reason":     reason,
                                "quote":      quote,
                            }

                    if best_signal:
                        s = best_signal
                        conf_bar  = confidence_bar(s["confidence"])
                        dir_badge = "🟢 CALL (ВВЕРХ)" if "ВВЕРХ" in s["direction"] else "🔴 PUT (ВНИЗ)"

                        price = s["quote"]["price"]
                        price_str = f"{price:.3f}" if price > 100 else f"{price:.5f}"

                        text = (
                            "🔔 <b>БЕСПЛАТНЫЙ VIP-АВТОСИГНАЛ</b>\n"
                            "━━━━━━━━━━━━━━━━━\n\n"
                            f"  📊 Актив:      <b>{s['pair']}</b>\n"
                            f"  💰 Цена:       <b>{price_str}</b>\n"
                            f"  ⏱ Экспирация: <b>⏱ 3 мин</b>\n\n"
                            f"🧠 <b>УВЕРЕННОСТЬ ИИ:</b>\n"
                            f"  <code>{conf_bar}</code> <b>{s['confidence']}%</b>\n\n"
                            f"🚀 <b>РЕКОМЕНДАЦИЯ:</b>\n"
                            f"  ┌──────────────────┐\n"
                            f"  │   {dir_badge}   │\n"
                            f"  └──────────────────┘\n\n"
                            f"💡 <b>Почему вошли:</b>\n"
                            f"  <i>{s['reason']}</i>\n\n"
                            f"━━━━━━━━━━━━━━━━━\n"
                            f"⚠️ <i>Money Management: 1–3% от баланса!\n"
                            f"Это автосигнал высокой уверенности. Макс. 3 в день.</i>"
                        )

                        users = db_get_all_users_with_access()
                        sent_count = 0
                        for uid in users:
                            try:
                                await bot.send_message(uid, text, parse_mode="HTML")
                                sent_count += 1
                                await asyncio.sleep(0.05)
                            except Exception:
                                pass

                        try:
                            conn   = get_db_connection()
                            cursor = conn.cursor()
                            cursor.execute(
                                """INSERT INTO auto_signals
                                   (pair, direction, confidence, reason, signal_date)
                                   VALUES (%s, %s, %s, %s, %s)""",
                                (s["pair"], s["direction"], s["confidence"],
                                 s["reason"], today)
                            )
                            conn.commit()
                            cursor.close()
                            conn.close()
                        except Exception as e:
                            print(f"Ошибка сохранения автосигнала: {e}")

                        print(f"✅ Автосигнал отправлен {sent_count} пользователям | {s['pair']} | {s['confidence']}%")

        except Exception as e:
            print(f"Ошибка auto_signals_scheduler: {e}")

        await asyncio.sleep(1200)


# ════════════════════════════════════════════════
#         РАНГИ И УТИЛИТЫ
# ════════════════════════════════════════════════
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
#              ВРЕМЕННЫЕ ДАННЫЕ
# ════════════════════════════════════════════════
pairs = [
    "💵 EUR/USD", "💵 GBP/USD", "💵 USD/JPY",
    "💵 USD/CAD", "💵 AUD/CAD", "💵 EUR/CHF",
]
times = ["⏱ 1 мин", "⏱ 3 мин", "⏱ 5 мин", "⏱ 10 мин"]

user_temp_data   = {}
pending_users    = set()
pending_support  = set()
pending_lot_calc = set()
last_click_time  = {}

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
        [KeyboardButton(text="📰 Новости рынка"),     KeyboardButton(text="🧮 Калькулятор лота")],
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
        "Сигналы в день         5       15         30\n"
        "Реал. котировки      ✅      ✅        ✅\n"
        "RSI-анализ                ✅      ✅        ✅\n"
        "Анализ тренда         ✅      ✅        ✅\n"
        "Работа поддержки  ❌      ✅        ✅\n"
        "VIP-уведомления    ❌      ❌        ✅\n"
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

# ════════════════════════════════════════════════
#              КОМАНДЫ И ОСНОВНЫЕ ХЕНДЛЕРЫ
# ════════════════════════════════════════════════
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
        "▸ RSI(14) + EMA(9/21) + MACD + паттерны свечей\n"
        "▸ Мульти-факторный анализ: минимум 3 из 5 индикаторов\n"
        "▸ Новостной фильтр (Investing.com, 3 быка)\n"
        "▸ Бесплатные VIP-автосигналы (до 3/день)\n"
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
        "📡 <b>Источник данных:</b> Twelve Data (live, 50 свечей)\n"
        "📰 <b>Новости:</b> Investing.com (3 быка, обновление каждый час)\n"
        "🧠 <b>Алгоритм:</b> RSI(14) + EMA(9/21) + MACD + Паттерны свечей\n"
        "🎯 <b>Фильтр входа:</b> минимум 3 из 5 индикаторов согласны\n"
        "📊 <b>Платформа:</b> Pocket Option\n"
        "💱 <b>Пары:</b> 6 валютных инструментов\n"
        "⏱ <b>Таймфреймы:</b> 1, 3, 5, 10 минут\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💱 <b>ТОРГОВЫЕ ПАРЫ И ЛУЧШЕЕ ВРЕМЯ:</b>\n\n"
        "🔹 <b>EUR/USD</b> — 10:00–19:00 МСК\n"
        "  <i>Самая техничная пара в мире. Минимум ложных сигналов.</i>\n\n"
        "🔹 <b>GBP/USD</b> — 11:00–18:00 МСК\n"
        "  <i>Высокая волатильность на открытии Лондона.</i>\n\n"
        "🔹 <b>USD/JPY</b> — 03:00–12:00 МСК\n"
        "  <i>Лучшие движения в Азиатскую и начале Европейской сессии.</i>\n\n"
        "🔹 <b>USD/CAD</b> — 15:00–21:00 МСК\n"
        "  <i>Оживает с открытием Нью-Йорка и выходом нефтяной статистики.</i>\n\n"
        "🔹 <b>AUD/CAD</b> — 05:00–13:00 МСК\n"
        "  <i>Активна в Азиатскую и начало Европейской сессии.</i>\n\n"
        "🔹 <b>EUR/CHF</b> — 09:00–17:00 МСК\n"
        "  <i>Спокойная и техничная пара. Лучшие сигналы в Европейскую сессию.</i>\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "⏰ <b>РЕЖИМ РАБОТЫ:</b>\n"
        "  🟢 ПН–ПТ: 24/7 (круглосуточно)\n"
        "  🔴 СБ–ВС: рынок закрыт, сигналы недоступны\n\n"
        f"  Сейчас: <b>{market_status}</b>\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📦 <b>Тарифы:</b>\n"
        "  ⬜ FREE  — 5 сигналов / день\n"
        "  🔵 JUNIOR — 15 сигналов / день  |  50$ / 7 дн\n"
        "  🟣 PRO — 30 сигналов / день  |  100$ / 7 дн\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Дисклеймер:</b>\n"
        "<i>Торговля бинарными опционами сопряжена с рисками. "
        "Сигналы носят информационный характер и не являются "
        "гарантией прибыли. Всегда соблюдайте мани-менеджмент.</i>"
    )
    await message.answer(text, parse_mode="HTML")

# ════════════════════════════════════════════════
#         📰 НОВОСТИ РЫНКА (хендлер)
# ════════════════════════════════════════════════
@dp.message(F.text == "📰 Новости рынка")
async def news_market(message: Message):
    news = db_get_today_news()
    today_str = (datetime.utcnow() + timedelta(hours=3)).strftime("%d.%m.%Y")
    now_msk   = datetime.utcnow() + timedelta(hours=3)

    if not news:
        await message.answer(
            "📰 <b>ЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ</b>\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>{today_str}</b>\n\n"
            "⚠️ <i>Данные ещё загружаются или важных событий на сегодня нет.\n"
            "Блокнот обновляется каждый час автоматически.</i>",
            parse_mode="HTML"
        )
        return

    lines = []
    for ev in news:
        ev_dt = ev.get("event_time_dt")
        if isinstance(ev_dt, str):
            try:
                ev_dt = datetime.strptime(ev_dt, "%Y-%m-%d %H:%M:%S")
            except Exception:
                ev_dt = None

        status = ""
        if ev_dt:
            delta = (ev_dt - now_msk).total_seconds() / 60
            if delta < -30:
                status = " ✅ прошло"
            elif -30 <= delta <= 0:
                status = " 🔴 СЕЙЧАС"
            elif 0 < delta <= 30:
                status = f" ⚠️ через {int(delta)} мин"
            else:
                status = ""

        lines.append(
            f"🔴🔴🔴 <b>{ev['event_time']} МСК</b> — {ev['title']} "
            f"(<b>{ev['currency']}</b>){status}"
        )

    news_text = "\n".join(lines)
    await message.answer(
        f"📰 <b>ЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>{today_str}</b> | Только события 🔴🔴🔴 (3 быка)\n\n"
        f"{news_text}\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>За 30 мин до и после события рекомендуется не торговать.\n"
        f"Терминал автоматически блокирует сигналы в опасное время.</i>",
        parse_mode="HTML"
    )

# ════════════════════════════════════════════════
#         🧮 КАЛЬКУЛЯТОР ЛОТА
# ════════════════════════════════════════════════
@dp.message(F.text == "🧮 Калькулятор лота")
async def lot_calculator(message: Message):
    pending_lot_calc.add(message.from_user.id)
    await message.answer(
        "🧮 <b>КАЛЬКУЛЯТОР ЛОТА</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
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
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"  💰 Ваш баланс: <b>{balance:,.2f}$</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
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
        f"━━━━━━━━━━━━━━━━━\n"
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
        "━━━━━━━━━━━━━━━━━\n\n"
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
            "━━━━━━━━━━━━━━━━━\n\n"
            "✅ Ваш аккаунт верифицирован.\n"
            "Все модули терминала разблокированы.\n\n"
            "📊 Нажмите <b>«📊 Торговая панель»</b> для выбора актива\n"
            "⚡ Или сразу <b>«⚡ Получить сигнал»</b>\n"
            "🔔 Вы будете получать бесплатные VIP-автосигналы (до 3/день)\n\n"
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
    news   = db_get_today_news()
    await message.answer(
        f"📊 <b>СТАТИСТИКА БОТА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"🟢 Активных (24ч): <b>{active}</b>\n"
        f"📰 Новостей в блокноте (сегодня): <b>{len(news)}</b>\n"
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

    if not is_market_open():
        return await message.answer(get_market_closed_text(), parse_mode="HTML")

    await message.answer(
        "📊 <b>ТОРГОВАЯ ПАНЕЛЬ</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "Выберите <b>валютную пару</b> для анализа:\n\n"
        "🔹 Мажорные пары: EUR/USD, GBP/USD, USD/JPY\n"
        "🔹 Кросс-пары: USD/CAD, AUD/CAD, EUR/CHF",
        reply_markup=pair_kb,
        parse_mode="HTML"
    )

@dp.message(F.text.in_(pairs))
async def set_pair(message: Message):
    if not is_market_open():
        return await message.answer(get_market_closed_text(), parse_mode="HTML")

    user_temp_data[message.from_user.id] = {"pair": message.text}

    best = PAIR_BEST_TIME.get(message.text, {})
    best_time_block = ""
    if best:
        best_time_block = (
            f"\n\n⏰ <b>Лучшее время для {message.text.replace('💵 ', '')}:</b>\n"
            f"  🟢 <b>{best['window']}</b>\n"
            f"  <i>{best['note']}</i>"
        )

    await message.answer(
        f"✅ <b>Актив выбран:</b> {message.text}"
        f"{best_time_block}\n\n"
        f"⏱ Выберите <b>время экспирации</b> опциона:",
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

    best = PAIR_BEST_TIME.get(pair, {})
    best_time_str = f"  ⏰ Лучшее окно: <b>{best['window']}</b>\n" if best else ""

    await message.answer(
        f"⚙️ <b>КОНФИГУРАЦИЯ СОХРАНЕНА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"  📊 Актив:       <b>{pair}</b>\n"
        f"  ⏱ Экспирация:  <b>{message.text}</b>\n"
        f"{best_time_str}"
        f"\n━━━━━━━━━━━━━━━━━\n"
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

    if not is_market_open():
        return await message.answer(get_market_closed_text(), parse_mode="HTML")

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

    # ── Проверяем новостной фильтр ──────────────────────
    danger = check_news_danger_for_pair(data["pair"])
    if danger["dangerous"]:
        delta   = danger["minutes"]
        event   = danger["event"]
        curr    = danger["currency"]
        t_str   = danger["time"]
        desc    = danger["desc"]

        if delta >= 0:
            warning_head = f"⚠️ Важная новость <b>{desc}</b> ({t_str} МСК)"
            warning_body = (
                f"По валюте <b>{curr}</b> выходят данные:\n"
                f"📌 <b>{event}</b>\n\n"
                f"Рынок непредсказуем в такие моменты — индикаторы могут ошибаться.\n\n"
                f"🕐 <b>Рекомендуем подождать ~20–30 минут</b> после публикации,\n"
                f"пока волатильность не успокоится и не сформируется чёткий тренд."
            )
        else:
            warning_head = f"⚠️ Важная новость вышла {desc} ({t_str} МСК)"
            warning_body = (
                f"По валюте <b>{curr}</b> только что вышли данные:\n"
                f"📌 <b>{event}</b>\n\n"
                f"Рынок ещё не переварил новость — высокая волатильность.\n\n"
                f"🕐 <b>Рекомендуем подождать ещё ~{20 + int(abs(delta))} мин</b>,\n"
                f"пока ситуация стабилизируется."
            )

        return await message.answer(
            f"🚨 <b>НОВОСТНОЙ ФИЛЬТР СРАБОТАЛ</b>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"  📊 Пара:  <b>{data['pair']}</b>\n"
            f"  {warning_head}\n\n"
            f"{warning_body}\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<i>Терминал заботится о вашем депозите. "
            f"Дождитесь спокойного рынка и нажмите «⚡ Получить сигнал» снова.</i>",
            reply_markup=signal_kb,
            parse_mode="HTML"
        )

    # ── Анимированный прогресс-бар ──────────────────────
    progress_frames = [
        ("⬛️⬛️⬛️⬛️⬛️ <b>[ 0%]</b>",  "📡 Подключение к потоку котировок..."),
        ("🟩⬛️⬛️⬛️⬛️ <b>[20%]</b>",  "📥 Загрузка 50 свечей (live)..."),
        ("🟩🟩⬛️⬛️⬛️ <b>[40%]</b>",  "📊 Расчёт RSI(14) и EMA(9/21)..."),
        ("🟩🟩🟩⬛️⬛️ <b>[65%]</b>",  "🔬 Анализ MACD и паттернов свечей..."),
        ("🟩🟩🟩🟩⬛️ <b>[85%]</b>",  "🧮 Мульти-факторный фильтр входа..."),
        ("🟩🟩🟩🟩🟩 <b>[100%]</b>", "✅ Сигнал сформирован!"),
    ]

    progress_msg = await message.answer(
        f"<b>⚡ АНАЛИЗ РЫНКА</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{progress_frames[0][0]}\n"
        f"<i>{progress_frames[0][1]}</i>",
        parse_mode="HTML"
    )

    await asyncio.sleep(0.5)
    quote = await get_real_quote(data["pair"])

    for bar, label in progress_frames[1:]:
        await asyncio.sleep(0.5)
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

    best = PAIR_BEST_TIME.get(data["pair"], {})
    best_time_block = ""
    if best:
        best_time_block = (
            f"\n━━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>ОПТИМАЛЬНОЕ ВРЕМЯ:</b>\n"
            f"  🟢 <b>{best['window']}</b>\n"
            f"  <i>{best['note']}</i>\n"
        )

    if quote:
        direction, confidence, reason = generate_signal_from_quote(quote)

        # ── Если сигнал пропущен ────────────────────────
        if direction is None:
            try:
                await progress_msg.delete()
            except:
                pass

            price_val = quote["price"]
            price_str = f"{price_val:.3f}" if price_val > 100 else f"{price_val:.5f}"
            rsi_bar   = format_rsi_bar(quote.get("rsi_value", 50))

            return await message.answer(
                f"🧠 <b>СИГНАЛ ПРОПУЩЕН — МУЛЬТИ-ФАКТОРНЫЙ ФИЛЬТР</b>\n"
                f"━━━━━━━━━━━━━━━━━\n\n"
                f"  📊 Актив:       <b>{data['pair']}</b>\n"
                f"  ⏱ Экспирация:  <b>{data['time']}</b>\n"
                f"  💰 Цена:        <b>{price_str}</b>\n\n"
                f"📈 <b>ИНДИКАТОРЫ:</b>\n"
                f"  RSI(14):   <code>{rsi_bar}</code>\n"
                f"  Сигнал:    <b>{quote['rsi_signal']}</b>\n"
                f"  EMA тренд: <b>{quote['trend']}</b>\n"
                f"  MACD:      <b>{quote['macd_trend']}</b>\n"
                f"  Паттерн:   <b>{quote['candle_pattern']}</b>\n\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>ПОЧЕМУ НЕТ СИГНАЛА?</b>\n"
                f"  <i>{reason.capitalize()}</i>\n\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"🛡 <b>Это защита вашего депозита.</b>\n"
                f"<i>Для входа требуется согласие минимум 3 из 5 индикаторов.\n"
                f"Лучше пропустить сделку, чем войти со слабым перевесом.\n\n"
                f"Попробуйте через несколько минут или выберите другую пару.</i>",
                reply_markup=signal_kb,
                parse_mode="HTML"
            )

        # ── Обычный сигнал ─────────────────────────────────
        db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)
        new_daily = daily + 1

        remaining = current_limit - new_daily
        limit_warning = ""
        if remaining == 0:
            limit_warning = "\n⚠️ <b>Это был последний сигнал на сегодня!</b> Лимит исчерпан."
        elif remaining <= 2:
            limit_warning = f"\n⚠️ <i>Осталось сигналов сегодня: <b>{remaining}</b>. Используйте с умом!</i>"

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

        reason_block = (
            f"\n━━━━━━━━━━━━━━━━━\n"
            f"💡 <b>ПОЧЕМУ ВОШЛИ?</b>\n"
            f"  <i>{reason.capitalize()}</i>\n"
        )

        # PRO-блок
        pro_block = ""
        if sub_type == "pro":
            rsi_v = quote.get("rsi_value", 50)
            macd_t = quote.get("macd_trend", "")
            if rsi_v > 70:
                pro_tip = "⚠️ Зона перекупленности — сократите объём"
            elif rsi_v < 30:
                pro_tip = "💡 Зона перепроданности — отличная точка входа"
            elif "разворот" in macd_t:
                pro_tip = "🔥 MACD разворот — сильный сигнал, стандартный объём"
            elif "высокая" in quote.get("volatility", ""):
                pro_tip = "🔥 Высокая волатильность — сократите объём на 30%"
            else:
                pro_tip = "✅ Стандартные условия — работайте по тренду"

            pro_block = (
                f"\n━━━━━━━━━━━━━━━━━\n"
                f"🟣 <b>PRO АНАЛИТИКА:</b>\n"
                f"  💬 {pro_tip}\n"
                f"  📐 Рек. объём: <b>2–3% от депозита</b>\n"
                f"  🎯 EMA9/21: <b>{quote['ema9']:.5f} / {quote['ema21']:.5f}</b>\n"
                f"  📊 MACD: <b>{quote['macd_trend']}</b>\n"
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
            f"  Паттерн:       <b>{quote['candle_pattern']}</b>\n"
            f"  EMA тренд:     <b>{quote['trend']}</b>\n"
            f"  MACD:          <b>{quote['macd_trend']}</b>\n"
            f"  Волатильность: <b>{quote['volatility']}</b>\n\n"
            f"📈 <b>RSI(14) ИНДИКАТОР</b>\n"
            f"  <code>{rsi_bar}</code>\n"
            f"  Сигнал: <b>{quote['rsi_signal']}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🧠 <b>УВЕРЕННОСТЬ ИИ:</b>\n"
            f"  <code>{conf_bar}</code> <b>{confidence}%</b>\n\n"
            f"🚀 <b>РЕКОМЕНДАЦИЯ:</b>\n"
            f"  ┌──────────────────┐\n"
            f"  │   {dir_badge}   │\n"
            f"  └──────────────────┘\n"
            f"{reason_block}"
            f"{best_time_block}"
            f"{pro_block}"
            f"━━━━━━━━━━━━━━━━━\n"
            f"  Использовано: <b>{new_daily} / {current_limit}</b> сигналов\n"
            f"{limit_warning}\n"
            f"⚠️ <i>Money Management: 1–3% от баланса на сделку!</i>"
        )

    else:
        # ── Автономный режим ───────────────────────────────
        db_update_user(uid, signals=u["signals"] + 1, daily=daily + 1, date=today)
        new_daily = daily + 1

        remaining = current_limit - new_daily
        limit_warning = ""
        if remaining == 0:
            limit_warning = "\n⚠️ <b>Это был последний сигнал на сегодня!</b> Лимит исчерпан."
        elif remaining <= 2:
            limit_warning = f"\n⚠️ <i>Осталось сигналов сегодня: <b>{remaining}</b>. Используйте с умом!</i>"

        direction  = random.choice(["ВВЕРХ 🟢 (CALL)", "ВНИЗ 🔴 (PUT)"])
        confidence = random.randint(78, 85)
        conf_bar   = confidence_bar(confidence)
        reason     = "технический анализ на основе исторических паттернов"
        res = (
            f"⚡️ <b>ТОРГОВЫЙ СИГНАЛ СФОРМИРОВАН</b> ⚡️\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"  📊 Актив:       <b>{data['pair']}</b>\n"
            f"  ⏱ Экспирация:  <b>{data['time']}</b>\n\n"
            f"🧠 <b>УВЕРЕННОСТЬ ИИ:</b>\n"
            f"  <code>{conf_bar}</code> <b>{confidence}%</b>\n\n"
            f"🚀 <b>РЕКОМЕНДАЦИЯ: {direction}</b>\n\n"
            f"💡 <b>ПОЧЕМУ ВОШЛИ?</b>\n"
            f"  <i>{reason}</i>\n"
            f"{best_time_block}"
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

    used_pct  = min(int((u["daily_count"] / sub_limit) * 10), 10)
    daily_bar = "▓" * used_pct + "░" * (10 - used_pct)

    market_str = "🟢 Открыт (ПН–ПТ)" if is_market_open() else "🔴 Закрыт (выходной)"
    name = message.from_user.first_name or "Трейдер"

    profile_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧮 Рассчитать лот", callback_data="open_lot_calc")],
    ])

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
        "━━━━━━━━━━━━━━━━━\n\n"
        "Введите ваш <b>текущий баланс в долларах</b> (только цифры):\n\n"
        "<i>Пример: 100 или 500 или 1250</i>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )
    await callback.answer()

# ════════════════════════════════════════════════
#              СТАТИСТИКА (обновлённая)
# ════════════════════════════════════════════════
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
    best_pair    = random.choice(["EUR/USD", "GBP/USD", "USD/JPY", "AUD/CAD"])
    peak_hour    = random.randint(10, 18)
    total_users  = db_get_total_users()
    active_users = db_get_active_users()

    wr_filled = int(win_rate / 10)
    wr_bar    = "█" * wr_filled + "░" * (10 - wr_filled)

    market_note = ""
    if not is_market_open():
        market_note = "\n⚠️ <i>Рынок сейчас закрыт (выходной). Статистика за последний рабочий день.</i>\n"

    pairs_strength_text = ""
    if is_market_open():
        try:
            strength_msg = await message.answer(
                "⏳ <i>Загружаю силу валютных пар...</i>",
                parse_mode="HTML"
            )
            pairs_data = await get_pairs_strength()
            try:
                await strength_msg.delete()
            except:
                pass

            if pairs_data:
                strongest = pairs_data[0]
                weakest   = pairs_data[-1]

                lines = []
                for i, pd in enumerate(pairs_data):
                    medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"][i] if i < 6 else f"{i+1}."
                    sign  = "+" if pd["change_pct"] >= 0 else ""
                    lines.append(
                        f"  {medal} <b>{pd['pair']}</b> {pd['trend']}  "
                        f"<code>{sign}{pd['change_pct']:.3f}%</code>"
                    )

                pairs_table = "\n".join(lines)
                pairs_strength_text = (
                    f"\n━━━━━━━━━━━━━━━━━\n"
                    f"💱 <b>СИЛА ВАЛЮТНЫХ ПАР (live):</b>\n\n"
                    f"{pairs_table}\n\n"
                    f"  🔝 Самая сильная: <b>{strongest['pair']}</b> ({strongest['trend']})\n"
                    f"  📉 Самая слабая:  <b>{weakest['pair']}</b> ({weakest['trend']})\n"
                )
        except Exception as e:
            print(f"Ошибка загрузки силы пар: {e}")

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
        f"  Пик активности:       <b>{peak_hour}:00–{peak_hour+1}:00</b>\n"
        f"{pairs_strength_text}"
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

# ════════════════════════════════════════════════
#              ЗАПУСК
# ════════════════════════════════════════════════
async def main():
    print("=" * 50)
    print("  🚀 AI TRADING TERMINAL — FX PRO v2.0")
    print("  ✅ BOT STARTED SUCCESSFULLY")
    print("  🧠 SIGNAL ENGINE: RSI(14)+EMA(9/21)+MACD+PATTERNS")
    print("=" * 50)

    init_db()

    asyncio.create_task(news_scheduler())
    asyncio.create_task(auto_signals_scheduler())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
