import telebot
from telebot import types
import sqlite3

TOKEN = "8740697928:AAGe0ErxjUKZcyhpnWyGynBrfaLzN_zrlbs"
ADMIN_ID = 1157537072

bot = telebot.TeleBot(TOKEN)

# --- БАЗА ---
conn = sqlite3.connect("database.db", check_same_thread=False)
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

REF_PERCENT = 10

# --- ТАРИФЫ РФ ---
plans = {
    "1GB / 7 дней": 499,
    "3GB / 30 дней": 990,
    "5GB / 30 дней": 1290,
    "10GB / 30 дней": 2190,
    "20GB / 30 дней": 3590,
    "50GB / 90 дней": 7990,
    "100GB / 180 дней": 15990
}

# --- СТАРТ ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id

    ref = None
    if len(message.text.split()) > 1:
        ref = int(message.text.split()[1])

    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (user_id, balance, ref) VALUES (?, ?, ?)",
            (user_id, 0, ref)
        )
        conn.commit()

    markup = main_menu()

    bot.send_message(
        message.chat.id,
        "🚀 esimlime\n\n"
        "📶 Интернет по миру и в России\n"
        "🔓 Без VPN\n"
        "⚡ Подключение за 1 минуту\n\n"
        "Выбери 👇",
        reply_markup=markup
    )

# --- МЕНЮ ---
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🇷🇺 Интернет по РФ")
    markup.add("👤 Кабинет", "❓ Помощь")
    return markup

@bot.message_handler(func=lambda message: True)
def menu(message):

    if message.text == "🇷🇺 Интернет по РФ":
        show_plans(message)

    elif message.text == "👤 Кабинет":
        cabinet(message)

    elif message.text == "❓ Помощь":
        bot.send_message(message.chat.id, "Напиши: @your_support")

    elif message.text.startswith("1GB") or message.text.startswith("3GB"):
        buy(message)

# --- ТАРИФЫ ---
def show_plans(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)

    for p, price in plans.items():
        markup.add(f"{p} — {price}₽")

    markup.add("🔙 Назад")

    bot.send_message(message.chat.id, "Выбери тариф:", reply_markup=markup)

# --- ПОКУПКА ---
def buy(message):
    user_id = message.from_user.id

    price = int(message.text.split("—")[1].replace("₽", "").strip())

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("✅ Я оплатил")

    bot.send_message(
        message.chat.id,
        f"{message.text}\n\n"
        "Оплата СБП:\n"
        "📱 89870005569\n"
        "Т-Банк\n"
        "Федор Е.\n\n"
        "После оплаты нажми кнопку",
        reply_markup=markup
    )

    bot.register_next_step_handler(message, lambda m: wait_payment(m, message.text, price))

# --- ЖДЕМ ОПЛАТУ ---
def wait_payment(message, order_text, price):

    if message.text == "✅ Я оплатил":

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "Подтвердить",
            callback_data=f"confirm|{message.from_user.id}|{price}|{order_text}"
        ))

        bot.send_message(
            ADMIN_ID,
            f"💰 Новый заказ\n\n{order_text}",
            reply_markup=markup
        )

        bot.send_message(message.chat.id, "⏳ Проверяем оплату")

# --- ПОДТВЕРЖДЕНИЕ ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm"))
def confirm(call):
    data = call.data.split("|")

    user_id = int(data[1])
    price = int(data[2])
    order_text = data[3]

    cursor.execute(
        "INSERT INTO orders (user_id, order_text, price) VALUES (?, ?, ?)",
        (user_id, order_text, price)
    )

    cursor.execute("SELECT ref FROM users WHERE user_id=?", (user_id,))
    ref = cursor.fetchone()[0]

    if ref:
        reward = int(price * REF_PERCENT / 100)
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (reward, ref)
        )
        bot.send_message(ref, f"💰 Вам начислено {reward}₽")

    conn.commit()

    bot.send_message(user_id, "✅ Оплата подтверждена\nОтправляем eSIM")

# --- КАБИНЕТ ---
def cabinet(message):
    user_id = message.from_user.id

    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    balance = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (user_id,))
    count = cursor.fetchone()[0]

    bot.send_message(
        message.chat.id,
        f"💰 Баланс: {balance}₽\n📦 Заказы: {count}\n\n"
        f"🔗 Реф ссылка:\nhttps://t.me/ВАШ_БОТ?start={user_id}"
    )

bot.polling(none_stop=True)
