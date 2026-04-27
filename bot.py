import os
import sqlite3
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

def add_column_if_not_exists(table: str, column: str, definition: str):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()

add_column_if_not_exists("orders", "pay_amount", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "discount_used", "INTEGER DEFAULT 0")
add_column_if_not_exists("orders", "ref_bonus_given", "INTEGER DEFAULT 0")

REF_BONUS = 100

RF_PLANS = {
    "1GB / 7 дней": 499,
    "3GB / 30 дней": 990,
    "5GB / 30 дней": 1290,
    "10GB / 30 дней": 2190,
    "20GB / 30 дней": 3590,
    "50GB / 90 дней": 7990,
    "100GB / 180 дней": 15990,
}

ZONE_PRICES = {
    0: {"1GB / 7 дней": 200, "3GB / 30 дней": 450, "5GB / 30 дней": 650, "10GB / 30 дней": 1100, "20GB / 30 дней": 2000, "50GB / 90 дней": 4600},
    1: {"1GB / 7 дней": 250, "3GB / 30 дней": 550, "5GB / 30 дней": 800, "10GB / 30 дней": 1400, "20GB / 30 дней": 2500, "50GB / 90 дней": 6000},
    2: {"1GB / 7 дней": 300, "3GB / 30 дней": 700, "5GB / 30 дней": 1000, "10GB / 30 дней": 1800, "20GB / 30 дней": 3000, "50GB / 90 дней": 7200},
    3: {"1GB / 7 дней": 450, "3GB / 30 дней": 1100, "5GB / 30 дней": 2000, "10GB / 30 дней": 3500, "20GB / 30 дней": 6500, "50GB / 90 дней": 15000},
    4: {"1GB / 7 дней": 700, "3GB / 30 дней": 2000, "5GB / 30 дней": 3000, "10GB / 30 дней": 6000, "20GB / 30 дней": 12000, "50GB / 90 дней": 28000},
    5: {"1GB / 7 дней": 1100, "3GB / 30 дней": 2700, "5GB / 30 дней": 4000, "10GB / 30 дней": 7000, "20GB / 30 дней": 13000, "50GB / 90 дней": 30000},
    6: {"1GB / 7 дней": 1300, "3GB / 30 дней": 3400, "5GB / 30 дней": 5000, "10GB / 30 дней": 8600, "20GB / 30 дней": 14800, "50GB / 90 дней": 35000},
    7: {"1GB / 7 дней": 2600, "3GB / 30 дней": 6600, "5GB / 30 дней": 9600, "10GB / 30 дней": 18200, "20GB / 30 дней": 35200, "50GB / 90 дней": 84000},
}

ZONE_COUNTRIES = {
    0: ["Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Kazakhstan", "Kyrgyzstan", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Norway", "Pakistan", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Sweden", "Turkey", "Ukraine", "United Kingdom", "Uzbekistan"],
    1: ["Albania", "Israel", "Malaysia", "Moldova", "Montenegro", "New Zealand", "Serbia", "Spain", "Switzerland", "USA Carib"],
    2: ["Armenia", "Bangladesh", "Egypt", "Iceland", "Indonesia", "Kuwait", "Reunion", "Tajikistan", "Thailand", "Tunisia", "United States"],
    3: ["Algeria", "Andorra", "Australia", "Azerbaijan", "Bahrain", "Belarus", "Bosnia & Herzegovina", "Brazil", "Cambodia", "Chile", "China", "Ecuador", "Faroe Islands", "Fiji", "Georgia", "Ghana", "Guernsey", "Hong Kong", "India", "Japan", "Kosovo", "Macao", "Morocco", "North Macedonia", "Oman", "Philippines", "Qatar", "Saudi Arabia", "Singapore", "South Africa", "South Korea", "Sri Lanka", "Taiwan", "United Arab Emirates", "Uruguay", "Vietnam"],
    4: ["Afghanistan", "Argentina", "Canada", "Costa Rica", "Costa Rica carib", "Democratic Republic of Congo", "El Salvador", "French Guiana", "Gibraltar", "Iraq", "Laos", "Mexico", "Nigeria", "Peru", "Puerto Rico & US Virgin Islands carib", "Samoa", "Uganda", "Uruguay"],
    5: ["Anguilla", "ANGUILLA carib", "Antigua and Barbuda", "ANTIGUA AND BARBUDA carib", "BAHAMAS carib", "Barbados", "BARBADOS carib", "Benin", "Bolivia", "BR VIRGIN ISLANDS carib", "British Virgin Islands", "CAYMAN ISLAND carib", "Cayman Islands", "Colombia", "Dominica", "DOMINICA carib", "Grenada", "GRENADA carib", "Jamaica", "JAMAICA carib", "Jersey", "Kenya", "Madagascar", "Montserrat", "MONTSERRAT carib", "Netherlands and French Antilles carib", "Panama", "PANAMA carib", "Paraguay", "Puerto Rico Carib", "Saint Kitts and Nevis", "SAINT KITTS AND NEVIS carib", "Saint Lucia", "SAINT LUCIA carib", "SAINT VINCENT AND THE GRENADINES carib", "Tanzania", "TURKS AND CAICOS ISLANDS carib", "Uganda", "Zambia"],
    6: ["Dominican Republic", "Gabon", "Guadeloupe", "Guam", "Honduras", "Jordan", "Malawi", "Mauritius", "Mongolia", "Puerto Rico"],
    7: ["Benin", "Côte d'Ivoire", "Curacao", "Dominican Republic", "Guatemala", "Guinea", "Guinea-Bissau", "Haiti", "Honduras", "Kiribati", "Liberia", "Maldives", "Martinique", "Monaco", "Nicaragua", "Papua New Guinea", "Rwanda", "Saint Vincent and the Grenadines", "Seychelles", "Sudan", "Tonga"],
}

COUNTRY_TO_ZONE = {}
for zone, countries in ZONE_COUNTRIES.items():
    for c in countries:
        COUNTRY_TO_ZONE[c] = zone

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
}

history: Dict[int, List[Tuple[str, Optional[str]]]] = {}
search_mode: Dict[int, bool] = {}
selection_mode: Dict[int, Dict[str, str]] = {}
admin_send_qr_target: Optional[int] = None

def ensure_user(user_id: int, ref: Optional[int] = None) -> None:
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cursor.fetchone():
        return
    if ref == user_id:
        ref = None
    cursor.execute("INSERT INTO users (user_id, balance, ref) VALUES (?, ?, ?)", (user_id, 0, ref))
    conn.commit()

def country_label(country: str) -> str:
    return f"{EMOJI.get(country, '🌐')} {country}"

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
    kb.add("⚡ Подобрать eSIM", "🇷🇺 eSIM для России")
    kb.add("📘 Инструкции")
    kb.add("👤 Личный кабинет", "❓ Помощь")
    if user_id == ADMIN_ID:
        kb.add("📊 Админ-статистика")
    return kb

def parse_price_from_order_text(text: str) -> Optional[int]:
    try:
        return int(text.split("—")[1].replace("₽", "").strip())
    except Exception:
        return None

def normalize_country_text(text: str) -> Optional[str]:
    clean = text.strip()
    lowered = clean.lower()
    if lowered in RU_COUNTRIES:
        return RU_COUNTRIES[lowered]
    for country in COUNTRY_TO_ZONE.keys():
        if clean == country or clean == country_label(country):
            return country
    return None

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
    zone = COUNTRY_TO_ZONE.get(country)
    if zone is None:
        return "10GB / 30 дней"

    prices = ZONE_PRICES[zone]
    available = list(prices.keys())

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
        kb.add(country_label(country))
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, f"{region}\n\nВыберите страну:", reply_markup=kb)

def show_country(chat_id: int, user_id: int, country: str, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "country", country)

    zone = COUNTRY_TO_ZONE.get(country)
    if zone is None:
        bot.send_message(chat_id, "Страна пока недоступна.", reply_markup=nav_keyboard())
        return

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for plan_name, price in ZONE_PRICES[zone].items():
        kb.add(f"{country_label(country)} | {plan_name} — {price}₽")
    kb.add("❓ Как это работает")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        f"{country_label(country)}\n\n"
        "Интернет для поездки без роуминга и поиска местной SIM-карты.\n\n"
        "✔ Подходит для карт, такси, мессенджеров и соцсетей\n"
        "✔ Не нужно менять основную SIM-карту\n"
        "✔ Можно установить заранее\n\n"
        "👇 Выберите тариф",
        reply_markup=kb
    )

def show_rf(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "rf")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for plan_name, price in RF_PLANS.items():
        kb.add(f"{plan_name} — {price}₽")
    kb.add("❓ Как это работает")
    kb.add("📱 Инструкция для России")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        "🇷🇺 eSIM для России\n\n"
        "Решение для интернета в России без VPN.\n\n"
        "👇 Выберите подходящий тариф",
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
        "4. Получаете QR-код\n"
        "5. Сканируете QR-код камерой телефона\n"
        "6. Включаете eSIM для передачи данных\n\n"
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
        SELECT ref, COUNT(*) as cnt
        FROM users
        WHERE ref IS NOT NULL
        GROUP BY ref
        ORDER BY cnt DESC
        LIMIT 5
    """)
    top_refs = cursor.fetchall()

    top_text = ""
    if top_refs:
        for i, (ref_id, cnt) in enumerate(top_refs, start=1):
            top_text += f"{i}. ID {ref_id} — {cnt} реф.\n"
    else:
        top_text = "Пока нет рефералов\n"

    bot.send_message(
        chat_id,
        "📊 Админ-статистика\n\n"
        f"Пользователей всего: {total_users}\n\n"
        f"Заказов всего: {total_orders}\n"
        f"Оплачено: {paid_orders}\n"
        f"Ожидают чек: {awaiting_orders}\n"
        f"На проверке: {pending_orders}\n"
        f"Отменено: {cancelled_orders}\n\n"
        f"Выручка: {revenue}₽\n"
        f"Списано бонусами: {discounts}₽\n\n"
        f"Топ рефералов:\n{top_text}",
        reply_markup=nav_keyboard()
    )

def show_instructions_menu(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    selection_mode.pop(user_id, None)
    if add_to_history:
        push_screen(user_id, "instructions")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📱 Инструкция для России")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, "📘 Инструкции\n\nВыберите нужную инструкцию:", reply_markup=kb)

def show_rf_instruction(chat_id: int, user_id: int, add_to_history: bool = True):
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "rf_instruction")

    bot.send_message(
        chat_id,
        "📱 Инструкция по установке eSIM\n\n"
        "Проверьте совместимость телефона.\n"
        "Наберите на телефоне команду: *#06#\n"
        "Если в списке есть строка EID — смартфон поддерживает eSIM.\n\n"
        "Установите eSIM через QR-код.\n"
        "Включите роуминг на eSIM.\n"
        "Переключите передачу данных на eSIM.\n\n"
        "⚠️ QR-код одноразовый — не удаляйте eSIM с устройства.",
        reply_markup=nav_keyboard()
    )

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
        "4. Получите QR-код и инструкцию.\n"
        "5. Установите eSIM до поездки или по прилёту.\n"
        "6. Включите роуминг на eSIM.\n"
        "7. Переключите передачу данных на eSIM.\n\n"
        "⚠️ QR-код одноразовый — не удаляйте eSIM с устройства.",
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

    zone = COUNTRY_TO_ZONE.get(country)
    price = ZONE_PRICES.get(zone, {}).get(plan)

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
    elif screen == "rf":
        show_rf(chat_id, user_id, add_to_history=False)
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
    parts = message.text.split()
    if len(parts) > 1:
        try:
            ref = int(parts[1])
        except ValueError:
            ref = None
    ensure_user(user_id, ref)
    show_main(message.chat.id, user_id, add_to_history=True)

@bot.message_handler(commands=["sendqr"])
def sendqr_handler(message):
    global admin_send_qr_target
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "Используй так: /sendqr USER_ID")
        return

    try:
        admin_send_qr_target = int(parts[1])
    except ValueError:
        bot.send_message(message.chat.id, "USER_ID должен быть числом.")
        return

    bot.send_message(message.chat.id, f"Теперь отправь ОДНО фото QR-кода. Я перешлю его пользователю {admin_send_qr_target}.")

@bot.message_handler(content_types=["text"])
def text_handler(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()

    ensure_user(user_id)

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

    if text == "🇷🇺 eSIM для России":
        show_rf(chat_id, user_id, add_to_history=True)
        return

    if text == "📘 Инструкции":
        show_instructions_menu(chat_id, user_id, add_to_history=True)
        return

    if text == "👤 Личный кабинет":
        show_cabinet(chat_id, user_id, add_to_history=True)
        return

    if text == "📊 Админ-статистика":
        show_admin_stats(chat_id, user_id)
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

    if text == "📱 Инструкция для России":
        show_rf_instruction(chat_id, user_id, add_to_history=True)
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
        matches = [country for country in COUNTRY_TO_ZONE.keys() if q in country.lower()]
        if not matches:
            bot.send_message(chat_id, "Ничего не найдено. Попробуйте другое название страны.", reply_markup=nav_keyboard())
            return

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for country in matches[:20]:
            kb.add(country_label(country))
        kb.add("🔙 Назад", "🏠 В начало")
        bot.send_message(chat_id, "Результаты поиска:", reply_markup=kb)
        return

    if "—" in text and "₽" in text:
        price = parse_price_from_order_text(text)
        if price is None:
            bot.send_message(chat_id, "Не удалось определить цену.", reply_markup=main_keyboard(user_id))
            return

        balance = get_user_balance(user_id)
        discount_used = min(balance, price)
        pay_amount = price - discount_used

        if discount_used > 0:
            subtract_balance(user_id, discount_used)

        if "|" in text:
            show_travel_instruction(chat_id, user_id, add_to_history=False)
        else:
            show_rf_instruction(chat_id, user_id, add_to_history=False)

        status = "pending_review" if pay_amount == 0 else "awaiting_receipt"

        cursor.execute(
            """
            INSERT INTO orders (user_id, text, price, pay_amount, discount_used, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, text, price, pay_amount, discount_used, status)
        )
        conn.commit()
        order_id = cursor.lastrowid

        if pay_amount == 0:
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
                f"Заказ отправлен на подтверждение. Ожидайте QR с инструкцией.",
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
        return

    bot.send_message(chat_id, "Не понял команду. Нажмите нужную кнопку 👇", reply_markup=main_keyboard(user_id))

@bot.message_handler(content_types=["photo"])
def photo_handler(message):
    global admin_send_qr_target
    user_id = message.from_user.id

    if user_id == ADMIN_ID and admin_send_qr_target:
        instruction = (
            "✅ Ваш QR-код eSIM\n\n"
            "📌 После установки:\n"
            "1. Включите роуминг в настройках eSIM\n"
            "2. Используйте eSIM для передачи данных\n\n"
            "⚠️ QR-код одноразовый — не удаляйте eSIM с устройства.\n\n"
            "Если нужна помощь — @F_Evdokimov"
        )
        bot.send_photo(admin_send_qr_target, message.photo[-1].file_id, caption=instruction)
        bot.send_message(ADMIN_ID, f"QR отправлен пользователю {admin_send_qr_target}")
        admin_send_qr_target = None
        return

    cursor.execute("""
        SELECT id, text, price, pay_amount, discount_used
        FROM orders
        WHERE user_id=? AND status='awaiting_receipt'
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))
    row = cursor.fetchone()

    if not row:
        bot.send_message(message.chat.id, "Не нашел заказ, который ждет чек. Сначала выберите тариф.", reply_markup=main_keyboard(user_id))
        return

    order_id, order_text, price, pay_amount, discount_used = row
    cursor.execute("UPDATE orders SET status='pending_review' WHERE id=?", (order_id,))
    conn.commit()

    username = message.from_user.username
    first_name = message.from_user.first_name or "Без имени"
    user_text = f"@{username}" if username else f"{first_name} (ID: {user_id})"

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"ok_{order_id}_{user_id}_{pay_amount}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"no_{order_id}_{user_id}")
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

    if data.startswith("ok_"):
        _, order_id, user_id, pay_amount = data.split("_")
        order_id = int(order_id)
        user_id = int(user_id)

        already_had_paid_orders = has_paid_orders(user_id, exclude_order_id=order_id)

        cursor.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))
        cursor.execute("SELECT ref FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        ref = row[0] if row else None

        if ref and not already_had_paid_orders:
            add_balance(ref, REF_BONUS)
            cursor.execute("UPDATE orders SET ref_bonus_given=1 WHERE id=?", (order_id,))
            bot.send_message(
                ref,
                f"🎉 Вам начислено {REF_BONUS}₽ за реферала.\n"
                f"Баланс можно использовать при следующей покупке."
            )

        conn.commit()

        bot.send_message(
            user_id,
            "✅ Заказ принят\n\n"
            "Мы проверили оплату и подготовим QR-код с инструкцией.\n\n"
            "Обычно это занимает 5–15 минут.\n\n"
            "Если есть вопросы — напишите @F_Evdokimov",
            reply_markup=main_keyboard(user_id)
        )

        bot.send_message(
            ADMIN_ID,
            f"✅ Оплата подтверждена\n\n"
            f"Чтобы отправить QR этому пользователю, отправь команду:\n"
            f"/sendqr {user_id}"
        )

        bot.answer_callback_query(call.id, "Оплата подтверждена")
        return

    if data.startswith("no_"):
        _, order_id, user_id = data.split("_")
        order_id = int(order_id)
        user_id = int(user_id)

        cursor.execute("SELECT discount_used FROM orders WHERE id=?", (order_id,))
        row = cursor.fetchone()
        discount_used = row[0] if row else 0

        if discount_used > 0:
            add_balance(user_id, discount_used)

        cursor.execute("UPDATE orders SET status='cancel' WHERE id=?", (order_id,))
        conn.commit()

        bot.send_message(
            user_id,
            "❌ Оплата не подтверждена.\n\n"
            "Если это ошибка — напишите в поддержку: @F_Evdokimov",
            reply_markup=main_keyboard(user_id)
        )

        bot.answer_callback_query(call.id, "Заказ отклонен")
        return

bot.polling(none_stop=True)
