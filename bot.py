import os
import sqlite3
from typing import Optional, Tuple, List, Dict

import telebot
from telebot import types

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not TOKEN:
    raise ValueError("TOKEN not found in environment variables")
if not ADMIN_ID_RAW:
    raise ValueError("ADMIN_ID not found in environment variables")

ADMIN_ID = int(ADMIN_ID_RAW)

bot = telebot.TeleBot(TOKEN)

# =========================
# DATABASE
# =========================
conn = sqlite3.connect("db.db", check_same_thread=False)
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

# =========================
# DATA
# =========================

# РФ тарифы от пользователя
RF_PLANS = {
    "1GB / 7 дней": 499,
    "3GB / 30 дней": 990,
    "5GB / 30 дней": 1290,
    "10GB / 30 дней": 2190,
    "20GB / 30 дней": 3590,
    "50GB / 90 дней": 7990,
    "100GB / 180 дней": 15990,
}

# Travel тарифы по зонам из таблицы
ZONE_PRICES = {
    0: {
        "1GB / 7 дней": 200,
        "3GB / 30 дней": 450,
        "5GB / 30 дней": 650,
        "10GB / 30 дней": 1100,
        "20GB / 30 дней": 2000,
        "50GB / 90 дней": 4600,
    },
    1: {
        "1GB / 7 дней": 250,
        "3GB / 30 дней": 550,
        "5GB / 30 дней": 800,
        "10GB / 30 дней": 1400,
        "20GB / 30 дней": 2500,
        "50GB / 90 дней": 6000,
    },
    2: {
        "1GB / 7 дней": 300,
        "3GB / 30 дней": 700,
        "5GB / 30 дней": 1000,
        "10GB / 30 дней": 1800,
        "20GB / 30 дней": 3000,
        "50GB / 90 дней": 7200,
    },
    3: {
        "1GB / 7 дней": 450,
        "3GB / 30 дней": 1100,
        "5GB / 30 дней": 2000,
        "10GB / 30 дней": 3500,
        "20GB / 30 дней": 6500,
        "50GB / 90 дней": 15000,
    },
    4: {
        "1GB / 7 дней": 700,
        "3GB / 30 дней": 2000,
        "5GB / 30 дней": 3000,
        "10GB / 30 дней": 6000,
        "20GB / 30 дней": 12000,
        "50GB / 90 дней": 28000,
    },
    5: {
        "1GB / 7 дней": 1100,
        "3GB / 30 дней": 2700,
        "5GB / 30 дней": 4000,
        "10GB / 30 дней": 7000,
        "20GB / 30 дней": 13000,
        "50GB / 90 дней": 30000,
    },
    6: {
        "1GB / 7 дней": 1300,
        "3GB / 30 дней": 3400,
        "5GB / 30 дней": 5000,
        "10GB / 30 дней": 8600,
        "20GB / 30 дней": 14800,
        "50GB / 90 дней": 35000,
    },
    7: {
        "1GB / 7 дней": 2600,
        "3GB / 30 дней": 6600,
        "5GB / 30 дней": 9600,
        "10GB / 30 дней": 18200,
        "20GB / 30 дней": 35200,
        "50GB / 90 дней": 84000,
    },
}

# Страны по зонам
ZONE_COUNTRIES = {
    0: [
        "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
        "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary",
        "Ireland", "Italy", "Kazakhstan", "Kyrgyzstan", "Latvia", "Liechtenstein",
        "Lithuania", "Luxembourg", "Malta", "Netherlands", "Norway", "Pakistan",
        "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Sweden",
        "Turkey", "Ukraine", "United Kingdom", "Uzbekistan"
    ],
    1: [
        "Albania", "Israel", "Malaysia", "Moldova", "Montenegro",
        "New Zealand", "Serbia", "Spain", "Switzerland", "USA Carib"
    ],
    2: [
        "Armenia", "Bangladesh", "Egypt", "Iceland", "Indonesia", "Kuwait",
        "Reunion", "Tajikistan", "Thailand", "Tunisia", "United States"
    ],
    3: [
        "Algeria", "Andorra", "Australia", "Azerbaijan", "Bahrain", "Belarus",
        "Bosnia & Herzegovina", "Brazil", "Cambodia", "Chile", "China", "Ecuador",
        "Faroe Islands", "Fiji", "Georgia", "Ghana", "Guernsey", "Hong Kong",
        "India", "Japan", "Kosovo", "Macao", "Morocco", "North Macedonia", "Oman",
        "Philippines", "Qatar", "Saudi Arabia", "Singapore", "South Africa",
        "South Korea", "Sri Lanka", "Taiwan", "United Arab Emirates",
        "Uruguay", "Vietnam"
    ],
    4: [
        "Afghanistan", "Argentina", "Canada", "Costa Rica", "Costa Rica carib",
        "Democratic Republic of Congo", "El Salvador", "French Guiana", "Gibraltar",
        "Iraq", "Laos", "Mexico", "Nigeria", "Peru",
        "Puerto Rico & US Virgin Islands carib", "Samoa", "Uganda", "Uruguay"
    ],
    5: [
        "Anguilla", "ANGUILLA carib", "Antigua and Barbuda",
        "ANTIGUA AND BARBUDA carib", "BAHAMAS carib", "Barbados", "BARBADOS carib",
        "Benin", "Bolivia", "BR VIRGIN ISLANDS carib", "British Virgin Islands",
        "CAYMAN ISLAND carib", "Cayman Islands", "Colombia", "Dominica",
        "DOMINICA carib", "Grenada", "GRENADA carib", "Jamaica", "JAMAICA carib",
        "Jersey", "Kenya", "Madagascar", "Montserrat", "MONTSERRAT carib",
        "Netherlands and French Antilles carib", "Panama", "PANAMA carib",
        "Paraguay", "Puerto Rico Carib", "Saint Kitts and Nevis",
        "SAINT KITTS AND NEVIS carib", "Saint Lucia", "SAINT LUCIA carib",
        "SAINT VINCENT AND THE GRENADINES carib", "Tanzania",
        "TURKS AND CAICOS ISLANDS carib", "Uganda", "Zambia"
    ],
    6: [
        "Dominican Republic", "Gabon", "Guadeloupe", "Guam", "Honduras",
        "Jordan", "Malawi", "Mauritius", "Mongolia", "Puerto Rico"
    ],
    7: [
        "Benin", "Côte d'Ivoire", "Curacao", "Dominican Republic", "Guatemala",
        "Guinea", "Guinea-Bissau", "Haiti", "Honduras", "Kiribati", "Liberia",
        "Maldives", "Martinique", "Monaco", "Nicaragua", "Papua New Guinea",
        "Rwanda", "Saint Vincent and the Grenadines", "Seychelles",
        "Sudan", "Tonga"
    ],
}

# Региональная витрина
REGIONS = {
    "🔥 Популярные": [
        "Turkey", "United Arab Emirates", "Thailand", "Georgia",
        "Kazakhstan", "Armenia", "Spain", "Italy"
    ],
    "🌍 Европа": [
        "Austria", "Belgium", "Croatia", "Czech Republic", "Denmark", "Estonia",
        "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy",
        "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Norway",
        "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain",
        "Sweden", "Switzerland", "United Kingdom"
    ],
    "🌏 Азия": [
        "Armenia", "Azerbaijan", "Bangladesh", "Cambodia", "China", "Georgia",
        "Hong Kong", "India", "Indonesia", "Israel", "Japan", "Kazakhstan",
        "Kuwait", "Macao", "Malaysia", "Oman", "Pakistan", "Philippines",
        "Qatar", "Saudi Arabia", "Singapore", "South Korea", "Sri Lanka",
        "Taiwan", "Tajikistan", "Thailand", "Turkey",
        "United Arab Emirates", "Uzbekistan", "Vietnam"
    ],
    "🌐 СНГ": [
        "Armenia", "Azerbaijan", "Georgia", "Kazakhstan",
        "Kyrgyzstan", "Moldova", "Tajikistan", "Ukraine", "Uzbekistan"
    ],
    "🌎 Америка": [
        "United States", "Canada", "Mexico", "Brazil", "Argentina", "Chile",
        "Colombia", "Ecuador", "Peru", "Panama", "Paraguay", "Uruguay",
        "Costa Rica", "El Salvador"
    ],
    "🌴 Карибы": [
        "Anguilla", "Antigua and Barbuda", "Bahamas carib", "Barbados",
        "British Virgin Islands", "Cayman Islands", "Dominica", "Grenada",
        "Jamaica", "Puerto Rico Carib", "Saint Kitts and Nevis",
        "Saint Lucia", "Turks and Caicos Islands carib"
    ],
    "🌍 Африка": [
        "Algeria", "Benin", "Côte d'Ivoire", "Egypt", "Gabon", "Ghana",
        "Guinea", "Guinea-Bissau", "Kenya", "Liberia", "Madagascar",
        "Malawi", "Mauritius", "Morocco", "Nigeria", "Reunion",
        "Rwanda", "Seychelles", "South Africa", "Sudan", "Tunisia",
        "Tanzania", "Uganda", "Zambia"
    ],
    "🌏 Другое": [
        "Australia", "New Zealand", "Fiji", "Samoa", "Guam",
        "Maldives", "Mongolia", "Papua New Guinea", "Tonga"
    ],
}

# Emoji mapping for nicer display
EMOJI = {
    "Turkey": "🇹🇷", "United Arab Emirates": "🇦🇪", "Thailand": "🇹🇭",
    "Georgia": "🇬🇪", "Kazakhstan": "🇰🇿", "Armenia": "🇦🇲", "Spain": "🇪🇸",
    "Italy": "🇮🇹", "France": "🇫🇷", "Germany": "🇩🇪", "United States": "🇺🇸",
    "Canada": "🇨🇦", "Mexico": "🇲🇽", "Japan": "🇯🇵", "China": "🇨🇳",
    "India": "🇮🇳", "Indonesia": "🇮🇩", "Australia": "🇦🇺",
    "United Kingdom": "🇬🇧", "Portugal": "🇵🇹", "Netherlands": "🇳🇱",
    "Greece": "🇬🇷", "Austria": "🇦🇹", "Switzerland": "🇨🇭",
    "Croatia": "🇭🇷", "Poland": "🇵🇱", "Brazil": "🇧🇷", "Argentina": "🇦🇷",
    "Chile": "🇨🇱", "South Korea": "🇰🇷", "Singapore": "🇸🇬",
    "Malaysia": "🇲🇾", "Saudi Arabia": "🇸🇦", "Qatar": "🇶🇦",
    "Egypt": "🇪🇬", "Tunisia": "🇹🇳", "South Africa": "🇿🇦",
    "New Zealand": "🇳🇿", "Israel": "🇮🇱", "Ukraine": "🇺🇦",
    "Azerbaijan": "🇦🇿", "Kyrgyzstan": "🇰🇬", "Tajikistan": "🇹🇯",
    "Uzbekistan": "🇺🇿", "Romania": "🇷🇴", "Ireland": "🇮🇪",
    "Belgium": "🇧🇪", "Czech Republic": "🇨🇿", "Denmark": "🇩🇰",
    "Estonia": "🇪🇪", "Finland": "🇫🇮", "Hungary": "🇭🇺",
    "Latvia": "🇱🇻", "Lithuania": "🇱🇹", "Luxembourg": "🇱🇺",
    "Malta": "🇲🇹", "Norway": "🇳🇴", "Slovakia": "🇸🇰",
    "Slovenia": "🇸🇮", "Sweden": "🇸🇪", "Bulgaria": "🇧🇬",
    "Cyprus": "🇨🇾", "Montenegro": "🇲🇪", "Serbia": "🇷🇸",
    "Moldova": "🇲🇩", "Albania": "🇦🇱", "Iceland": "🇮🇸",
    "Pakistan": "🇵🇰", "Bangladesh": "🇧🇩", "Sri Lanka": "🇱🇰",
    "Philippines": "🇵🇭", "Vietnam": "🇻🇳", "Cambodia": "🇰🇭",
    "Morocco": "🇲🇦", "Kenya": "🇰🇪",
}

def pretty_country(name: str) -> str:
    return f"{EMOJI.get(name, '🌐')} {name}"

COUNTRY_TO_ZONE: Dict[str, int] = {}
for zone_id, country_list in ZONE_COUNTRIES.items():
    for country_name in country_list:
        COUNTRY_TO_ZONE[country_name] = zone_id

TRAVEL_COUNTRIES = sorted(COUNTRY_TO_ZONE.keys())

REF_PERCENT = 10

# =========================
# STATE
# =========================
user_nav: Dict[int, List[Tuple[str, Optional[str]]]] = {}
user_mode: Dict[int, Optional[str]] = {}         # None / "search"
admin_qr_target: Dict[int, int] = {}             # admin_id -> target user_id

# =========================
# HELPERS
# =========================
def ensure_user(user_id: int, ref: Optional[int] = None) -> None:
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cursor.fetchone():
        return
    if ref == user_id:
        ref = None
    cursor.execute(
        "INSERT INTO users (user_id, balance, ref) VALUES (?, ?, ?)",
        (user_id, 0, ref)
    )
    conn.commit()

def set_nav(user_id: int, screen: str, payload: Optional[str] = None, reset: bool = False) -> None:
    if reset:
        user_nav[user_id] = [(screen, payload)]
        return
    stack = user_nav.setdefault(user_id, [])
    if not stack or stack[-1] != (screen, payload):
        stack.append((screen, payload))

def current_nav(user_id: int) -> Tuple[str, Optional[str]]:
    stack = user_nav.get(user_id, [])
    if not stack:
        return ("main", None)
    return stack[-1]

def go_back(user_id: int) -> Tuple[str, Optional[str]]:
    stack = user_nav.setdefault(user_id, [("main", None)])
    if len(stack) > 1:
        stack.pop()
    return stack[-1]

def main_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🇷🇺 Интернет РФ", "🌍 Путешествия")
    kb.add("👤 Личный кабинет", "❓ Помощь")
    return kb

def nav_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔙 Назад", "🏠 В начало")
    return kb

def format_price(value: int) -> str:
    return f"{value}₽"

def get_last_waiting_order(user_id: int) -> Optional[Tuple[int, str, int, str]]:
    cursor.execute("""
        SELECT id, text, price, status
        FROM orders
        WHERE user_id=? AND status IN ('awaiting_receipt', 'pending_review')
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))
    return cursor.fetchone()

def render_screen(chat_id: int, user_id: int, screen: str, payload: Optional[str] = None, push: bool = True) -> None:
    if screen == "main":
        render_main(chat_id, user_id, push=push)
    elif screen == "travel_home":
        render_travel_home(chat_id, user_id, push=push)
    elif screen == "region":
        render_region(chat_id, user_id, payload or "", push=push)
    elif screen == "country":
        render_country(chat_id, user_id, payload or "", push=push)
    elif screen == "search_prompt":
        render_search_prompt(chat_id, user_id, push=push)
    elif screen == "search_results":
        render_search_results(chat_id, user_id, payload or "", push=push)
    elif screen == "rf":
        render_rf(chat_id, user_id, push=push)
    elif screen == "cabinet":
        render_cabinet(chat_id, user_id, push=push)
    elif screen == "help":
        render_help(chat_id, user_id, push=push)
    elif screen == "rf_instruction":
        render_rf_instruction(chat_id, user_id, push=push)
    elif screen == "travel_instruction":
        render_travel_instruction(chat_id, user_id, push=push)
    else:
        render_main(chat_id, user_id, push=push)

# =========================
# RENDERS
# =========================
def render_main(chat_id: int, user_id: int, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "main", None, reset=True)

    text = (
        "🚀 esimlime\n\n"
        "Здесь можно купить eSIM:\n"
        "• для путешествий\n"
        "• для пользования в России без VPN\n\n"
        "Выберите раздел 👇"
    )
    bot.send_message(chat_id, text, reply_markup=main_menu())

def render_travel_home(chat_id: int, user_id: int, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "travel_home")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔥 Популярные")
    kb.add("🌍 Европа", "🌏 Азия")
    kb.add("🌐 СНГ", "🌎 Америка")
    kb.add("🌴 Карибы", "🌍 Африка")
    kb.add("🌏 Другое", "🔎 Поиск страны")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        "🌍 Путешествия\n\nВыберите раздел:",
        reply_markup=kb
    )

def render_region(chat_id: int, user_id: int, region: str, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "region", region)

    countries = REGIONS.get(region, [])
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for country in countries:
        kb.add(pretty_country(country))
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, f"{region}\n\nВыберите страну:", reply_markup=kb)

def render_country(chat_id: int, user_id: int, country: str, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "country", country)

    zone = COUNTRY_TO_ZONE.get(country)
    if zone is None:
        bot.send_message(chat_id, "Страна пока недоступна.", reply_markup=nav_menu())
        return

    prices = ZONE_PRICES[zone]
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for plan_name, price in prices.items():
        kb.add(f"{pretty_country(country)} | {plan_name} — {format_price(price)}")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        f"{pretty_country(country)}\n\nВыберите тариф:",
        reply_markup=kb
    )

def render_search_prompt(chat_id: int, user_id: int, push: bool = True) -> None:
    user_mode[user_id] = "search"
    if push:
        set_nav(user_id, "search_prompt")

    bot.send_message(
        chat_id,
        "🔎 Напишите страну, например:\nTurkey\nThailand\nItaly\nUnited States",
        reply_markup=nav_menu()
    )

def render_search_results(chat_id: int, user_id: int, query: str, push: bool = True) -> None:
    user_mode[user_id] = "search"
    if push:
        set_nav(user_id, "search_results", query)

    q = query.lower().strip()
    results = [c for c in TRAVEL_COUNTRIES if q in c.lower()]

    if not results:
        bot.send_message(chat_id, "Ничего не найдено. Попробуйте другое название страны.", reply_markup=nav_menu())
        return

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for country in results[:20]:
        kb.add(pretty_country(country))
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, "Результаты поиска:", reply_markup=kb)

def render_rf(chat_id: int, user_id: int, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "rf")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for plan_name, price in RF_PLANS.items():
        kb.add(f"{plan_name} — {format_price(price)}")
    kb.add("📱 Инструкция для России")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, "🇷🇺 Тарифы для России:", reply_markup=kb)

def render_cabinet(chat_id: int, user_id: int, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "cabinet")

    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0

    cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='paid'", (user_id,))
    paid_orders = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE ref=?", (user_id,))
    referrals = cursor.fetchone()[0]

    cursor.execute("""
        SELECT text
        FROM orders
        WHERE user_id=? AND status='paid'
        ORDER BY id DESC
        LIMIT 5
    """, (user_id,))
    last_orders = [r[0] for r in cursor.fetchall()]

    ref_link = f"https://t.me/esimlimebot?start={user_id}"

    text = (
        "👤 Личный кабинет\n\n"
        f"Баланс: {balance}₽\n"
        f"Куплено eSIM: {paid_orders}\n"
        f"Рефералов: {referrals}\n\n"
        f"Реферальная ссылка:\n{ref_link}\n"
    )

    if last_orders:
        text += "\nПоследние покупки:\n" + "\n".join([f"• {o}" for o in last_orders])

    bot.send_message(chat_id, text, reply_markup=nav_menu())

def render_help(chat_id: int, user_id: int, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "help")

    text = (
        "❓ Помощь\n\n"
        "По всем вопросам: @F_Evdokimov\n\n"
        "Если не получается установить eSIM или нужна помощь с покупкой — напишите."
    )
    bot.send_message(chat_id, text, reply_markup=nav_menu())

def render_rf_instruction(chat_id: int, user_id: int, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "rf_instruction")

    text = (
        "📱 Инструкция по установке eSIM для России\n\n"
        "1. Проверьте совместимость телефона.\n"
        "Наберите команду *#06#\n"
        "Если в списке есть строка EID — телефон поддерживает eSIM.\n\n"
        "2. Выберите и оплатите тариф.\n\n"
        "3. Получите QR-код.\n\n"
        "4. Установите eSIM.\n"
        "Откройте камеру, отсканируйте QR-код — профиль активируется автоматически.\n\n"
        "5. Если через камеру не активировалось, попробуйте отсканировать с экрана другого устройства.\n\n"
        "6. Включите роуминг на eSIM и переключите передачу данных на eSIM.\n"
        "Активация может занять до 24 часов, но обычно происходит быстрее.\n\n"
        "Если трафик закончится — напишите мне, подключим еще."
    )
    bot.send_message(chat_id, text, reply_markup=nav_menu())

def render_travel_instruction(chat_id: int, user_id: int, push: bool = True) -> None:
    user_mode[user_id] = None
    if push:
        set_nav(user_id, "travel_instruction")

    text = (
        "✈️ Инструкция для путешествий\n\n"
        "1. Выберите страну и тариф.\n"
        "2. Оплатите заказ.\n"
        "3. Отправьте чек скриншотом.\n"
        "4. Дождитесь подтверждения.\n"
        "5. Получите QR-код и инструкцию.\n"
        "6. Установите eSIM и включите роуминг на этой eSIM.\n"
        "7. Переключите интернет на eSIM.\n\n"
        "Если нужна помощь — напишите @F_Evdokimov"
    )
    bot.send_message(chat_id, text, reply_markup=nav_menu())

# =========================
# ORDER FLOW
# =========================
def create_order_and_request_payment(chat_id: int, user_id: int, order_text: str, price: int) -> None:
    cursor.execute(
        "INSERT INTO orders (user_id, text, price, status) VALUES (?, ?, ?, ?)",
        (user_id, order_text, price, "awaiting_receipt")
    )
    conn.commit()

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📸 Отправить чек")
    kb.add("🔙 Назад", "🏠 В начало")

    text = (
        f"🧾 Ваш заказ:\n{order_text}\n\n"
        "Оплата по СБП:\n"
        "Номер: 89870005569\n"
        "Банк: Т-Банк\n"
        "Получатель: Федор Е.\n\n"
        "После оплаты нажмите «📸 Отправить чек» и отправьте скриншот одним сообщением."
    )
    bot.send_message(chat_id, text, reply_markup=kb)

def parse_order_text(text: str) -> Optional[Tuple[str, int]]:
    if "—" not in text:
        return None
    try:
        price = int(text.split("—")[1].replace("₽", "").strip())
        return text, price
    except Exception:
        return None

# =========================
# START
# =========================
@bot.message_handler(commands=["start"])
def start_cmd(message):
    uid = message.from_user.id
    ref = None

    parts = message.text.split()
    if len(parts) > 1:
        try:
            ref = int(parts[1])
        except ValueError:
            ref = None

    ensure_user(uid, ref)
    render_main(message.chat.id, uid, push=True)

# =========================
# ADMIN COMMANDS
# =========================
@bot.message_handler(commands=["sendqr"])
def send_qr_command(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "Используй так: /sendqr USER_ID")
        return

    try:
        target_user_id = int(parts[1])
    except ValueError:
        bot.send_message(message.chat.id, "USER_ID должен быть числом.")
        return

    admin_qr_target[message.from_user.id] = target_user_id
    bot.send_message(
        message.chat.id,
        f"Теперь отправь ОДНО фото QR-кода, и я перешлю его пользователю {target_user_id} с инструкцией."
    )

# =========================
# TEXT HANDLER
# =========================
@bot.message_handler(func=lambda m: m.content_type == "text")
def text_handler(message):
    uid = message.from_user.id
    text = message.text.strip()

    ensure_user(uid)

    # Глобальная навигация
    if text == "🏠 В начало":
        render_main(message.chat.id, uid, push=True)
        return

    if text == "🔙 Назад":
        screen, payload = go_back(uid)
        render_screen(message.chat.id, uid, screen, payload, push=False)
        return

    # Админ в режиме ожидания QR, но отправил текст
    if uid == ADMIN_ID and uid in admin_qr_target:
        bot.send_message(message.chat.id, "Сейчас жду от тебя фото QR-кода. Либо отправь фото, либо сначала закончи текущую отправку.")
        return

    # Поиск страны
    if user_mode.get(uid) == "search":
        if text:
            render_search_results(message.chat.id, uid, text, push=True)
            return

    # Главное меню
    if text == "🇷🇺 Интернет РФ":
        render_rf(message.chat.id, uid, push=True)
        return

    if text == "🌍 Путешествия":
        render_travel_home(message.chat.id, uid, push=True)
        return

    if text == "👤 Личный кабинет":
        render_cabinet(message.chat.id, uid, push=True)
        return

    if text == "❓ Помощь":
        render_help(message.chat.id, uid, push=True)
        return

    # Витрина
    if text == "🔥 Популярные":
        render_region(message.chat.id, uid, "🔥 Популярные", push=True)
        return

    if text in REGIONS:
        render_region(message.chat.id, uid, text, push=True)
        return

    if text == "🔎 Поиск страны":
        render_search_prompt(message.chat.id, uid, push=True)
        return

    if text == "📱 Инструкция для России":
        render_rf_instruction(message.chat.id, uid, push=True)
        return

    if text == "✈️ Инструкция для путешествий":
        render_travel_instruction(message.chat.id, uid, push=True)
        return

    # Выбор страны
    normalized_country = None
    for c in TRAVEL_COUNTRIES:
        if text == pretty_country(c) or text == c:
            normalized_country = c
            break

    if normalized_country:
        render_country(message.chat.id, uid, normalized_country, push=True)
        return

    # Выбор тарифа
    parsed = parse_order_text(text)
    if parsed:
        order_text, price = parsed
        create_order_and_request_payment(message.chat.id, uid, order_text, price)
        return

    # Просьба отправить чек
    if text == "📸 Отправить чек":
        bot.send_message(
            message.chat.id,
            "Отправьте скрин чека ОДНИМ сообщением как фото.",
            reply_markup=nav_menu()
        )
        return

    bot.send_message(
        message.chat.id,
        "Не понял команду. Нажмите нужную кнопку 👇",
        reply_markup=main_menu()
    )

# =========================
# PHOTO HANDLER
# =========================
@bot.message_handler(content_types=["photo"])
def photo_handler(message):
    uid = message.from_user.id

    # 1) Админ отправляет QR пользователю
    if uid == ADMIN_ID and uid in admin_qr_target:
        target_user_id = admin_qr_target.pop(uid)

        instruction = (
            "✅ Ваш QR-код eSIM\n\n"
            "1. Откройте камеру и отсканируйте QR-код.\n"
            "2. Если не сработает — попробуйте отсканировать с другого устройства.\n"
            "3. Включите роуминг на eSIM.\n"
            "4. Переключите интернет на eSIM.\n\n"
            "Если нужна помощь — @F_Evdokimov"
        )

        bot.send_photo(
            target_user_id,
            message.photo[-1].file_id,
            caption=instruction
        )
        bot.send_message(uid, f"QR отправлен пользователю {target_user_id}")
        return

    # 2) Пользователь отправляет чек
    order = get_last_waiting_order(uid)
    if not order:
        bot.send_message(
            message.chat.id,
            "Не нашел заказ, который ждет чек. Сначала выберите тариф.",
            reply_markup=main_menu()
        )
        return

    order_id, order_text, price, status = order

    cursor.execute(
        "UPDATE orders SET status='pending_review' WHERE id=?",
        (order_id,)
    )
    conn.commit()

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"ok_{order_id}_{uid}_{price}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"no_{order_id}_{uid}")
    )

    bot.send_photo(
        ADMIN_ID,
        message.photo[-1].file_id,
        caption=f"Чек от пользователя {uid}\nЗаказ: {order_text}",
        reply_markup=kb
    )

    bot.send_message(
        message.chat.id,
        "Чек отправлен. Заказ принят в обработку.",
        reply_markup=main_menu()
    )

# =========================
# CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda c: True)
def callback_handler(call):
    data = call.data

    if data.startswith("ok_"):
        _, order_id, user_id, price = data.split("_")
        order_id = int(order_id)
        user_id = int(user_id)
        price = int(price)

        cursor.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))

        cursor.execute("SELECT ref FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        ref = row[0] if row else None

        if ref:
            bonus = int(price * REF_PERCENT / 100)
            cursor.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (bonus, ref)
            )

        conn.commit()

        bot.send_message(
            user_id,
            "✅ Заказ принят, ожидайте QR с инструкцией",
            reply_markup=main_menu()
        )
        bot.send_message(
            ADMIN_ID,
            f"Оплата подтверждена.\nЧтобы отправить QR, напиши:\n/sendqr {user_id}"
        )
        bot.answer_callback_query(call.id, "Оплата подтверждена")
        return

    if data.startswith("no_"):
        _, order_id, user_id = data.split("_")
        order_id = int(order_id)
        user_id = int(user_id)

        cursor.execute("UPDATE orders SET status='cancel'",)
        cursor.execute("UPDATE orders SET status='cancel' WHERE id=?", (order_id,))
        conn.commit()

        bot.send_message(
            user_id,
            "❌ Оплата не подтверждена.\nЕсли это ошибка — напишите в поддержку: @F_Evdokimov",
            reply_markup=main_menu()
        )
        bot.answer_callback_query(call.id, "Заказ отклонен")
        return

# =========================
# POLLING
# =========================
bot.polling(none_stop=True)
