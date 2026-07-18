import json
import os
import re
import sqlite3
import threading
from pathlib import Path
import time
from typing import Optional, Dict, List, Tuple

import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not TOKEN:
    raise ValueError("TOKEN not found")
if not ADMIN_ID_RAW:
    raise ValueError("ADMIN_ID not found")

ADMIN_ID = int(ADMIN_ID_RAW)
bot = telebot.TeleBot(TOKEN)

DB_PATH = os.getenv("DB_PATH", "db.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("PRAGMA busy_timeout = 5000")
conn.execute("PRAGMA journal_mode = WAL")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER DEFAULT 0,
    ref INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    price INTEGER NOT NULL,
    status TEXT DEFAULT 'awaiting_receipt'
)
""")

conn.commit()

def add_column_if_not_exists(table: str, column: str, definition: str):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()

add_column_if_not_exists("orders", "pay_amount", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "discount_used", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "ref_bonus_given", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "country", "TEXT DEFAULT ''")
add_column_if_not_exists("orders", "tariff", "TEXT DEFAULT ''")
add_column_if_not_exists("orders", "created_at", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "receipt_received_at", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "paid_at", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "esim_sent_at", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "install_confirmed", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "source_code", "TEXT DEFAULT ''")
add_column_if_not_exists("orders", "partner_code", "TEXT DEFAULT ''")
add_column_if_not_exists("orders", "partner_rate", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "partner_commission", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "username", "TEXT DEFAULT ''")
add_column_if_not_exists("users", "first_name", "TEXT DEFAULT ''")
add_column_if_not_exists("users", "first_source", "TEXT DEFAULT ''")
add_column_if_not_exists("users", "first_source_at", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "active_partner_code", "TEXT DEFAULT ''")
add_column_if_not_exists("users", "active_partner_until", "INTEGER DEFAULT 0")

cursor.execute("""
CREATE TABLE IF NOT EXISTS reminder_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    reminder_type TEXT NOT NULL,
    scheduled_at INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    last_error TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    sent_at INTEGER DEFAULT 0,
    UNIQUE(order_id, reminder_type)
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS ad_sources (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    is_active INTEGER DEFAULT 1
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS partners (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    telegram_user_id INTEGER UNIQUE,
    commission_rate INTEGER NOT NULL DEFAULT 20,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS partner_commissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE,
    partner_code TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    sale_amount INTEGER NOT NULL,
    commission_rate INTEGER NOT NULL,
    commission_amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',
    payout_id INTEGER,
    created_at INTEGER NOT NULL,
    paid_at INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS partner_payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_code TEXT NOT NULL,
    amount INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    confirmed_by INTEGER NOT NULL
)
""")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_partner_commissions_code_status ON partner_commissions(partner_code, status)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_partner_commissions_user ON partner_commissions(user_id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_partner_payouts_code ON partner_payouts(partner_code)")
conn.commit()

REF_BONUS = 100
DEFAULT_PARTNER_RATE = 20
PARTNER_WINDOW_HOURS = 72
PARTNER_WINDOW_SECONDS = PARTNER_WINDOW_HOURS * 60 * 60

ROAMING_COMPARISON = {
    "operator": "МТС",
    "plan": "10 ГБ / 14 дней",
    "price": 3900,
    "checked_at": "10.07.2026",
}

TRAVEL_PLAN_ORDER = ['1GB / 7 дней', '3GB / 30 дней', '5GB / 30 дней', '10GB / 30 дней', '20GB / 30 дней', '50GB / 90 дней', '100GB / 180 дней']

PRICE_FILE = Path(__file__).resolve().parent / "country_prices.json"

def load_country_prices() -> Dict[str, Dict[str, int]]:
    try:
        raw = PRICE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError as exc:
        raise RuntimeError(f"country_prices.json not found: {PRICE_FILE}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"country_prices.json is not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or not data:
        raise RuntimeError("country_prices.json must contain a non-empty country price object")

    normalized: Dict[str, Dict[str, int]] = {}
    for country, plans in data.items():
        if not isinstance(country, str) or not country.strip():
            raise RuntimeError("country_prices.json contains an invalid country name")
        if not isinstance(plans, dict) or not plans:
            raise RuntimeError(f"country_prices.json contains no plans for {country}")
        normalized[country] = {}
        for plan, price in plans.items():
            if not isinstance(plan, str) or plan not in TRAVEL_PLAN_ORDER:
                raise RuntimeError(f"country_prices.json contains an invalid plan for {country}: {plan}")
            if not isinstance(price, int):
                raise RuntimeError(f"country_prices.json contains a non-integer price for {country} / {plan}")
            normalized[country][plan] = price
    return normalized

COUNTRY_PRICES = load_country_prices()


REGIONS = {
    "🔥 Популярные страны": [
        "Turkey", "Egypt", "United Arab Emirates", "Thailand", "Vietnam",
        "China", "Kazakhstan", "Georgia", "Armenia", "Indonesia"
    ],
    "🌍 Европа": ["Austria", "Belgium", "Croatia", "Czech Republic", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland", "United Kingdom"],
    "🌏 Азия": ["Armenia", "Azerbaijan", "Bangladesh", "Cambodia", "China", "Georgia", "Hong Kong", "India", "Indonesia", "Israel", "Japan", "Kazakhstan", "Kuwait", "Macao", "Malaysia", "Oman", "Pakistan", "Philippines", "Qatar", "Saudi Arabia", "Singapore", "South Korea", "Sri Lanka", "Taiwan", "Tajikistan", "Thailand", "Turkey", "United Arab Emirates", "Uzbekistan", "Vietnam"],
    "🌐 СНГ": ["Armenia", "Azerbaijan", "Georgia", "Kazakhstan", "Kyrgyzstan", "Moldova", "Tajikistan", "Ukraine", "Uzbekistan"],
    "🌎 Америка": ["United States", "Canada", "Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Ecuador", "Peru", "Panama", "Paraguay", "Uruguay", "Costa Rica", "El Salvador"],
}

EMOJI = {
    "Turkey": "🇹🇷", "Egypt": "🇪🇬", "United Arab Emirates": "🇦🇪", "Thailand": "🇹🇭",
    "Vietnam": "🇻🇳", "China": "🇨🇳", "Kazakhstan": "🇰🇿", "Georgia": "🇬🇪",
    "Armenia": "🇦🇲", "Indonesia": "🇮🇩", "Spain": "🇪🇸", "Italy": "🇮🇹",
    "France": "🇫🇷", "Germany": "🇩🇪", "United States": "🇺🇸", "Canada": "🇨🇦",
    "Mexico": "🇲🇽", "Japan": "🇯🇵", "India": "🇮🇳", "United Kingdom": "🇬🇧",
    "Portugal": "🇵🇹", "Netherlands": "🇳🇱", "Greece": "🇬🇷", "Austria": "🇦🇹",
    "Switzerland": "🇨🇭", "Croatia": "🇭🇷", "Poland": "🇵🇱", "Brazil": "🇧🇷",
    "Argentina": "🇦🇷", "Chile": "🇨🇱", "South Korea": "🇰🇷", "Singapore": "🇸🇬",
    "Malaysia": "🇲🇾", "Saudi Arabia": "🇸🇦", "Qatar": "🇶🇦", "Tunisia": "🇹🇳",
    "South Africa": "🇿🇦", "New Zealand": "🇳🇿", "Israel": "🇮🇱", "Ukraine": "🇺🇦",
    "Azerbaijan": "🇦🇿", "Kyrgyzstan": "🇰🇬", "Tajikistan": "🇹🇯", "Uzbekistan": "🇺🇿",
}

RU_COUNTRIES = {
    "турция": "Turkey", "египет": "Egypt", "оаэ": "United Arab Emirates",
    "эмираты": "United Arab Emirates", "дубай": "United Arab Emirates",
    "таиланд": "Thailand", "тайланд": "Thailand", "вьетнам": "Vietnam",
    "китай": "China", "пекин": "China", "шанхай": "China",
    "казахстан": "Kazakhstan", "грузия": "Georgia", "армения": "Armenia",
    "индонезия": "Indonesia", "бали": "Indonesia", "испания": "Spain",
    "италия": "Italy", "германия": "Germany", "франция": "France",
    "сша": "United States", "америка": "United States", "япония": "Japan",
    "индия": "India", "великобритания": "United Kingdom", "англия": "United Kingdom",
    "португалия": "Portugal", "нидерланды": "Netherlands", "голландия": "Netherlands",
    "греция": "Greece", "австрия": "Austria", "швейцария": "Switzerland",
    "хорватия": "Croatia", "польша": "Poland", "бразилия": "Brazil",
    "аргентина": "Argentina", "чили": "Chile", "корея": "South Korea",
    "южная корея": "South Korea", "сингапур": "Singapore", "малайзия": "Malaysia",
    "катар": "Qatar", "тунис": "Tunisia", "мексика": "Mexico", "канада": "Canada",
    "филиппины": "Philippines", "мальдивы": "Maldives",
    "uae": "United Arab Emirates", "usa": "United States",
}

COUNTRY_GUIDES = {
    "Turkey": {
        "apps": [
            ("🚕 BiTaksi", "вызов официального такси"),
            ("🚌 Obilet", "покупка билетов на автобусы между городами"),
            ("🗺 Google Maps", "маршруты и офлайн-карты"),
        ],
        "tip": "Скачайте карту нужного города заранее.",
    },
    "Egypt": {
        "apps": [
            ("🚕 Uber или Careem", "заказ такси с ценой в приложении"),
            ("🗺 Google Maps", "маршруты и офлайн-карты"),
            ("🗣 Google Translate", "перевод вывесок, меню и фраз"),
        ],
        "tip": "Офлайн-карту и арабский язык для переводчика лучше скачать до поездки.",
    },
    "United Arab Emirates": {
        "apps": [
            ("🚕 Careem", "такси, доставка еды и другие городские услуги"),
            ("🗺 Google Maps", "маршруты и поиск нужных мест"),
            ("💬 WhatsApp", "сообщения и связь с отелями"),
        ],
        "warning": "Голосовые и видеозвонки через WhatsApp могут быть ограничены.",
    },
    "Thailand": {
        "apps": [
            ("🚕 Grab или Bolt", "заказ такси и мототакси"),
            ("💬 LINE", "связь с местными компаниями и сервисами"),
            ("🗺 Google Maps", "маршруты и офлайн-карты"),
        ],
        "tip": "Установите приложения такси до прилёта.",
    },
    "Vietnam": {
        "apps": [
            ("🚕 Grab", "заказ такси, мототакси и доставки"),
            ("💬 Zalo", "популярный местный мессенджер"),
            ("🗺 Google Maps", "маршруты и поиск нужных мест"),
        ],
        "tip": "Grab пригодится уже при поездке из аэропорта.",
    },
    "China": {
        "apps": [
            ("💳 Alipay", "оплата покупок и повседневных услуг"),
            ("💬 WeChat", "сообщения, связь с местными и оплата"),
            ("🚕 DiDi", "заказ такси"),
        ],
        "warning": "Google, WhatsApp, Instagram и некоторые другие зарубежные сервисы могут быть недоступны.",
        "tip": "Установите приложения и зарегистрируйтесь в них до поездки.",
    },
    "Kazakhstan": {
        "apps": [
            ("🚕 Yandex Go", "заказ такси"),
            ("🗺 2GIS", "подробные карты, организации и офлайн-навигация"),
            ("🗺 Google Maps", "построение маршрутов"),
        ],
        "tip": "Скачайте карту нужного города в 2GIS заранее.",
    },
    "Georgia": {
        "apps": [
            ("🚕 Bolt", "заказ такси с ценой до начала поездки"),
            ("🗺 Google Maps", "маршруты и поиск нужных мест"),
            ("🗣 Google Translate", "перевод фраз и меню"),
        ],
        "tip": "В аэропорту удобнее заказывать машину через приложение.",
    },
    "Armenia": {
        "apps": [
            ("🚕 Yandex Go или GG", "заказ такси"),
            ("🗺 Maps.me", "офлайн-навигация за пределами Еревана"),
            ("💱 Rate.am", "курсы валют и поиск обменных пунктов"),
        ],
        "tip": "Офлайн-карты особенно пригодятся при поездках по стране.",
    },
    "Indonesia": {
        "apps": [
            ("🚕 Grab или Gojek", "такси, мототакси и доставка"),
            ("💬 WhatsApp", "связь с отелями, водителями и гидами"),
            ("🗺 Google Maps", "маршруты и поиск нужных мест"),
        ],
        "tip": "Grab и Gojek лучше установить до прилёта.",
    },
}

history: Dict[int, List[Tuple[str, Optional[str]]]] = {}
search_mode: Dict[int, bool] = {}
selection_mode: Dict[int, Dict[str, str]] = {}
admin_send_qr_target: Optional[int] = None
admin_send_qr_order_id: Optional[int] = None

def ensure_user(user_id: int, ref: Optional[int] = None, username: Optional[str] = None, first_name: Optional[str] = None) -> None:
    username = username or ""
    first_name = first_name or ""
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cursor.fetchone():
        cursor.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (username, first_name, user_id)
        )
        conn.commit()
        return
    if ref == user_id:
        ref = None
    cursor.execute(
        "INSERT INTO users (user_id, balance, ref, username, first_name) VALUES (?, ?, ?, ?, ?)",
        (user_id, 0, ref, username, first_name)
    )
    conn.commit()

def remember_user_from_message(message, ref: Optional[int] = None) -> None:
    ensure_user(
        message.from_user.id,
        ref=ref,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

def normalize_source_code(raw_code: str) -> Optional[str]:
    code = (raw_code or "").strip().lower()
    if code.startswith("ad_"):
        code = code[3:]
    if not re.fullmatch(r"[a-z0-9_]{2,48}", code):
        return None
    return code

def source_link(code: str) -> str:
    return f"https://t.me/esimlimebot?start=ad_{code}"

def ensure_ad_source(code: str, name: Optional[str] = None) -> None:
    clean_name = (name or code).strip() or code
    now = int(time.time())
    cursor.execute("SELECT code FROM ad_sources WHERE code=?", (code,))
    if cursor.fetchone():
        if name:
            cursor.execute("UPDATE ad_sources SET name=? WHERE code=?", (clean_name, code))
    else:
        cursor.execute(
            "INSERT INTO ad_sources (code, name, created_at, is_active) VALUES (?, ?, ?, 1)",
            (code, clean_name, now)
        )
    conn.commit()

def remember_ad_source_for_user(user_id: int, code: str) -> bool:
    cursor.execute("SELECT COALESCE(ref, 0), COALESCE(first_source, '') FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return False
    ref, first_source = row
    if ref or first_source:
        return False
    cursor.execute(
        "UPDATE users SET first_source=?, first_source_at=? WHERE user_id=?",
        (code, int(time.time()), user_id)
    )
    conn.commit()
    return True

def get_user_first_source(user_id: int) -> str:
    cursor.execute("SELECT COALESCE(first_source, '') FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else ""

def normalize_partner_code(raw_code: str) -> Optional[str]:
    code = (raw_code or "").strip().lower()
    if code.startswith("partner_"):
        code = code[8:]
    if not re.fullmatch(r"[a-z0-9_]{2,48}", code):
        return None
    return code

def partner_link(code: str) -> str:
    return f"https://t.me/esimlimebot?start=partner_{code}"

def get_partner_by_code(code: str):
    cursor.execute(
        "SELECT code, name, telegram_user_id, commission_rate, is_active FROM partners WHERE code=?",
        (code,)
    )
    return cursor.fetchone()

def get_partner_by_user(user_id: int):
    cursor.execute(
        "SELECT code, name, telegram_user_id, commission_rate, is_active FROM partners WHERE telegram_user_id=? AND is_active=1",
        (user_id,)
    )
    return cursor.fetchone()

def activate_partner_window(user_id: int, code: str) -> bool:
    partner = get_partner_by_code(code)
    if not partner or not partner[4]:
        return False
    until = int(time.time()) + PARTNER_WINDOW_SECONDS
    cursor.execute(
        "UPDATE users SET active_partner_code=?, active_partner_until=? WHERE user_id=?",
        (code, until, user_id)
    )
    conn.commit()
    return True

def get_active_partner_for_user(user_id: int) -> Tuple[str, int]:
    cursor.execute(
        "SELECT COALESCE(active_partner_code, ''), COALESCE(active_partner_until, 0) FROM users WHERE user_id=?",
        (user_id,)
    )
    row = cursor.fetchone()
    if not row:
        return "", 0
    code, until = row
    now = int(time.time())
    if not code or until <= now:
        if code or until:
            cursor.execute("UPDATE users SET active_partner_code='', active_partner_until=0 WHERE user_id=?", (user_id,))
            conn.commit()
        return "", 0
    partner = get_partner_by_code(code)
    if not partner or not partner[4]:
        cursor.execute("UPDATE users SET active_partner_code='', active_partner_until=0 WHERE user_id=?", (user_id,))
        conn.commit()
        return "", 0
    return code, int(partner[3] or DEFAULT_PARTNER_RATE)

def get_partner_amounts(code: str) -> Dict[str, int]:
    cursor.execute(
        "SELECT COUNT(DISTINCT user_id) FROM partner_commissions WHERE partner_code=? AND status IN ('available', 'paid')",
        (code,)
    )
    clients = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM partner_commissions WHERE partner_code=? AND status IN ('available', 'paid')",
        (code,)
    )
    paid_orders = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COALESCE(SUM(sale_amount), 0) FROM partner_commissions WHERE partner_code=? AND status IN ('available', 'paid')",
        (code,)
    )
    sales = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(commission_amount), 0) FROM partner_commissions WHERE partner_code=? AND status='available'", (code,))
    available = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(commission_amount), 0) FROM partner_commissions WHERE partner_code=? AND status='paid'", (code,))
    paid = cursor.fetchone()[0]
    return {
        "clients": clients,
        "paid_orders": paid_orders,
        "sales": sales,
        "available": available,
        "paid": paid,
    }

def partner_commission_status_label(status: str) -> str:
    if status == "available":
        return "к выплате"
    if status == "paid":
        return "выплачено"
    return status

def format_partner_commission_rows(rows) -> str:
    if not rows:
        return "Начислений пока нет"
    lines = []
    for country, tariff, sale_amount, commission_amount, status in rows:
        lines.append(
            f"{country or 'Не указано'} | {tariff or 'Не указано'}\n"
            f"Продажа: {format_price(sale_amount)} ₽\n"
            f"Вознаграждение: {format_price(commission_amount)} ₽\n"
            f"Статус: {partner_commission_status_label(status)}"
        )
    return "\n\n".join(lines)

def country_label(country: str) -> str:
    return f"{EMOJI.get(country, '🌐')} {country}"

def format_price(value: int) -> str:
    return f"{value:,}".replace(",", " ")

def build_country_guide(country: str) -> str:
    guide = COUNTRY_GUIDES.get(country)
    if not guide:
        return ""

    plan_name = "10GB / 30 дней"
    esim_price = COUNTRY_PRICES.get(country, {}).get(plan_name)
    if esim_price is None:
        return ""

    apps = guide.get("apps", [])[:3]
    app_lines = [f"{name} — {purpose}" for name, purpose in apps]
    savings = ROAMING_COMPARISON["price"] - esim_price

    parts = [
        f"{country_label(country)}",
        "",
    ]

    if app_lines:
        parts.extend([
            "📲 Полезные приложения:",
            "",
            "\n".join(app_lines),
        ])

    warning = guide.get("warning")
    if warning:
        parts.extend(["", f"⚠️ {warning}"])

    tip = guide.get("tip")
    if tip:
        parts.extend(["", f"💡 {tip}"])

    parts.extend([
        "",
        "💰 Сравнение 10 ГБ:",
        "",
        f"eSIMLime: 10 ГБ / 30 дней — {format_price(esim_price)} ₽",
        f"{ROAMING_COMPARISON['operator']}: {ROAMING_COMPARISON['plan']} — {format_price(ROAMING_COMPARISON['price'])} ₽",
        f"Экономия с eSIMLime — {format_price(savings)} ₽",
        "",
        f"Цена роуминга проверена {ROAMING_COMPARISON['checked_at']}.",
        "Стоимость роуминга может зависеть от оператора и условий тарифа.",
    ])

    return "\n".join(parts)

def push_screen(user_id: int, screen: str, payload: Optional[str] = None) -> None:
    stack = history.setdefault(user_id, [])
    if not stack or stack[-1] != (screen, payload):
        stack.append((screen, payload))

def reset_to_main(user_id: int) -> None:
    history[user_id] = [("main", None)]
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)

def go_back(user_id: int) -> Tuple[str, Optional[str]]:
    stack = history.setdefault(user_id, [("main", None)])
    if len(stack) > 1:
        stack.pop()
    return stack[-1]

def nav_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔙 Назад", "🏠 В начало")
    return kb

def main_keyboard(user_id: Optional[int] = None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✈️ eSIM для путешествий")
    kb.add("⚡ Подобрать eSIM")
    kb.add("📘 Инструкции")
    kb.add("👤 Личный кабинет", "❓ Помощь")
    if user_id == ADMIN_ID:
        kb.add("📊 Статистика", "📦 Заказы")
        kb.add("👥 Пользователи")
        kb.add("📣 Реклама")
        kb.add("🤝 Партнёры")
    elif user_id and get_partner_by_user(user_id):
        kb.add("🤝 Кабинет партнёра")
    return kb

def is_legacy_russia_text(text: str) -> bool:
    old_tariffs = {
        "1GB / 7 дней — 499₽",
        "3GB / 30 дней — 990₽",
        "5GB / 30 дней — 1290₽",
        "10GB / 30 дней — 2190₽",
        "20GB / 30 дней — 3590₽",
        "50GB / 90 дней — 7990₽",
        "100GB / 180 дней — 15990₽",
    }
    return "Россия" in text or text in old_tariffs

def show_russia_discontinued(chat_id: int, user_id: int) -> None:
    bot.send_message(
        chat_id,
        "Продажа eSIM для России прекращена. Выберите eSIM для путешествий.",
        reply_markup=main_keyboard(user_id)
    )

def parse_price_from_order_text(text: str) -> Optional[int]:
    try:
        return int(text.split("—")[1].replace("₽", "").strip())
    except Exception:
        return None

def parse_order_details(text: str) -> Tuple[str, str]:
    clean = text.strip()
    if " | " in clean:
        country_part, tariff_part = clean.split(" | ", 1)
        country = normalize_country_text(country_part)
        if not country and " " in country_part:
            country = country_part.split(" ", 1)[1].strip()
        tariff = tariff_part.split("—", 1)[0].strip()
        return country or country_part.strip(), tariff

    tariff = clean.split("—", 1)[0].strip()
    return "Russia", tariff


def get_valid_plan_price(country: str, tariff: str) -> Optional[int]:
    return COUNTRY_PRICES.get(country, {}).get(tariff)

def refresh_tariff_selection(chat_id: int, user_id: int, country: Optional[str]) -> None:
    if country and country in COUNTRY_PRICES:
        show_country(chat_id, user_id, country, add_to_history=False)
    else:
        show_main(chat_id, user_id, add_to_history=False)

def process_order_selection(
    chat_id: int,
    user_id: int,
    country: str,
    tariff: str,
    displayed_price: Optional[int] = None
) -> None:
    if country == "Russia":
        show_russia_discontinued(chat_id, user_id)
        return

    server_price = get_valid_plan_price(country, tariff)
    if server_price is None:
        bot.send_message(
            chat_id,
            "Не удалось проверить выбранный тариф. Пожалуйста, выберите его заново."
        )
        refresh_tariff_selection(chat_id, user_id, country)
        return

    if displayed_price is not None and displayed_price != server_price:
        bot.send_message(
            chat_id,
            "Цена тарифа обновилась. Пожалуйста, выберите тариф заново."
        )
        refresh_tariff_selection(chat_id, user_id, country)
        return

    price = server_price
    text = f"{country_label(country)} | {tariff} — {server_price}₽"

    balance = get_user_balance(user_id)
    discount_used = min(balance, price)
    pay_amount = price - discount_used

    if discount_used > 0:
        subtract_balance(user_id, discount_used)

    show_travel_instruction(chat_id, user_id, add_to_history=False)

    partner_code, partner_rate = get_active_partner_for_user(user_id)
    if partner_code:
        source_code = ""
        partner_commission = int(round(pay_amount * partner_rate / 100))
    else:
        source_code = get_user_first_source(user_id)
        partner_rate = 0
        partner_commission = 0
    status = "pending_review" if pay_amount == 0 else "awaiting_receipt"
    created_at = int(time.time())

    cursor.execute(
        """
        INSERT INTO orders (user_id, text, price, pay_amount, discount_used, status, country, tariff, created_at, source_code, partner_code, partner_rate, partner_commission)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, text, price, pay_amount, discount_used, status, country, tariff, created_at, source_code, partner_code, partner_rate, partner_commission)
    )
    conn.commit()
    order_id = cursor.lastrowid
    if status == "awaiting_receipt":
        schedule_reminder(user_id, order_id, "payment_30m", created_at + 30 * 60)
        schedule_reminder(user_id, order_id, "payment_24h", created_at + 24 * 60 * 60)

    if pay_amount == 0:
        bot.send_message(
            ADMIN_ID,
            f"Детали заказа #{order_id}\n"
            f"Покупатель: {format_user_for_admin(user_id)}\n"
            f"Страна: {country}\n"
            f"Тариф: {tariff}\n"
            f"Сумма: {price}₽\n"
            f"К оплате: 0₽"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"ok_{order_id}_{user_id}_{pay_amount}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"no_{order_id}_{user_id}")
        )

        bot.send_message(
            chat_id,
            f"🧾 Ваш заказ:\n{text}\n\n"
            f"Стоимость: {price}₽\n"
            f"Списано с баланса: {discount_used}₽\n"
            f"К оплате: 0₽\n\n"
            f"Заказ отправлен на подтверждение. Ожидайте данные для установки eSIM: ссылку и/или QR-код с инструкцией.",
            reply_markup=main_keyboard(user_id)
        )

        bot.send_message(
            ADMIN_ID,
            f"🧾 Новый заказ за баланс\n\n"
            f"Пользователь ID: {user_id}\n"
            f"Заказ: {text}\n"
            f"Стоимость: {price}₽\n"
            f"Списано с баланса: {discount_used}₽\n"
            f"К оплате: 0₽",
            reply_markup=kb
        )
        return

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📸 Отправить чек")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        "Перед оплатой:\n\n"
        "✔ Убедитесь, что телефон поддерживает eSIM\n"
        "✔ Установка занимает несколько минут\n"
        "✔ QR-код одноразовый — не удаляйте eSIM после установки\n\n"
        f"🧾 Ваш заказ:\n{text}\n\n"
        f"Стоимость: {price}₽\n"
        f"Списано с баланса: {discount_used}₽\n"
        f"К оплате: {pay_amount}₽\n\n"
        f"Оплата по СБП:\n"
        f"Номер: 89870005569\n"
        f"Банк: Т-Банк\n"
        f"Получатель: Федор Е.\n\n"
        f"После оплаты нажмите «📸 Отправить чек» и отправьте скриншот.",
        reply_markup=kb
    )


def format_user_for_admin(user_id: int) -> str:
    cursor.execute("SELECT username, first_name FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return f"ID: {user_id}"
    username, first_name = row
    parts = [f"ID: {user_id}"]
    if username:
        parts.append(f"@{username}")
    if first_name:
        parts.append(first_name)
    return " | ".join(parts)

def status_label(status: str) -> str:
    return {
        "awaiting_receipt": "ожидает чек",
        "pending_review": "на проверке",
        "paid": "оплачено",
        "cancel": "отменено",
    }.get(status, status)

def format_top_rows(rows: List[Tuple[str, int]], empty_text: str) -> str:
    if not rows:
        return empty_text
    return "\n".join(f"{i}. {name or 'Не указано'} — {cnt}" for i, (name, cnt) in enumerate(rows, start=1))

def install_instruction_text() -> str:
    return (
        "📲 Как установить eSIM\n\n"
        "Если в полученном сообщении есть ссылка установки, откройте её на том телефоне, куда устанавливаете eSIM, "
        "и следуйте подсказкам.\n\n"
        "На iPhone ссылка поддерживается начиная с iOS 17.4.\n"
        "На Android возможность зависит от модели телефона и поставщика eSIM.\n\n"
        "Если ссылка не открывается или её нет, используйте QR-код.\n\n"
        "⚠️ Не удаляйте установленную eSIM: повторная установка может быть недоступна."
    )

def schedule_reminder(user_id: int, order_id: int, reminder_type: str, scheduled_at: int, db_cursor=None, db_conn=None) -> None:
    db_cursor = db_cursor or cursor
    db_conn = db_conn or conn
    now = int(time.time())
    db_cursor.execute(
        """
        INSERT OR IGNORE INTO reminder_jobs
            (user_id, order_id, reminder_type, scheduled_at, status, attempts, last_error, created_at, sent_at)
        VALUES (?, ?, ?, ?, 'pending', 0, '', ?, 0)
        """,
        (user_id, order_id, reminder_type, scheduled_at, now)
    )
    db_conn.commit()

def cancel_order_reminders(order_id: int, db_cursor=None, db_conn=None) -> None:
    db_cursor = db_cursor or cursor
    db_conn = db_conn or conn
    db_cursor.execute(
        "UPDATE reminder_jobs SET status='cancelled' WHERE order_id=? AND status IN ('pending', 'processing')",
        (order_id,)
    )
    db_conn.commit()

def cancel_reminders_by_type(order_id: int, reminder_types, db_cursor=None, db_conn=None) -> None:
    db_cursor = db_cursor or cursor
    db_conn = db_conn or conn
    if isinstance(reminder_types, str):
        reminder_types = [reminder_types]
    for reminder_type in reminder_types:
        db_cursor.execute(
            "UPDATE reminder_jobs SET status='cancelled' WHERE order_id=? AND reminder_type=? AND status IN ('pending', 'processing')",
            (order_id, reminder_type)
        )
    db_conn.commit()

def reminder_stop_keyboard(order_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("📸 Как отправить чек", callback_data=f"receipt_help_{order_id}"),
        types.InlineKeyboardButton("🔕 Не напоминать", callback_data=f"reminder_stop_{order_id}")
    )
    return kb

def install_check_keyboard(order_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Всё установлено", callback_data=f"install_ok_{order_id}"),
        types.InlineKeyboardButton("🆘 Нужна помощь", callback_data=f"install_help_{order_id}")
    )
    return kb

def mark_due_reminder(db_cursor, job_id: int) -> bool:
    db_cursor.execute(
        "UPDATE reminder_jobs SET status='processing' WHERE id=? AND status='pending'",
        (job_id,)
    )
    return db_cursor.rowcount == 1

def send_reminder_job(db_cursor, job) -> bool:
    job_id, user_id, order_id, reminder_type, attempts = job
    db_cursor.execute(
        """
        SELECT status, country, tariff, pay_amount, created_at, receipt_received_at, esim_sent_at, install_confirmed
        FROM orders
        WHERE id=? AND user_id=?
        """,
        (order_id, user_id)
    )
    order = db_cursor.fetchone()
    if not order:
        return False

    status, country, tariff, pay_amount, created_at, receipt_received_at, esim_sent_at, install_confirmed = order
    country = country or "Не указано"
    tariff = tariff or "Не указано"

    if reminder_type in ("payment_30m", "payment_24h"):
        if status != "awaiting_receipt" or created_at <= 0:
            return False
        if reminder_type == "payment_30m":
            text = (
                "🧾 Вы выбрали тариф, но чек пока не получен\n\n"
                f"Ваш заказ:\n{country} | {tariff}\n\n"
                f"К оплате: {pay_amount} ₽\n\n"
                "Если вы уже оплатили, отправьте скриншот чека одним сообщением.\n"
                "Если нужна помощь — напишите @F_Evdokimov."
            )
        else:
            text = (
                "⏰ Напоминание о заказе\n\n"
                f"Вы выбирали:\n{country} | {tariff}\n\n"
                f"К оплате: {pay_amount} ₽\n\n"
                "Заказ пока не оплачен. Если он больше не нужен, напоминания можно отключить."
            )
        bot.send_message(user_id, text, reply_markup=reminder_stop_keyboard(order_id))
        return True

    if reminder_type == "review_15m":
        if status != "pending_review" or receipt_received_at <= 0:
            return False
        bot.send_message(
            user_id,
            "⏳ Чек получен и находится на проверке\n\n"
            "Повторно отправлять его не нужно.\n\n"
            "После подтверждения оплаты мы подготовим данные для установки eSIM.\n"
            "Если нужна помощь — напишите @F_Evdokimov."
        )
        return True

    if reminder_type == "install_2h":
        if status != "paid" or esim_sent_at <= 0 or install_confirmed:
            return False
        bot.send_message(
            user_id,
            "📲 Получилось установить eSIM?\n\n"
            "Если всё работает — подтвердите установку.\n\n"
            "Если возникла ошибка, напишите нам — поможем разобраться.",
            reply_markup=install_check_keyboard(order_id)
        )
        return True

    return False

def reminder_worker():
    worker_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    worker_conn.execute("PRAGMA busy_timeout = 5000")
    worker_conn.execute("PRAGMA journal_mode = WAL")
    worker_cursor = worker_conn.cursor()

    while True:
        try:
            now = int(time.time())
            worker_cursor.execute(
                """
                SELECT id, user_id, order_id, reminder_type, attempts
                FROM reminder_jobs
                WHERE status='pending' AND scheduled_at<=?
                ORDER BY scheduled_at ASC
                LIMIT 10
                """,
                (now,)
            )
            jobs = worker_cursor.fetchall()

            for job in jobs:
                job_id = job[0]
                if not mark_due_reminder(worker_cursor, job_id):
                    worker_conn.commit()
                    continue
                worker_conn.commit()

                try:
                    sent = send_reminder_job(worker_cursor, job)
                    if sent:
                        worker_cursor.execute(
                            "UPDATE reminder_jobs SET status='sent', sent_at=? WHERE id=?",
                            (int(time.time()), job_id)
                        )
                    else:
                        worker_cursor.execute(
                            "UPDATE reminder_jobs SET status='cancelled' WHERE id=?",
                            (job_id,)
                        )
                    worker_conn.commit()
                except Exception as exc:
                    attempts = job[4] + 1
                    if attempts >= 3:
                        worker_cursor.execute(
                            "UPDATE reminder_jobs SET status='failed', attempts=?, last_error=? WHERE id=?",
                            (attempts, str(exc)[:500], job_id)
                        )
                    else:
                        worker_cursor.execute(
                            """
                            UPDATE reminder_jobs
                            SET status='pending', attempts=?, last_error=?, scheduled_at=?
                            WHERE id=?
                            """,
                            (attempts, str(exc)[:500], int(time.time()) + 5 * 60, job_id)
                        )
                    worker_conn.commit()
        except Exception:
            try:
                worker_conn.rollback()
            except Exception:
                pass

        time.sleep(60)

def normalize_country_text(text: str) -> Optional[str]:
    clean = text.strip()
    lowered = clean.lower()
    if lowered in RU_COUNTRIES:
        return RU_COUNTRIES[lowered]
    for country in COUNTRY_PRICES.keys():
        if clean == country or clean == country_label(country):
            return country
    return None

def backfill_order_details() -> None:
    cursor.execute("SELECT id, text FROM orders WHERE country='' OR tariff='' OR country IS NULL OR tariff IS NULL")
    rows = cursor.fetchall()
    for order_id, order_text in rows:
        country, tariff = parse_order_details(order_text)
        cursor.execute(
            "UPDATE orders SET country=?, tariff=? WHERE id=?",
            (country, tariff, order_id)
        )
    if rows:
        conn.commit()

backfill_order_details()

def get_user_balance(user_id: int) -> int:
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def add_balance(user_id: int, amount: int):
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def subtract_balance(user_id: int, amount: int):
    cursor.execute("UPDATE users SET balance = MAX(balance - ?, 0) WHERE user_id=?", (amount, user_id))
    conn.commit()

def has_paid_orders(user_id: int, exclude_order_id: Optional[int] = None) -> bool:
    if exclude_order_id:
        cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='paid' AND id!=?", (user_id, exclude_order_id))
    else:
        cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='paid'", (user_id,))
    return cursor.fetchone()[0] > 0

def available_plan_by_gb(country: str, desired_gb: int) -> str:
    prices = COUNTRY_PRICES.get(country)
    if not prices:
        return "10GB / 30 дней"

    available = [plan for plan in TRAVEL_PLAN_ORDER if plan in prices]

    def gb_value(plan: str) -> int:
        try:
            return int(plan.split("GB")[0])
        except Exception:
            return 999

    sorted_plans = sorted(available, key=gb_value)
    for plan in sorted_plans:
        if gb_value(plan) >= desired_gb:
            return plan
    return sorted_plans[-1]

def recommend_gb(days: int, usage: str) -> int:
    if days <= 5:
        if usage == "light":
            return 3
        if usage == "medium":
            return 5
        return 10

    if days <= 10:
        if usage == "light":
            return 5
        if usage == "medium":
            return 10
        return 20

    if days <= 20:
        if usage == "light":
            return 10
        if usage == "medium":
            return 20
        return 50

    if usage == "light":
        return 20
    if usage == "medium":
        return 50
    return 100

def usage_label(usage: str) -> str:
    return {
        "light": "карты и мессенджеры",
        "medium": "карты, соцсети, фото",
        "heavy": "активно: видео, Reels, YouTube, раздача интернета",
    }.get(usage, usage)

def show_main(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        reset_to_main(user_id)

    bot.send_message(
        chat_id,
        "🌍 Интернет в поездках без роуминга\n\n"
        "Подключаете eSIM за несколько минут и пользуетесь интернетом сразу по прилёту.\n\n"
        "✔ Работает в 100+ странах\n"
        "✔ Не нужно искать местную SIM-карту\n"
        "✔ Можно подключить заранее\n"
        "✔ Поддержка, если что-то не получится\n\n"
        "👇 Выберите, куда вам нужен интернет",
        reply_markup=main_keyboard(user_id)
    )

def show_travel_home(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "travel")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔥 Популярные страны")
    kb.add("⚡ Подобрать eSIM")
    kb.add("🌍 Европа", "🌏 Азия")
    kb.add("🌐 СНГ", "🌎 Америка")
    kb.add("🔎 Поиск страны")
    kb.add("❓ Как это работает")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        "✈️ Интернет в путешествиях\n\n"
        "Обычно в поездках возникают одни и те же проблемы:\n"
        "— дорогой роуминг\n"
        "— нестабильный Wi-Fi\n"
        "— сложно вызвать такси, открыть карты или написать близким\n\n"
        "С eSIM интернет можно подключить заранее и пользоваться им без смены основной SIM-карты.\n\n"
        "👇 Выберите страну, регион или подбор тарифа",
        reply_markup=kb
    )

def show_region(chat_id: int, user_id: int, region: str, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "region", region)

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for country in REGIONS.get(region, []):
        if country in COUNTRY_PRICES:
            kb.add(country_label(country))
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, f"{region}\n\nВыберите страну:", reply_markup=kb)

def show_country(chat_id: int, user_id: int, country: str, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "country", country)

    prices = COUNTRY_PRICES.get(country)
    if not prices:
        bot.send_message(chat_id, "Страна пока недоступна.", reply_markup=nav_keyboard())
        return

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for plan_name in TRAVEL_PLAN_ORDER:
        price = prices.get(plan_name)
        if price is not None:
            kb.add(f"{country_label(country)} | {plan_name} — {price}₽")
    kb.add("❓ Как это работает")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    guide_text = build_country_guide(country)
    if guide_text:
        message_text = f"{guide_text}\n\n👇 Выберите подходящий тариф"
    else:
        message_text = (
            f"{country_label(country)}\n\n"
            "Интернет для поездки без роуминга и поиска местной SIM-карты.\n\n"
            "✔ Подходит для карт, такси, мессенджеров и соцсетей\n"
            "✔ Не нужно менять основную SIM-карту\n"
            "✔ Можно установить заранее\n\n"
            "👇 Выберите тариф"
        )

    bot.send_message(
        chat_id,
        message_text,
        reply_markup=kb
    )

def show_how_it_works(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "how")

    bot.send_message(
        chat_id,
        "❓ Как работает eSIM\n\n"
        "1. Вы выбираете страну или тариф\n"
        "2. Оплачиваете заказ\n"
        "3. Отправляете чек\n"
        "4. Получаете ссылку установки и/или QR-код\n"
        "5. Открываете ссылку на телефоне и следуете подсказкам\n"
        "6. Если ссылка не поддерживается, устанавливаете eSIM через QR-код\n"
        "7. Включаете eSIM для передачи данных\n\n"
        "Без салонов связи, без физической SIM-карты и без сложных настроек.",
        reply_markup=nav_keyboard()
    )

def show_help(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "help")

    bot.send_message(
        chat_id,
        "❓ Помощь\n\n"
        "По всем вопросам: @F_Evdokimov\n\n"
        "Напишите, если нужна помощь с выбором тарифа, оплатой или установкой eSIM.",
        reply_markup=nav_keyboard()
    )

def show_cabinet(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "cabinet")

    balance = get_user_balance(user_id)

    cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='paid'", (user_id,))
    orders_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE ref=?", (user_id,))
    ref_count = cursor.fetchone()[0]

    ref_link = f"https://t.me/esimlimebot?start={user_id}"

    bot.send_message(
        chat_id,
        f"👤 Личный кабинет\n\n"
        f"Баланс: {balance}₽\n"
        f"Куплено eSIM: {orders_count}\n"
        f"Рефералов: {ref_count}\n\n"
        f"За каждого реферала после его первой покупки вы получаете {REF_BONUS}₽ на баланс.\n"
        f"Баланс автоматически списывается при следующей покупке.\n\n"
        f"Реферальная ссылка:\n{ref_link}",
        reply_markup=nav_keyboard()
    )

def show_admin_stats(chat_id: int, user_id: int):
    if user_id != ADMIN_ID:
        bot.send_message(chat_id, "Раздел доступен только администратору.", reply_markup=main_keyboard(user_id))
        return

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders")
    total_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status='paid'")
    paid_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status='awaiting_receipt'")
    awaiting_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status='pending_review'")
    pending_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status='cancel'")
    cancelled_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(pay_amount), 0) FROM orders WHERE status='paid'")
    revenue = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(discount_used), 0) FROM orders WHERE status='paid'")
    discounts = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(NULLIF(country, ''), 'Не указано') AS name, COUNT(*) AS cnt
        FROM orders
        GROUP BY name
        ORDER BY cnt DESC
        LIMIT 5
    """)
    top_countries = format_top_rows(cursor.fetchall(), "Пока нет заказов")

    cursor.execute("""
        SELECT COALESCE(NULLIF(tariff, ''), 'Не указано') AS name, COUNT(*) AS cnt
        FROM orders
        GROUP BY name
        ORDER BY cnt DESC
        LIMIT 5
    """)
    top_tariffs = format_top_rows(cursor.fetchall(), "Пока нет заказов")

    bot.send_message(
        chat_id,
        "📊 Статистика\n\n"
        f"Пользователей: {total_users}\n"
        f"Заказов: {total_orders}\n"
        f"Оплачено: {paid_orders}\n"
        f"Ожидают чек: {awaiting_orders}\n"
        f"На проверке: {pending_orders}\n"
        f"Отменено: {cancelled_orders}\n\n"
        f"Выручка: {revenue}₽\n"
        f"Бонусами: {discounts}₽\n\n"
        f"Топ стран:\n{top_countries}\n\n"
        f"Топ тарифов:\n{top_tariffs}",
        reply_markup=nav_keyboard()
    )

def show_admin_orders(chat_id: int, user_id: int):
    if user_id != ADMIN_ID:
        bot.send_message(chat_id, "Раздел доступен только администратору.", reply_markup=main_keyboard(user_id))
        return

    cursor.execute("""
        SELECT o.id, o.user_id, u.username, u.first_name, o.country, o.tariff, o.pay_amount, o.status
        FROM orders o
        LEFT JOIN users u ON u.user_id = o.user_id
        ORDER BY o.id DESC
        LIMIT 15
    """)
    recent_orders = cursor.fetchall()
    orders_text = ""
    if recent_orders:
        for order_id, order_user_id, username, first_name, country, tariff, amount, status in recent_orders:
            user_line = f"user_id: {order_user_id}"
            if username:
                user_line += f", @{username}"
            if first_name:
                user_line += f", {first_name}"
            orders_text += (
                f"#{order_id}: {user_line}\n"
                f"{country or 'Не указано'} | {tariff or 'Не указано'}\n"
                f"К оплате: {amount}₽ | {status_label(status)}\n\n"
            )
    else:
        orders_text = "Пока нет заказов"

    bot.send_message(
        chat_id,
        f"📦 Последние 15 заказов\n\n{orders_text}",
        reply_markup=nav_keyboard()
    )

def show_admin_users(chat_id: int, user_id: int):
    if user_id != ADMIN_ID:
        bot.send_message(chat_id, "Раздел доступен только администратору.", reply_markup=main_keyboard(user_id))
        return

    cursor.execute("""
        SELECT
            u.user_id,
            u.username,
            u.first_name,
            u.balance,
            u.ref,
            COALESCE(SUM(CASE WHEN o.status='paid' THEN 1 ELSE 0 END), 0) AS paid_orders,
            COUNT(o.id) AS total_orders
        FROM users u
        LEFT JOIN orders o ON o.user_id = u.user_id
        GROUP BY u.user_id, u.username, u.first_name, u.balance, u.ref
        ORDER BY u.rowid DESC
        LIMIT 15
    """)
    users = cursor.fetchall()
    users_text = ""
    if users:
        for row in users:
            user_id_value, username, first_name, balance, ref, paid_orders, total_orders = row
            user_line = f"user_id: {user_id_value}"
            if username:
                user_line += f", @{username}"
            if first_name:
                user_line += f", {first_name}"
            users_text += (
                f"{user_line}\n"
                f"Баланс: {balance}₽ | оплачено: {paid_orders} | всего: {total_orders}"
            )
            if ref:
                users_text += f" | ref: {ref}"
            users_text += "\n\n"
    else:
        users_text = "Пока нет пользователей"

    bot.send_message(
        chat_id,
        f"👥 Последние 15 пользователей\n\n{users_text}",
        reply_markup=nav_keyboard()
    )

def percent(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(part / total * 100, 1)

def get_source_stats(code: str) -> Dict[str, int]:
    cursor.execute("SELECT COUNT(*) FROM users WHERE first_source=?", (code,))
    attracted = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM orders WHERE source_code=?", (code,))
    ordered_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE source_code=?", (code,))
    total_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM orders WHERE source_code=? AND status='paid'", (code,))
    paid_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE source_code=? AND status='paid'", (code,))
    paid_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(pay_amount), 0) FROM orders WHERE source_code=? AND status='paid'", (code,))
    revenue = cursor.fetchone()[0]
    return {
        "attracted": attracted,
        "ordered_users": ordered_users,
        "total_orders": total_orders,
        "paid_users": paid_users,
        "paid_orders": paid_orders,
        "revenue": revenue,
    }

def show_source_stats(chat_id: int, user_id: int, raw_code: str) -> None:
    if user_id != ADMIN_ID:
        return

    code = normalize_source_code(raw_code)
    if not code:
        bot.send_message(chat_id, "Неверный код источника. Используйте латиницу, цифры и _, от 2 до 48 символов.")
        return

    cursor.execute("SELECT name FROM ad_sources WHERE code=?", (code,))
    row = cursor.fetchone()
    name = row[0] if row else code
    stats = get_source_stats(code)
    order_conversion = percent(stats["ordered_users"], stats["attracted"])
    paid_conversion = percent(stats["paid_users"], stats["attracted"])

    bot.send_message(
        chat_id,
        f"📣 Источник\n\n"
        f"Название: {name}\n"
        f"Код: {code}\n"
        f"Ссылка:\n{source_link(code)}\n\n"
        f"Пришли: {stats['attracted']}\n"
        f"Создали заказ: {stats['ordered_users']}\n"
        f"Всего заказов: {stats['total_orders']}\n"
        f"Оплатили: {stats['paid_users']}\n"
        f"Оплаченных заказов: {stats['paid_orders']}\n"
        f"Выручка: {format_price(stats['revenue'])} ₽\n\n"
        f"Конверсия в заказ: {order_conversion:.1f}%\n"
        f"Конверсия в оплату: {paid_conversion:.1f}%",
        reply_markup=nav_keyboard()
    )

def show_ad_stats(chat_id: int, user_id: int):
    if user_id != ADMIN_ID:
        bot.send_message(chat_id, "Раздел доступен только администратору.", reply_markup=main_keyboard(user_id))
        return

    cursor.execute("SELECT COUNT(*) FROM ad_sources")
    source_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE first_source!=''")
    attracted = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM orders WHERE source_code!=''")
    ordered_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE source_code!=''")
    total_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM orders WHERE source_code!='' AND status='paid'")
    paid_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE source_code!='' AND status='paid'")
    paid_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(pay_amount), 0) FROM orders WHERE source_code!='' AND status='paid'")
    revenue = cursor.fetchone()[0]

    order_conversion = percent(ordered_users, attracted)
    paid_conversion = percent(paid_users, attracted)

    cursor.execute("""
        SELECT
            s.code,
            s.name,
            COALESCE(u.attracted, 0) AS attracted,
            COALESCE(o.ordered_users, 0) AS ordered_users,
            COALESCE(o.paid_users, 0) AS paid_users,
            COALESCE(o.paid_orders, 0) AS paid_orders,
            COALESCE(o.revenue, 0) AS revenue
        FROM ad_sources s
        LEFT JOIN (
            SELECT first_source, COUNT(*) AS attracted
            FROM users
            WHERE first_source!=''
            GROUP BY first_source
        ) u ON u.first_source = s.code
        LEFT JOIN (
            SELECT
                source_code,
                COUNT(DISTINCT user_id) AS ordered_users,
                COUNT(DISTINCT CASE WHEN status='paid' THEN user_id END) AS paid_users,
                COUNT(CASE WHEN status='paid' THEN id END) AS paid_orders,
                COALESCE(SUM(CASE WHEN status='paid' THEN pay_amount ELSE 0 END), 0) AS revenue
            FROM orders
            WHERE source_code!=''
            GROUP BY source_code
        ) o ON o.source_code = s.code
        WHERE COALESCE(u.attracted, 0) > 0
        ORDER BY revenue DESC, paid_orders DESC, attracted DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()
    if rows:
        top_lines = []
        for idx, (code, name, src_attracted, src_ordered, src_paid_users, src_paid_orders, src_revenue) in enumerate(rows, start=1):
            top_lines.append(
                f"{idx}. {name or code}\n"
                f"Код: {code}\n"
                f"Пришли: {src_attracted} | Заказали: {src_ordered} | Оплатили: {src_paid_users}\n"
                f"Выручка: {format_price(src_revenue)} ₽"
            )
        top_text = "\n\n".join(top_lines)
    else:
        top_text = "Пока нет источников с пользователями"

    bot.send_message(
        chat_id,
        f"📣 Реклама\n\n"
        f"Источников: {source_count}\n"
        f"Пришли по рекламе: {attracted}\n"
        f"Создали заказ: {ordered_users}\n"
        f"Рекламных заказов: {total_orders}\n"
        f"Оплатили: {paid_users}\n"
        f"Оплаченных заказов: {paid_orders}\n"
        f"Выручка: {format_price(revenue)} ₽\n"
        f"Конверсия в заказ: {order_conversion:.1f}%\n"
        f"Конверсия в оплату: {paid_conversion:.1f}%\n\n"
        f"Топ-20 источников:\n\n{top_text}\n\n"
        f"Для подробной статистики:\n/source_stats КОД\n\n"
        f"Для создания новой ссылки:\n/source КОД НАЗВАНИЕ",
        reply_markup=nav_keyboard()
    )

def show_partner_cabinet(chat_id: int, user_id: int):
    partner = get_partner_by_user(user_id)
    if not partner:
        bot.send_message(chat_id, "Партнёрский кабинет доступен только подключённому партнёру.", reply_markup=main_keyboard(user_id))
        return

    code, name, telegram_user_id, rate, is_active = partner
    amounts = get_partner_amounts(code)
    cursor.execute("""
        SELECT o.country, o.tariff, pc.sale_amount, pc.commission_amount, pc.status
        FROM partner_commissions pc
        LEFT JOIN orders o ON o.id=pc.order_id
        WHERE pc.partner_code=?
        ORDER BY pc.id DESC
        LIMIT 10
    """, (code,))
    history_text = format_partner_commission_rows(cursor.fetchall())
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💸 Запросить выплату", callback_data=f"pp_req_{code}"))
    bot.send_message(
        chat_id,
        f"🤝 Кабинет партнёра\n\n"
        f"{name}\n"
        f"Код: {code}\n"
        f"Ставка: {rate}%\n"
        f"Ссылка:\n{partner_link(code)}\n\n"
        f"Клиентов: {amounts['clients']}\n"
        f"Оплаченных eSIM: {amounts['paid_orders']}\n"
        f"Продажи: {format_price(amounts['sales'])} ₽\n"
        f"К выплате: {format_price(amounts['available'])} ₽\n"
        f"Выплачено: {format_price(amounts['paid'])} ₽\n\n"
        f"Последние начисления:\n{history_text}",
        reply_markup=kb
    )

def show_admin_partners(chat_id: int, user_id: int):
    if user_id != ADMIN_ID:
        bot.send_message(chat_id, "Раздел доступен только администратору.", reply_markup=main_keyboard(user_id))
        return

    cursor.execute("SELECT COUNT(*) FROM partners WHERE is_active=1")
    active_partners = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM partner_commissions WHERE status IN ('available', 'paid')")
    partner_clients = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM partner_commissions WHERE status IN ('available', 'paid')")
    paid_esims = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(sale_amount), 0) FROM partner_commissions WHERE status IN ('available', 'paid')")
    sales = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(commission_amount), 0) FROM partner_commissions WHERE status='available'")
    available = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(commission_amount), 0) FROM partner_commissions WHERE status='paid'")
    paid = cursor.fetchone()[0]

    cursor.execute("""
        SELECT
            p.code,
            p.name,
            COALESCE(pc.clients, 0) AS clients,
            COALESCE(pc.paid_orders, 0) AS paid_orders,
            COALESCE(pc.sales, 0) AS sales,
            COALESCE((SELECT SUM(commission_amount) FROM partner_commissions pc WHERE pc.partner_code=p.code AND pc.status='available'), 0) AS available,
            COALESCE((SELECT SUM(commission_amount) FROM partner_commissions pc WHERE pc.partner_code=p.code AND pc.status='paid'), 0) AS paid
        FROM partners p
        LEFT JOIN (
            SELECT
                partner_code,
                COUNT(DISTINCT user_id) AS clients,
                COUNT(*) AS paid_orders,
                COALESCE(SUM(sale_amount), 0) AS sales
            FROM partner_commissions
            WHERE status IN ('available', 'paid')
            GROUP BY partner_code
        ) pc ON pc.partner_code=p.code
        GROUP BY p.code, p.name
        ORDER BY sales DESC, available DESC, paid_orders DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()
    if rows:
        top = []
        for idx, (code, name, clients, paid_orders, src_sales, src_available, src_paid) in enumerate(rows, start=1):
            top.append(
                f"{idx}. {name}\n"
                f"Код: {code}\n"
                f"Клиентов: {clients} | eSIM: {paid_orders}\n"
                f"Продажи: {format_price(src_sales)} ₽\n"
                f"К выплате: {format_price(src_available)} ₽\n"
                f"Выплачено: {format_price(src_paid)} ₽"
            )
        top_text = "\n\n".join(top)
    else:
        top_text = "Партнёров пока нет"

    bot.send_message(
        chat_id,
        f"🤝 Партнёры\n\n"
        f"Активных партнёров: {active_partners}\n"
        f"Клиентов партнёров: {partner_clients}\n"
        f"Оплаченных партнёрских eSIM: {paid_esims}\n"
        f"Продажи: {format_price(sales)} ₽\n"
        f"К выплате: {format_price(available)} ₽\n"
        f"Выплачено: {format_price(paid)} ₽\n\n"
        f"Топ-20:\n\n{top_text}\n\n"
        f"Создать партнёра:\n/partner_create КОД TELEGRAM_ID НАЗВАНИЕ\n\n"
        f"Подробная статистика:\n/partner_stats КОД\n\n"
        f"Отметить выплату:\n/partner_payout КОД",
        reply_markup=nav_keyboard()
    )

def show_partner_stats(chat_id: int, user_id: int, raw_code: str):
    if user_id != ADMIN_ID:
        return
    code = normalize_partner_code(raw_code)
    partner = get_partner_by_code(code) if code else None
    if not partner:
        bot.send_message(chat_id, "Партнёр не найден.")
        return
    code, name, telegram_user_id, rate, is_active = partner
    amounts = get_partner_amounts(code)
    cursor.execute("""
        SELECT o.country, o.tariff, pc.sale_amount, pc.commission_amount, pc.status
        FROM partner_commissions pc
        LEFT JOIN orders o ON o.id=pc.order_id
        WHERE pc.partner_code=?
        ORDER BY pc.id DESC
        LIMIT 10
    """, (code,))
    history_text = format_partner_commission_rows(cursor.fetchall())

    bot.send_message(
        chat_id,
        f"🤝 Партнёр\n\n"
        f"Название: {name}\n"
        f"Код: {code}\n"
        f"Telegram ID: {telegram_user_id}\n"
        f"Ставка: {rate}%\n"
        f"Ссылка:\n{partner_link(code)}\n\n"
        f"Клиентов: {amounts['clients']}\n"
        f"Оплаченных eSIM: {amounts['paid_orders']}\n"
        f"Продажи: {format_price(amounts['sales'])} ₽\n"
        f"К выплате: {format_price(amounts['available'])} ₽\n"
        f"Выплачено: {format_price(amounts['paid'])} ₽\n\n"
        f"Последние начисления:\n{history_text}\n\n"
        f"Для полной выплаты:\n/partner_payout {code}"
    )

def send_partner_payout_request(user_id: int, chat_id: int):
    partner = get_partner_by_user(user_id)
    if not partner:
        bot.send_message(chat_id, "Партнёр не найден.")
        return
    code, name, telegram_user_id, rate, is_active = partner
    amounts = get_partner_amounts(code)
    if amounts["available"] <= 0:
        bot.send_message(chat_id, "Сейчас нет суммы к выплате")
        return
    bot.send_message(
        ADMIN_ID,
        f"💸 Запрос выплаты\n\n"
        f"Партнёр: {name}\n"
        f"Код: {code}\n"
        f"Telegram ID: {telegram_user_id}\n"
        f"К выплате: {format_price(amounts['available'])} ₽\n\n"
        f"Для выплаты:\n/partner_payout {code}"
    )
    bot.send_message(chat_id, "Запрос отправлен администратору")

def show_instructions_menu(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "instructions")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, "📘 Инструкции\n\nВыберите нужную инструкцию:", reply_markup=kb)

def show_travel_instruction(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "travel_instruction")

    bot.send_message(
        chat_id,
        "✈️ Инструкция для путешествий\n\n"
        "1. Выберите страну и тариф.\n"
        "2. Проверьте, что телефон поддерживает eSIM.\n"
        "3. После оплаты отправьте чек.\n"
        "4. Получите ссылку установки и/или QR-код.\n"
        "5. Если пришла ссылка и телефон её поддерживает, откройте её на нужном устройстве.\n"
        "6. Если ссылка не работает или её нет, используйте QR-код.\n"
        "7. Установите eSIM до поездки.\n"
        "8. По прилёте включите роуминг на туристической eSIM.\n"
        "9. Переключите передачу данных на туристическую eSIM.\n\n"
        "⚠️ Не удаляйте установленную eSIM: повторная установка может быть недоступна.",
        reply_markup=nav_keyboard()
    )

def show_search(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = True
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "search")

    bot.send_message(
        chat_id,
        "🔎 Напишите страну.\n\n"
        "Можно на русском:\n"
        "Турция, Египет, ОАЭ, Таиланд, Вьетнам, Китай\n\n"
        "Можно на английском:\n"
        "Turkey, Egypt, UAE, Thailand, Vietnam, China",
        reply_markup=nav_keyboard()
    )

def start_selection(chat_id: int, user_id: int):
    search_mode[user_id] = False
    selection_mode[user_id] = {"step": "country"}
    push_screen(user_id, "select")

    bot.send_message(
        chat_id,
        "⚡ Подберём тариф с запасом\n\n"
        "Куда вы едете?\n\n"
        "Например: Турция, Египет, ОАЭ, Таиланд, Вьетнам, Китай",
        reply_markup=nav_keyboard()
    )

def ask_days(chat_id: int, user_id: int, country: str):
    selection_mode[user_id] = {"step": "days", "country": country}
    bot.send_message(
        chat_id,
        f"{country_label(country)}\n\nНа сколько дней поездка?\n\nНапишите число, например: 7, 10, 14",
        reply_markup=nav_keyboard()
    )

def ask_usage(chat_id: int, user_id: int, country: str, days: int):
    selection_mode[user_id] = {"step": "usage", "country": country, "days": str(days)}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🟢 Только карты и мессенджеры")
    kb.add("🟡 Карты, соцсети, фото")
    kb.add("🔴 Активно: видео и раздача")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        "Как будете пользоваться интернетом?",
        reply_markup=kb
    )

def finish_selection(chat_id: int, user_id: int, country: str, days: int, usage: str):
    desired_gb = recommend_gb(days, usage)
    plan = available_plan_by_gb(country, desired_gb)

    price = COUNTRY_PRICES.get(country, {}).get(plan)

    text = (
        f"🔥 Рекомендация\n\n"
        f"Страна: {country_label(country)}\n"
        f"Срок: {days} дней\n"
        f"Использование: {usage_label(usage)}\n\n"
        f"Рекомендую: {plan}\n"
    )

    if price:
        text += f"Цена: {price}₽\n\n"

    text += (
        "Почему с запасом:\n"
        "в поездке интернет уходит быстрее из-за карт, такси, мессенджеров, фото и нестабильного Wi-Fi.\n\n"
        "Ниже открою все тарифы по этой стране 👇"
    )

    bot.send_message(chat_id, text)
    selection_mode.pop(user_id, None)
    show_country(chat_id, user_id, country, add_to_history=True)

def render_from_state(chat_id: int, user_id: int, state: Tuple[str, Optional[str]]):
    screen, payload = state
    if screen == "main":
        show_main(chat_id, user_id, add_to_history=False)
    elif screen == "travel":
        show_travel_home(chat_id, user_id, add_to_history=False)
    elif screen == "region":
        show_region(chat_id, user_id, payload or "", add_to_history=False)
    elif screen == "country":
        show_country(chat_id, user_id, payload or "", add_to_history=False)
    elif screen == "help":
        show_help(chat_id, user_id, add_to_history=False)
    elif screen == "cabinet":
        show_cabinet(chat_id, user_id, add_to_history=False)
    elif screen == "instructions":
        show_instructions_menu(chat_id, user_id, add_to_history=False)
    elif screen == "search":
        show_search(chat_id, user_id, add_to_history=False)
    else:
        show_main(chat_id, user_id, add_to_history=False)

@bot.message_handler(commands=["start"])
def start_handler(message):
    user_id = message.from_user.id
    ref = None
    source_code = None
    partner_code = None
    invalid_partner_link = False
    parts = message.text.split()
    if len(parts) > 1:
        start_param = parts[1].strip()
        if start_param.isdigit():
            ref = int(start_param)
        elif start_param.lower().startswith("ad_"):
            source_code = normalize_source_code(start_param)
            if not source_code and user_id == ADMIN_ID:
                bot.send_message(message.chat.id, "Неверный рекламный код в ссылке.")
        elif start_param.lower().startswith("partner_"):
            partner_code = normalize_partner_code(start_param)
            if not partner_code or not get_partner_by_code(partner_code) or not get_partner_by_code(partner_code)[4]:
                invalid_partner_link = True
    remember_user_from_message(message, ref)
    if source_code:
        ensure_ad_source(source_code)
        remember_ad_source_for_user(user_id, source_code)
    if partner_code and not invalid_partner_link:
        activate_partner_window(user_id, partner_code)
    if invalid_partner_link:
        bot.send_message(message.chat.id, "Партнёрская ссылка недействительна или больше не активна.")
    show_main(message.chat.id, user_id, add_to_history=True)

@bot.message_handler(commands=["myid"])
def myid_handler(message):
    remember_user_from_message(message)
    bot.send_message(message.chat.id, f"Ваш Telegram ID: {message.from_user.id}")

@bot.message_handler(commands=["source"])
def source_handler(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        bot.send_message(
            message.chat.id,
            "Используйте так:\n/source КОД НАЗВАНИЕ\n\nНапример:\n/source tg_travel_moscow Путешествия Москва"
        )
        return

    code = normalize_source_code(parts[1])
    if not code:
        bot.send_message(message.chat.id, "Неверный код. Разрешены a-z, 0-9 и _, длина от 2 до 48 символов.")
        return

    requested_name = parts[2].strip() if len(parts) > 2 else None
    cursor.execute("SELECT name FROM ad_sources WHERE code=?", (code,))
    row = cursor.fetchone()
    existed = row is not None
    if existed:
        if requested_name:
            ensure_ad_source(code, requested_name)
            name = requested_name
        else:
            name = row[0] or code
    else:
        name = requested_name or code
        ensure_ad_source(code, name)

    action = "обновлён" if existed else "создан"
    bot.send_message(
        message.chat.id,
        f"✅ Рекламный источник {action}\n\n"
        f"Название: {name}\n"
        f"Код: {code}\n\n"
        f"Ссылка:\n{source_link(code)}"
    )

@bot.message_handler(commands=["source_stats"])
def source_stats_handler(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Используйте так:\n/source_stats КОД")
        return

    show_source_stats(message.chat.id, message.from_user.id, parts[1])

@bot.message_handler(commands=["partner_create"])
def partner_create_handler(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        bot.send_message(message.chat.id, "Используйте так:\n/partner_create КОД TELEGRAM_ID НАЗВАНИЕ")
        return
    code = normalize_partner_code(parts[1])
    if not code:
        bot.send_message(message.chat.id, "Неверный код партнёра. Разрешены a-z, 0-9 и _, от 2 до 48 символов.")
        return
    try:
        telegram_user_id = int(parts[2])
    except ValueError:
        bot.send_message(message.chat.id, "TELEGRAM_ID должен быть числом.")
        return
    if telegram_user_id <= 0:
        bot.send_message(message.chat.id, "TELEGRAM_ID должен быть положительным числом.")
        return
    name = parts[3].strip()
    if not name:
        bot.send_message(message.chat.id, "Название партнёра обязательно.")
        return

    cursor.execute("SELECT code FROM partners WHERE telegram_user_id=? AND code!=?", (telegram_user_id, code))
    conflict = cursor.fetchone()
    if conflict:
        bot.send_message(message.chat.id, f"Этот Telegram ID уже привязан к партнёру: {conflict[0]}")
        return

    now = int(time.time())
    cursor.execute("SELECT code FROM partners WHERE code=?", (code,))
    existed = cursor.fetchone() is not None
    try:
        if existed:
            cursor.execute(
                """
                UPDATE partners
                SET name=?, telegram_user_id=?, commission_rate=?, is_active=1
                WHERE code=?
                """,
                (name, telegram_user_id, DEFAULT_PARTNER_RATE, code)
            )
        else:
            cursor.execute(
                """
                INSERT INTO partners (code, name, telegram_user_id, commission_rate, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (code, name, telegram_user_id, DEFAULT_PARTNER_RATE, now)
            )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        bot.send_message(message.chat.id, "Не удалось сохранить партнёра: Telegram ID уже привязан к другому партнёру.")
        return
    action = "обновлён" if existed else "создан"
    bot.send_message(
        message.chat.id,
        f"✅ Партнёр {action}\n\n"
        f"Название: {name}\n"
        f"Код: {code}\n"
        f"Telegram ID: {telegram_user_id}\n"
        f"Ставка: {DEFAULT_PARTNER_RATE}%\n\n"
        f"Ссылка:\n{partner_link(code)}\n\n"
        f"Кабинет партнёра:\n/partner"
    )

@bot.message_handler(commands=["partner"])
def partner_handler(message):
    show_partner_cabinet(message.chat.id, message.from_user.id)

@bot.message_handler(commands=["partner_stats"])
def partner_stats_handler(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Используйте так:\n/partner_stats КОД")
        return
    show_partner_stats(message.chat.id, message.from_user.id, parts[1])

@bot.message_handler(commands=["partner_payout"])
def partner_payout_handler(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Используйте так:\n/partner_payout КОД")
        return
    code = normalize_partner_code(parts[1])
    partner = get_partner_by_code(code) if code else None
    if not partner:
        bot.send_message(message.chat.id, "Партнёр не найден.")
        return
    code, name, telegram_user_id, rate, is_active = partner
    amounts = get_partner_amounts(code)
    if amounts["available"] <= 0:
        bot.send_message(message.chat.id, "У партнёра нет суммы к выплате")
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Подтвердить выплату", callback_data=f"pp_ok_{code}"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="pp_cancel")
    )
    bot.send_message(
        message.chat.id,
        f"Партнёр: {name}\n"
        f"К выплате: {format_price(amounts['available'])} ₽\n\n"
        f"Подтвердите, что деньги уже переведены партнёру.",
        reply_markup=kb
    )

@bot.message_handler(commands=["sendqr"])
def sendqr_handler(message):
    global admin_send_qr_target, admin_send_qr_order_id

    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()

    if len(parts) not in (2, 3):
        bot.send_message(
            message.chat.id,
            "Используй так:\n/sendqr USER_ID ORDER_ID\n\n"
            "Например:\n/sendqr 888804373 125\n\n"
            "Старый формат тоже работает:\n/sendqr USER_ID"
        )
        return

    try:
        target_user_id = int(parts[1])
    except ValueError:
        bot.send_message(message.chat.id, "USER_ID должен быть числом.")
        return

    if len(parts) == 3:
        try:
            order_id = int(parts[2])
        except ValueError:
            bot.send_message(message.chat.id, "ORDER_ID должен быть числом.")
            return

        cursor.execute("SELECT id, status FROM orders WHERE id=? AND user_id=?", (order_id, target_user_id))
        row = cursor.fetchone()
        if not row:
            bot.send_message(message.chat.id, "Не нашёл такой заказ у указанного пользователя.")
            return
        if row[1] != "paid":
            bot.send_message(message.chat.id, "eSIM можно отправить только по оплаченному заказу.")
            return
    else:
        cursor.execute(
            """
            SELECT id
            FROM orders
            WHERE user_id=? AND status='paid' AND COALESCE(esim_sent_at, 0)=0
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_user_id,)
        )
        row = cursor.fetchone()
        if not row:
            bot.send_message(message.chat.id, "Не нашёл оплаченный заказ без отправленной eSIM для этого пользователя.")
            return
        order_id = row[0]

    admin_send_qr_target = target_user_id
    admin_send_qr_order_id = order_id

    bot.send_message(
        message.chat.id,
        f"✅ Режим отправки eSIM включен.\n\n"
        f"Клиент: {admin_send_qr_target}\n"
        f"Заказ: #{admin_send_qr_order_id}\n\n"
        f"Теперь отправь сюда ОДНО сообщение от поставщика:\n"
        f"— текст с QR-ссылкой\n"
        f"— фото QR\n"
        f"— фото с подписью\n"
        f"— инструкцию со ссылками\n"
        f"— документ или картинку\n\n"
        f"Я скопирую это сообщение клиенту полностью.\n\n"
        f"Чтобы отменить: /cancelqr"
    )


@bot.message_handler(commands=["cancelqr"])
def cancelqr_handler(message):
    global admin_send_qr_target, admin_send_qr_order_id

    if message.from_user.id != ADMIN_ID:
        return

    admin_send_qr_target = None
    admin_send_qr_order_id = None
    bot.send_message(message.chat.id, "❌ Отправка eSIM отменена.")


def is_admin_esim_message(message) -> bool:
    if message.from_user.id != ADMIN_ID:
        return False
    if admin_send_qr_target is None or admin_send_qr_order_id is None:
        return False
    if message.content_type == "text" and message.text and message.text.startswith("/"):
        return False
    return True


@bot.message_handler(
    func=is_admin_esim_message,
    content_types=["text", "photo", "document", "video", "animation", "audio", "voice"]
)
def admin_send_esim_message(message):
    global admin_send_qr_target, admin_send_qr_order_id

    target_user_id = admin_send_qr_target
    order_id = admin_send_qr_order_id

    try:
        bot.copy_message(
            chat_id=target_user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )
        bot.send_message(target_user_id, install_instruction_text())

        now = int(time.time())
        cursor.execute("SELECT COALESCE(esim_sent_at, 0) FROM orders WHERE id=? AND user_id=?", (order_id, target_user_id))
        row = cursor.fetchone()
        already_sent = bool(row and row[0])
        cursor.execute("UPDATE orders SET esim_sent_at=? WHERE id=? AND user_id=?", (now, order_id, target_user_id))
        conn.commit()
        if not already_sent:
            schedule_reminder(target_user_id, order_id, "install_2h", now + 2 * 60 * 60)

        bot.send_message(
            ADMIN_ID,
            f"✅ eSIM отправлена пользователю {target_user_id}\n"
            f"Заказ #{order_id}"
        )

        admin_send_qr_target = None
        admin_send_qr_order_id = None

    except Exception as e:
        bot.send_message(
            ADMIN_ID,
            f"❌ Не удалось отправить eSIM пользователю {target_user_id}.\n\n"
            f"Ошибка:\n{e}\n\n"
            f"Режим отправки не сброшен. Можно отправить сообщение ещё раз или отменить командой /cancelqr."
        )


@bot.message_handler(content_types=["text"])
def text_handler(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()

    remember_user_from_message(message)

    if text == "🏠 В начало":
        show_main(chat_id, user_id, add_to_history=True)
        return

    if text == "🔙 Назад":
        selection_mode.pop(user_id, None)
        state = go_back(user_id)
        render_from_state(chat_id, user_id, state)
        return

    if user_id in selection_mode:
        state = selection_mode[user_id]
        step = state.get("step")

        if step == "country":
            country = normalize_country_text(text)
            if not country:
                bot.send_message(chat_id, "Не нашёл страну. Попробуйте написать иначе: Турция, Египет, ОАЭ, Китай.")
                return
            ask_days(chat_id, user_id, country)
            return

        if step == "days":
            try:
                days = int(text)
            except ValueError:
                bot.send_message(chat_id, "Напишите только число дней, например: 7")
                return
            ask_usage(chat_id, user_id, state["country"], days)
            return

        if step == "usage":
            if text.startswith("🟢"):
                usage = "light"
            elif text.startswith("🟡"):
                usage = "medium"
            elif text.startswith("🔴"):
                usage = "heavy"
            else:
                bot.send_message(chat_id, "Выберите вариант кнопкой ниже.")
                return
            finish_selection(chat_id, user_id, state["country"], int(state["days"]), usage)
            return

    if text == "✈️ eSIM для путешествий":
        show_travel_home(chat_id, user_id, add_to_history=True)
        return

    if text == "⚡ Подобрать eSIM":
        start_selection(chat_id, user_id)
        return

    if is_legacy_russia_text(text):
        show_russia_discontinued(chat_id, user_id)
        return

    if text == "📘 Инструкции":
        show_instructions_menu(chat_id, user_id, add_to_history=True)
        return

    if text == "👤 Личный кабинет":
        show_cabinet(chat_id, user_id, add_to_history=True)
        return

    if text in ("📊 Статистика", "📊 Админ-статистика"):
        show_admin_stats(chat_id, user_id)
        return

    if text == "📦 Заказы":
        show_admin_orders(chat_id, user_id)
        return

    if text == "👥 Пользователи":
        show_admin_users(chat_id, user_id)
        return

    if text == "📣 Реклама":
        show_ad_stats(chat_id, user_id)
        return

    if text == "🤝 Партнёры":
        show_admin_partners(chat_id, user_id)
        return

    if text == "🤝 Кабинет партнёра":
        show_partner_cabinet(chat_id, user_id)
        return

    if text == "❓ Помощь":
        show_help(chat_id, user_id, add_to_history=True)
        return

    if text == "❓ Как это работает":
        show_how_it_works(chat_id, user_id, add_to_history=True)
        return

    if text == "🔥 Популярные страны":
        show_region(chat_id, user_id, "🔥 Популярные страны", add_to_history=True)
        return

    if text in REGIONS:
        show_region(chat_id, user_id, text, add_to_history=True)
        return

    if text == "🔎 Поиск страны":
        show_search(chat_id, user_id, add_to_history=True)
        return

    if text == "✈️ Инструкция для путешествий":
        show_travel_instruction(chat_id, user_id, add_to_history=True)
        return

    if text == "📸 Отправить чек":
        bot.send_message(chat_id, "Отправьте скрин чека одним сообщением как фото.", reply_markup=nav_keyboard())
        return

    selected_country = normalize_country_text(text)
    if selected_country:
        search_mode[user_id] = False
        show_country(chat_id, user_id, selected_country, add_to_history=True)
        return

    if search_mode.get(user_id):
        q = text.lower()
        matches = [country for country in COUNTRY_PRICES.keys() if q in country.lower()]
        if not matches:
            bot.send_message(chat_id, "Ничего не найдено. Попробуйте другое название страны.", reply_markup=nav_keyboard())
            return

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for country in matches[:20]:
            kb.add(country_label(country))
        kb.add("🔙 Назад", "🏠 В начало")
        bot.send_message(chat_id, "Результаты поиска:", reply_markup=kb)
        return

    if is_legacy_russia_text(text):
        show_russia_discontinued(chat_id, user_id)
        return

    if "—" in text and "₽" in text:
        displayed_price = parse_price_from_order_text(text)
        if displayed_price is None:
            bot.send_message(chat_id, "Не удалось определить цену.", reply_markup=main_keyboard(user_id))
            return

        country, tariff = parse_order_details(text)
        process_order_selection(chat_id, user_id, country, tariff, displayed_price)
        return

    bot.send_message(chat_id, "Не понял команду. Нажмите нужную кнопку 👇", reply_markup=main_keyboard(user_id))

@bot.message_handler(content_types=["photo"])
def photo_handler(message):
    global admin_send_qr_target, admin_send_qr_order_id
    user_id = message.from_user.id
    remember_user_from_message(message)

    if user_id == ADMIN_ID and admin_send_qr_target and admin_send_qr_order_id:
        target_user_id = admin_send_qr_target
        order_id = admin_send_qr_order_id
        try:
            bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            bot.send_message(target_user_id, install_instruction_text())
            now = int(time.time())
            cursor.execute("SELECT COALESCE(esim_sent_at, 0) FROM orders WHERE id=? AND user_id=?", (order_id, target_user_id))
            row = cursor.fetchone()
            already_sent = bool(row and row[0])
            cursor.execute("UPDATE orders SET esim_sent_at=? WHERE id=? AND user_id=?", (now, order_id, target_user_id))
            conn.commit()
            if not already_sent:
                schedule_reminder(target_user_id, order_id, "install_2h", now + 2 * 60 * 60)
            bot.send_message(ADMIN_ID, f"✅ eSIM отправлена пользователю {target_user_id}\nЗаказ #{order_id}")
            admin_send_qr_target = None
            admin_send_qr_order_id = None
            return
        except Exception as e:
            bot.send_message(
                ADMIN_ID,
                f"❌ Не удалось отправить eSIM пользователю {target_user_id}.\n\nОшибка:\n{e}"
            )
            return

    cursor.execute("""
        SELECT id, text, price, pay_amount, discount_used, country, tariff
        FROM orders
        WHERE user_id=? AND status='awaiting_receipt'
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))
    row = cursor.fetchone()

    if not row:
        bot.send_message(message.chat.id, "Не нашел заказ, который ждет чек. Сначала выберите тариф.", reply_markup=main_keyboard(user_id))
        return

    order_id, order_text, price, pay_amount, discount_used, country, tariff = row
    receipt_received_at = int(time.time())
    cursor.execute(
        "UPDATE orders SET status='pending_review', receipt_received_at=? WHERE id=?",
        (receipt_received_at, order_id)
    )
    conn.commit()
    cancel_reminders_by_type(order_id, ["payment_30m", "payment_24h"])
    schedule_reminder(user_id, order_id, "review_15m", receipt_received_at + 15 * 60)

    username = message.from_user.username
    first_name = message.from_user.first_name or "Без имени"
    user_text = f"@{username}" if username else f"{first_name} (ID: {user_id})"

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"ok_{order_id}_{user_id}_{pay_amount}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"no_{order_id}_{user_id}")
    )

    bot.send_message(
        ADMIN_ID,
        f"Детали чека по заказу #{order_id}\n"
        f"Покупатель: {format_user_for_admin(user_id)}\n"
        f"Страна: {country}\n"
        f"Тариф: {tariff}\n"
        f"Сумма: {price}₽\n"
        f"К оплате: {pay_amount}₽"
    )

    bot.send_photo(
        ADMIN_ID,
        message.photo[-1].file_id,
        caption=(
            f"🧾 Новый чек\n\n"
            f"Покупатель: {user_text}\n"
            f"ID: {user_id}\n"
            f"Заказ: {order_text}\n"
            f"Стоимость: {price}₽\n"
            f"Списано с баланса: {discount_used}₽\n"
            f"К оплате: {pay_amount}₽"
        ),
        reply_markup=kb
    )

    bot.send_message(message.chat.id, "Чек отправлен. Заказ принят в обработку.", reply_markup=main_keyboard(user_id))

@bot.callback_query_handler(func=lambda c: True)
def callback_handler(call):
    data = call.data

    if data.startswith("reminder_stop_"):
        order_id = int(data.replace("reminder_stop_", "", 1))
        user_id = call.from_user.id
        cursor.execute("SELECT id FROM orders WHERE id=? AND user_id=?", (order_id, user_id))
        if not cursor.fetchone():
            bot.answer_callback_query(call.id, "Это не ваш заказ.")
            return
        cursor.execute(
            "UPDATE reminder_jobs SET status='cancelled' WHERE order_id=? AND status='pending'",
            (order_id,)
        )
        conn.commit()
        bot.answer_callback_query(call.id, "Напоминания отключены")
        bot.send_message(user_id, "Напоминания по этому заказу отключены.")
        return

    if data.startswith("receipt_help_"):
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.from_user.id,
            "Отправьте скриншот чека сюда одним сообщением как фотографию.\n\n"
            "Бот автоматически привяжет его к вашему последнему заказу, ожидающему оплату."
        )
        return

    if data.startswith("install_ok_"):
        order_id = int(data.replace("install_ok_", "", 1))
        user_id = call.from_user.id
        cursor.execute("SELECT id FROM orders WHERE id=? AND user_id=?", (order_id, user_id))
        if not cursor.fetchone():
            bot.answer_callback_query(call.id, "Это не ваш заказ.")
            return
        cursor.execute("UPDATE orders SET install_confirmed=1 WHERE id=? AND user_id=?", (order_id, user_id))
        cancel_reminders_by_type(order_id, "install_2h")
        conn.commit()
        bot.answer_callback_query(call.id, "Готово")
        bot.send_message(
            user_id,
            "Отлично! eSIM готова к использованию.\n\n"
            "Перед поездкой не удаляйте её. По прилёте включите роуминг на eSIM и выберите её для мобильного интернета."
        )
        return

    if data.startswith("install_help_"):
        order_id = int(data.replace("install_help_", "", 1))
        user_id = call.from_user.id
        cursor.execute(
            """
            SELECT o.country, o.tariff, u.username, u.first_name
            FROM orders o
            LEFT JOIN users u ON u.user_id=o.user_id
            WHERE o.id=? AND o.user_id=?
            """,
            (order_id, user_id)
        )
        row = cursor.fetchone()
        if not row:
            bot.answer_callback_query(call.id, "Это не ваш заказ.")
            return
        country, tariff, username, first_name = row
        cancel_reminders_by_type(order_id, "install_2h")
        bot.answer_callback_query(call.id, "Поддержка уведомлена")
        bot.send_message(user_id, "Напишите нам — поможем разобраться: @F_Evdokimov")
        user_line = f"ID {user_id}"
        if username:
            user_line += f" | @{username}"
        if first_name:
            user_line += f" | {first_name}"
        bot.send_message(
            ADMIN_ID,
            f"🆘 Клиенту нужна помощь с установкой\n\n"
            f"Заказ #{order_id}\n"
            f"Пользователь: {user_line}\n"
            f"Страна: {country or 'Не указано'}\n"
            f"Тариф: {tariff or 'Не указано'}"
        )
        return

    if data.startswith("pp_req_"):
        code = data.replace("pp_req_", "", 1)
        partner = get_partner_by_user(call.from_user.id)
        if not partner or partner[0] != code:
            bot.answer_callback_query(call.id, "Партнёр не найден")
            return
        bot.answer_callback_query(call.id)
        send_partner_payout_request(call.from_user.id, call.from_user.id)
        return

    if data == "pp_cancel":
        bot.answer_callback_query(call.id, "Отменено")
        return

    if data.startswith("pp_ok_"):
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Недоступно")
            return
        code = data.replace("pp_ok_", "", 1)
        partner = get_partner_by_code(code)
        if not partner:
            bot.answer_callback_query(call.id, "Партнёр не найден")
            return
        code, name, telegram_user_id, rate, is_active = partner
        now = int(time.time())
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT COALESCE(SUM(commission_amount), 0) FROM partner_commissions WHERE partner_code=? AND status='available'",
                (code,)
            )
            amount = cursor.fetchone()[0]
            if amount <= 0:
                conn.rollback()
                bot.answer_callback_query(call.id, "Выплата уже обработана или доступная сумма равна нулю")
                return
            cursor.execute(
                "INSERT INTO partner_payouts (partner_code, amount, created_at, confirmed_by) VALUES (?, ?, ?, ?)",
                (code, amount, now, ADMIN_ID)
            )
            payout_id = cursor.lastrowid
            cursor.execute(
                """
                UPDATE partner_commissions
                SET status='paid', payout_id=?, paid_at=?
                WHERE partner_code=? AND status='available'
                """,
                (payout_id, now, code)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        amounts = get_partner_amounts(code)
        bot.answer_callback_query(call.id, "Выплата записана")
        bot.send_message(
            ADMIN_ID,
            f"✅ Выплата записана\n\n"
            f"Партнёр: {name}\n"
            f"Выплачено: {format_price(amount)} ₽\n"
            f"К выплате сейчас: {format_price(amounts['available'])} ₽"
        )
        if telegram_user_id:
            try:
                bot.send_message(
                    telegram_user_id,
                    f"✅ Вознаграждение выплачено\n\n"
                    f"Сумма: {format_price(amount)} ₽\n"
                    f"К выплате сейчас: {format_price(amounts['available'])} ₽\n"
                    f"Выплачено за всё время: {format_price(amounts['paid'])} ₽"
                )
            except Exception as exc:
                bot.send_message(ADMIN_ID, f"⚠️ Выплата сохранена, но уведомление партнёру не отправилось:\n{exc}")
        return

    if data.startswith("ok_"):
        _, order_id, user_id, pay_amount = data.split("_")
        order_id = int(order_id)
        user_id = int(user_id)

        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Недоступно")
            return

        ref_to_notify = None
        try:
            now = int(time.time())
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                SELECT user_id, status, country, tariff, price, pay_amount, partner_code, partner_rate,
                       partner_commission, ref_bonus_given
                FROM orders
                WHERE id=? AND user_id=?
                """,
                (order_id, user_id)
            )
            order_row = cursor.fetchone()
            if not order_row or order_row[1] != "pending_review":
                conn.rollback()
                bot.answer_callback_query(call.id, "Заказ уже обработан")
                return

            order_user_id, _status, country, tariff, price, pay_amount, partner_code, partner_rate, partner_commission, ref_bonus_given = order_row
            cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='paid' AND id!=?", (user_id, order_id))
            already_had_paid_orders = cursor.fetchone()[0] > 0

            cursor.execute("UPDATE orders SET status='paid', paid_at=? WHERE id=? AND user_id=? AND status='pending_review'", (now, order_id, user_id))
            if cursor.rowcount != 1:
                conn.rollback()
                bot.answer_callback_query(call.id, "Заказ уже обработан")
                return
            cursor.execute(
                "UPDATE reminder_jobs SET status='cancelled' WHERE order_id=? AND reminder_type IN ('payment_30m', 'payment_24h', 'review_15m') AND status IN ('pending', 'processing')",
                (order_id,)
            )

            if partner_code:
                if partner_commission > 0:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO partner_commissions
                            (order_id, partner_code, user_id, sale_amount, commission_rate, commission_amount, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'available', ?)
                        """,
                        (order_id, partner_code, user_id, pay_amount, partner_rate, partner_commission, now)
                    )
            else:
                cursor.execute("SELECT ref FROM users WHERE user_id=?", (user_id,))
                row = cursor.fetchone()
                ref = row[0] if row else None
                if ref and not already_had_paid_orders and not ref_bonus_given:
                    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (REF_BONUS, ref))
                    cursor.execute("UPDATE orders SET ref_bonus_given=1 WHERE id=?", (order_id,))
                    ref_to_notify = ref

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        if ref_to_notify:
            bot.send_message(
                ref_to_notify,
                f"🎉 Вам начислено {REF_BONUS}₽ за реферала.\n"
                f"Баланс можно использовать при следующей покупке."
            )

        bot.send_message(
            user_id,
            "✅ Заказ принят\n\n"
            "Мы проверили оплату и подготовим данные для установки eSIM: ссылку и/или QR-код с инструкцией.\n\n"
            "Обычно это занимает 5–15 минут.\n\n"
            "Если есть вопросы — напишите @F_Evdokimov",
            reply_markup=main_keyboard(user_id)
        )

        bot.send_message(
            ADMIN_ID,
            f"✅ Оплата подтверждена\n\n"
            f"Заказ #{order_id}\n"
            f"Покупатель: {format_user_for_admin(user_id)}\n"
            f"Страна: {country}\n"
            f"Тариф: {tariff}\n"
            f"Сумма: {price}₽\n"
            f"К оплате: {pay_amount}₽\n\n"
            f"Чтобы отправить eSIM этому пользователю, отправь команду:\n"
            f"/sendqr {user_id} {order_id}"
        )

        bot.answer_callback_query(call.id, "Оплата подтверждена")
        return

    if data.startswith("no_"):
        _, order_id, user_id = data.split("_")
        order_id = int(order_id)
        user_id = int(user_id)

        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Недоступно")
            return

        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT status, discount_used FROM orders WHERE id=? AND user_id=?",
                (order_id, user_id)
            )
            row = cursor.fetchone()
            if not row or row[0] != "pending_review":
                conn.rollback()
                bot.answer_callback_query(call.id, "Заказ уже обработан")
                return

            discount_used = row[1] or 0
            cursor.execute(
                "UPDATE orders SET status='cancel' WHERE id=? AND user_id=? AND status='pending_review'",
                (order_id, user_id)
            )
            if cursor.rowcount != 1:
                conn.rollback()
                bot.answer_callback_query(call.id, "Заказ уже обработан")
                return

            if discount_used > 0:
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (discount_used, user_id))

            cursor.execute(
                "UPDATE reminder_jobs SET status='cancelled' WHERE order_id=? AND status IN ('pending', 'processing')",
                (order_id,)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        bot.send_message(
            user_id,
            "❌ Оплата не подтверждена.\n\n"
            "Если это ошибка — напишите в поддержку: @F_Evdokimov",
            reply_markup=main_keyboard(user_id)
        )

        bot.answer_callback_query(call.id, "Заказ отклонен")
        return

threading.Thread(target=reminder_worker, daemon=True).start()
bot.polling(none_stop=True)
