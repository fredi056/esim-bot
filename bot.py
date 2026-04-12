import telebot
from telebot import types
import sqlite3
import os

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = telebot.TeleBot(TOKEN)

# --- БАЗА ---
conn = sqlite3.connect("db.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER,
    ref INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    order_text TEXT,
    price INTEGER
)
""")

conn.commit()

# --- ДАННЫЕ ---
zones = {
    0: {"1GB": 200, "3GB": 450, "5GB": 650, "10GB": 1100},
    1: {"1GB": 300, "3GB": 700, "5GB": 1000, "10GB": 1800},
    2: {"1GB": 700, "3GB": 1100, "5GB": 2000, "10GB": 3500}
}

countries = {
    "Германия": 0,
    "Франция": 0,
    "Италия": 0,
    "Испания": 1,
    "США": 1,
    "Турция": 0,
    "Таиланд": 2,
    "Япония": 2,
    "ОАЭ": 2,
    "Казахстан": 1,
    "Грузия": 1,
    "Армения": 1
}

rf_plans = {
    "1GB / 7 дней": 499,
    "3GB / 30 дней": 990,
    "5GB / 30 дней": 1290,
    "10GB / 30 дней": 2190,
    "20GB / 30 дней": 3590,
    "50GB / 90 дней": 7990,
    "100GB / 180 дней": 15990
}

user_state = {}

# --- КНОПКИ ---
def main_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("🇷🇺 Интернет по РФ", "🌍 Путешествия")
    m.add("👤 Кабинет", "❓ Помощь")
    return m

def back_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("🔙 Назад")
    m.add("🏠 В начало")
    return m

# --- СТАРТ ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id

    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (user_id, balance, ref) VALUES (?, ?, ?)",
            (user_id, 0, None)
        )
        conn.commit()

    bot.send_message(
        message.chat.id,
        "🚀 esimlime\n\n"
        "📶 Интернет по миру и РФ\n"
        "🔓 Без VPN\n"
        "⚡ Подключение за 1 минуту\n\n"
        "Выбери нужный раздел 👇",
        reply_markup=main_menu()
    )

# --- ОБРАБОТКА ---
@bot.message_handler(func=lambda message: True)
def handler(message):
    text = message.text

    if text == "🏠 В начало":
        start(message)
        return

    if text == "🔙 Назад":
        start(message)
        return

    if text == "🌍 Путешествия":
        user_state[message.from_user.id] = "search"
        bot.send_message(
            message.chat.id,
            "🔍 Напиши страну (например: Турция)",
            reply_markup=back_menu()
        )
        return

    if text == "🇷🇺 Интернет по РФ":
        show_rf(message)
        return

    if text == "👤 Кабинет":
        cabinet(message)
        return

    if text == "❓ Помощь":
        help_msg(message)
        return

    if user_state.get(message.from_user.id) == "search":
        search_country(message)
        return

    if text in countries:
        show_plans(message, text)
        return

    if "₽" in text:
        buy(message)
        return

# --- ПОИСК ---
def search_country(message):
    query = message.text.lower()
    results = [c for c in countries if query in c.lower()]

    if not results:
        bot.send_message(message.chat.id, "❌ Страна не найдена", reply_markup=back_menu())
        return

    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for r in results[:10]:
        m.add(r)

    m.add("🔙 Назад")
    m.add("🏠 В начало")

    bot.send_message(message.chat.id, "Выбери страну:", reply_markup=m)

# --- ТАРИФЫ ---
def show_plans(message, country):
    zone = countries[country]

    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for gb, price in zones[zone].items():
        m.add(f"{country} {gb} — {price}₽")

    m.add("🔙 Назад")
    m.add("🏠 В начало")

    bot.send_message(message.chat.id, f"{country}\nВыбери тариф:", reply_markup=m)

# --- РФ ---
def show_rf(message):
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)

    for name, price in rf_plans.items():
        m.add(f"{name} — {price}₽")

    m.add("🔙 Назад")
    m.add("🏠 В начало")

    bot.send_message(message.chat.id, "🇷🇺 Тарифы для РФ:", reply_markup=m)

# --- ПОКУПКА ---
def buy(message):
    text = message.text
    price = int(text.split("—")[1].replace("₽", "").strip())

    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("✅ Я оплатил")
    m.add("🏠 В начало")

    bot.send_message(
        message.chat.id,
        f"{text}\n\n"
        "💳 Оплата:\n"
        "89870005569\n"
        "Т-Банк\n"
        "Федор Е.\n\n"
        "После оплаты нажми кнопку",
        reply_markup=m
    )

    bot.register_next_step_handler(message, lambda m: wait_payment(m, text, price))

def wait_payment(message, text, price):
    if message.text == "✅ Я оплатил":
        bot.send_message(ADMIN_ID, f"Новый заказ:\n{text}")
        bot.send_message(message.chat.id, "⏳ Проверяем оплату")

# --- КАБИНЕТ ---
def cabinet(message):
    user_id = message.from_user.id

    cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (user_id,))
    count = cursor.fetchone()[0]

    bot.send_message(
        message.chat.id,
        f"📦 Ваши заказы: {count}\n\n"
        "Реферальная программа скоро будет доступна"
    )

# --- ПОМОЩЬ ---
def help_msg(message):
    bot.send_message(
        message.chat.id,
        "❓ Поддержка:\n@F_Evdokimov\n\n"
        "Напиши, если нужна помощь или есть вопросы"
    )

# --- ЗАПУСК ---
bot.polling(none_stop=True)
