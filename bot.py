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

# =========================
# DB
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

COUNTRY_TO_ZONE: Dict[str, int] = {}
for zone, countries in ZONE_COUNTRIES.items():
    for c in countries:
        COUNTRY_TO_ZONE[c] = zone

REGIONS = {
    "🔥 Популярные страны": [
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
}

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
}

REF_PERCENT = 10

# =========================
# STATE
# =========================
history: Dict[int, List[Tuple[str, Optional[str]]]] = {}
search_mode: Dict[int, bool] = {}
admin_send_qr_target: Optional[int] = None

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

def country_label(country: str) -> str:
    return f"{EMOJI.get(country, '🌐')} {country}"

def push_screen(user_id: int, screen: str, payload: Optional[str] = None) -> None:
    stack = history.setdefault(user_id, [])
    if not stack or stack[-1] != (screen, payload):
        stack.append((screen, payload))

def reset_to_main(user_id: int) -> None:
    history[user_id] = [("main", None)]
    search_mode[user_id] = False

def go_back(user_id: int) -> Tuple[str, Optional[str]]:
    stack = history.setdefault(user_id, [("main", None)])
    if len(stack) > 1:
        stack.pop()
    return stack[-1]

def nav_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔙 Назад", "🏠 В начало")
    return kb

def main_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🇷🇺 eSIM для России", "✈️ eSIM для путешествий")
    kb.add("📘 Инструкции")
    kb.add("👤 Личный кабинет", "❓ Помощь")
    return kb

def parse_price_from_order_text(text: str) -> Optional[int]:
    try:
        return int(text.split("—")[1].replace("₽", "").strip())
    except Exception:
        return None

# =========================
# SCREENS
# =========================
def show_main(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        reset_to_main(user_id)

    bot.send_message(
        chat_id,
        "👋 Добро пожаловать в esimlime\n\n"
        "Здесь можно быстро подключить eSIM:\n"
        "• для путешествий за границу\n"
        "• для интернета в России без VPN\n\n"
        "Почему это удобно:\n"
        "• не нужно искать местную сим-карту\n"
        "• можно подключить заранее\n"
        "• установка занимает всего несколько минут\n\n"
        "Выберите нужный раздел ниже 👇",
        reply_markup=main_keyboard()
    )

def show_travel_home(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "travel")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔥 Популярные страны")
    kb.add("🌍 Европа", "🌏 Азия")
    kb.add("🌐 СНГ", "🌎 Америка")
    kb.add("🔎 Поиск страны")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        "✈️ eSIM для путешествий\n\n"
        "Выберите страну или регион.\n"
        "Сначала можно посмотреть популярные направления — это самый быстрый вариант 👇",
        reply_markup=kb
    )

def show_region(chat_id: int, user_id: int, region: str, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "region", region)

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for country in REGIONS.get(region, []):
        kb.add(country_label(country))
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, f"{region}\n\nВыберите страну:", reply_markup=kb)

def show_country(chat_id: int, user_id: int, country: str, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "country", country)

    zone = COUNTRY_TO_ZONE.get(country)
    if zone is None:
        bot.send_message(chat_id, "Страна пока недоступна.", reply_markup=nav_keyboard())
        return

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for plan_name, price in ZONE_PRICES[zone].items():
        kb.add(f"{country_label(country)} | {plan_name} — {price}₽")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, f"{country_label(country)}\n\nВыберите тариф:", reply_markup=kb)

def show_rf(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "rf")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for plan_name, price in RF_PLANS.items():
        kb.add(f"{plan_name} — {price}₽")
    kb.add("📱 Инструкция для России")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(
        chat_id,
        "🇷🇺 eSIM для России\n\n"
        "Подходит для интернета в России без VPN.\n"
        "Выберите подходящий тариф ниже 👇",
        reply_markup=kb
    )

def show_help(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "help")

    bot.send_message(
        chat_id,
        "❓ Помощь\n\nПо всем вопросам: @F_Evdokimov",
        reply_markup=nav_keyboard()
    )

def show_cabinet(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "cabinet")

    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0

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
        f"Реферальная ссылка:\n{ref_link}",
        reply_markup=nav_keyboard()
    )

def show_instructions_menu(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "instructions")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📱 Инструкция для России")
    kb.add("✈️ Инструкция для путешествий")
    kb.add("🔙 Назад", "🏠 В начало")

    bot.send_message(chat_id, "📘 Инструкции\n\nВыберите нужную инструкцию:", reply_markup=kb)

def show_rf_instruction(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "rf_instruction")

    text = (
        "📱 Инструкция по установке eSIM\n\n"
        "Проверьте совместимость телефона.\n"
        "Наберите на телефоне команду: *#06#\n"
        "На экране появятся серийные номера (IMEI и др.).\n"
        "Если в списке есть строка EID — смартфон оснащён модулем eSIM.\n\n"
        "Выберите и оплатите тариф.\n\n"
        "Получите письмо с eSIM.\n\n"
        "Установите eSIM.\n"
        "Откройте камеру, просканируйте QR-код — профиль активируется автоматически.\n\n"
        "Если через камеру не активировалось, то попробуйте отсканировать камерой с экрана другого устройства.\n\n"
        "Активация и включение интернета:\n"
        "• Включите роуминг, в настройках телефона на eSIM.\n"
        "• Вам придет сообщение от оператора со ссылкой на активацию.\n"
        "• Переключите передачу данных на eSIM.\n"
        "• Активация может занять до 24 часов, но обычно происходит мгновенно.\n\n"
        "Все, вы великолепны. Хорошего пользования.\n"
        "Если трафик закончился, напишите мне и подключим еще.\n\n"
        "Важно, во время тестов «белых списков» так же работать возможно не будет, т.к. блокируется именно поток данных.\n\n"
        "📌 После установки:\n"
        "1. Включите роуминг в настройках eSIM\n"
        "2. По прилету установите eSIM для передачи данных\n\n"
        "⚠️ QR-код одноразовый — не удаляйте eSIM с устройства, восстановить доступ не получится."
    )
    bot.send_message(chat_id, text, reply_markup=nav_keyboard())

def show_travel_instruction(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = False
    if add_to_history:
        push_screen(user_id, "travel_instruction")

    text = (
        "✈️ Инструкция для путешествий\n\n"
        "1. Выберите страну и тариф.\n"
        "2. Проверьте, что телефон поддерживает eSIM.\n"
        "3. После оплаты отправьте чек.\n"
        "4. Дождитесь подтверждения заказа.\n"
        "5. Получите QR-код и инструкцию.\n"
        "6. Установите eSIM до поездки или по прилету.\n"
        "7. Включите роуминг на eSIM.\n"
        "8. Переключите передачу данных на eSIM.\n\n"
        "📌 После установки:\n"
        "• включите роуминг на eSIM\n"
        "• используйте eSIM для передачи данных\n\n"
        "⚠️ QR-код одноразовый — не удаляйте eSIM с устройства, восстановить доступ не получится.\n\n"
        "Если нужна помощь — @F_Evdokimov"
    )
    bot.send_message(chat_id, text, reply_markup=nav_keyboard())

def show_search(chat_id: int, user_id: int, add_to_history: bool = True) -> None:
    search_mode[user_id] = True
    if add_to_history:
        push_screen(user_id, "search")

    bot.send_message(
        chat_id,
        "🔎 Напишите страну, например:\nTurkey\nThailand\nItaly\nUnited States",
        reply_markup=nav_keyboard()
    )

def render_from_state(chat_id: int, user_id: int, state: Tuple[str, Optional[str]]) -> None:
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
    elif screen == "rf_instruction":
        show_rf_instruction(chat_id, user_id, add_to_history=False)
    elif screen == "travel_instruction":
        show_travel_instruction(chat_id, user_id, add_to_history=False)
    elif screen == "search":
        show_search(chat_id, user_id, add_to_history=False)
    else:
        show_main(chat_id, user_id, add_to_history=False)

# =========================
# START
# =========================
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

# =========================
# ADMIN SEND QR
# =========================
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

    bot.send_message(
        message.chat.id,
        f"Теперь отправь ОДНО фото QR-кода. Я перешлю его пользователю {admin_send_qr_target}."
    )

# =========================
# TEXT HANDLER
# =========================
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
        state = go_back(user_id)
        render_from_state(chat_id, user_id, state)
        return

    if text == "🇷🇺 eSIM для России":
        show_rf(chat_id, user_id, add_to_history=True)
        return

    if text == "✈️ eSIM для путешествий":
        show_travel_home(chat_id, user_id, add_to_history=True)
        return

    if text == "📘 Инструкции":
        show_instructions_menu(chat_id, user_id, add_to_history=True)
        return

    if text == "👤 Личный кабинет":
        show_cabinet(chat_id, user_id, add_to_history=True)
        return

    if text == "❓ Помощь":
        show_help(chat_id, user_id, add_to_history=True)
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

    if search_mode.get(user_id):
        matches = [c for c in COUNTRY_TO_ZONE.keys() if text.lower() in c.lower()]
        if not matches:
            bot.send_message(chat_id, "Ничего не найдено. Попробуйте другое название страны.", reply_markup=nav_keyboard())
            return

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for c in matches[:20]:
            kb.add(country_label(c))
        kb.add("🔙 Назад", "🏠 В начало")
        bot.send_message(chat_id, "Результаты поиска:", reply_markup=kb)
        return

    selected_country = None
    for c in COUNTRY_TO_ZONE.keys():
        if text == c or text == country_label(c):
            selected_country = c
            break

    if selected_country:
        show_country(chat_id, user_id, selected_country, add_to_history=True)
        return

    if "—" in text and "₽" in text:
        price = parse_price_from_order_text(text)
        if price is None:
            bot.send_message(chat_id, "Не удалось определить цену.", reply_markup=main_keyboard())
            return

        if "|" in text:
            show_travel_instruction(chat_id, user_id, add_to_history=False)
        else:
            show_rf_instruction(chat_id, user_id, add_to_history=False)

        cursor.execute(
            "INSERT INTO orders (user_id, text, price, status) VALUES (?, ?, ?, ?)",
            (user_id, text, price, "awaiting_receipt")
        )
        conn.commit()

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("📸 Отправить чек")
        kb.add("🔙 Назад", "🏠 В начало")

        bot.send_message(
            chat_id,
            f"🧾 Ваш заказ:\n{text}\n\n"
            f"Оплата по СБП:\n"
            f"Номер: 89870005569\n"
            f"Банк: Т-Банк\n"
            f"Получатель: Федор Е.\n\n"
            f"После оплаты нажмите «📸 Отправить чек» и отправьте скриншот.",
            reply_markup=kb
        )
        return

    bot.send_message(chat_id, "Не понял команду. Нажмите нужную кнопку 👇", reply_markup=main_keyboard())

# =========================
# PHOTO HANDLER
# =========================
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
            "⚠️ QR-код одноразовый — не удаляйте eSIM с устройства, восстановить доступ не получится.\n\n"
            "Если нужна помощь — @F_Evdokimov"
        )
        bot.send_photo(admin_send_qr_target, message.photo[-1].file_id, caption=instruction)
        bot.send_message(ADMIN_ID, f"QR отправлен пользователю {admin_send_qr_target}")
        admin_send_qr_target = None
        return

    cursor.execute("""
        SELECT id, text, price
        FROM orders
        WHERE user_id=? AND status='awaiting_receipt'
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))
    row = cursor.fetchone()

    if not row:
        bot.send_message(message.chat.id, "Не нашел заказ, который ждет чек. Сначала выберите тариф.", reply_markup=main_keyboard())
        return

    order_id, order_text, price = row

    cursor.execute("UPDATE orders SET status='pending_review' WHERE id=?", (order_id,))
    conn.commit()

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"ok_{order_id}_{user_id}_{price}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"no_{order_id}_{user_id}")
    )

    bot.send_photo(
        ADMIN_ID,
        message.photo[-1].file_id,
        caption=f"Чек от пользователя {user_id}\nЗаказ: {order_text}",
        reply_markup=kb
    )

    bot.send_message(message.chat.id, "Чек отправлен. Заказ принят в обработку.", reply_markup=main_keyboard())

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
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (bonus, ref))

        conn.commit()

        bot.send_message(user_id, "✅ Заказ принят, ожидайте QR с инструкцией", reply_markup=main_keyboard())
        bot.send_message(ADMIN_ID, f"Оплата подтверждена.\nЧтобы отправить QR, напиши:\n/sendqr {user_id}")
        bot.answer_callback_query(call.id, "Оплата подтверждена")
        return

    if data.startswith("no_"):
        _, order_id, user_id = data.split("_")
        order_id = int(order_id)
        user_id = int(user_id)

        cursor.execute("UPDATE orders SET status='cancel' WHERE id=?", (order_id,))
        conn.commit()

        bot.send_message(user_id, "❌ Оплата не подтверждена. Если это ошибка — напишите @F_Evdokimov", reply_markup=main_keyboard())
        bot.answer_callback_query(call.id, "Заказ отклонен")
        return

# =========================
# RUN
# =========================
bot.polling(none_stop=True)
