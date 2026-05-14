import os
import psycopg2
from psycopg2.extras import RealDictCursor
import telebot
from telebot import types

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = telebot.TeleBot(TOKEN)

# =========================
# DB
# =========================

conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
conn.autocommit = True
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    balance INTEGER DEFAULT 0,
    ref BIGINT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    text TEXT,
    price INT,
    pay_amount INT,
    discount_used INT,
    status TEXT DEFAULT 'awaiting'
)
""")

# =========================
# COUNTRIES (100+)
# =========================

COUNTRIES = [
"Turkey","Egypt","United Arab Emirates","Thailand","Vietnam","Indonesia","Malaysia",
"Singapore","Philippines","Japan","South Korea","China","India","Sri Lanka","Nepal",
"Bangladesh","Pakistan","Kazakhstan","Uzbekistan","Kyrgyzstan","Tajikistan","Georgia",
"Armenia","Azerbaijan","Israel","Jordan","Qatar","Kuwait","Saudi Arabia","Oman","Bahrain",
"Iran","Iraq","Lebanon","Syria","Yemen",

"USA","Canada","Mexico","Cuba","Dominican Republic","Panama","Costa Rica","Guatemala",
"Honduras","Nicaragua","El Salvador","Colombia","Peru","Chile","Brazil","Argentina",
"Uruguay","Paraguay","Bolivia","Ecuador","Venezuela",

"Germany","France","Italy","Spain","Portugal","Netherlands","Belgium","Switzerland",
"Austria","Poland","Czech Republic","Slovakia","Hungary","Romania","Bulgaria","Greece",
"Croatia","Serbia","Slovenia","Bosnia and Herzegovina","Montenegro","North Macedonia",
"Albania","Norway","Sweden","Finland","Denmark","Iceland","Ireland","United Kingdom",
"Ukraine","Belarus","Lithuania","Latvia","Estonia",

"South Africa","Kenya","Nigeria","Morocco","Tunisia","Algeria","Ethiopia","Ghana",
"Tanzania","Uganda","Rwanda","Senegal","Cameroon","Ivory Coast",

"Australia","New Zealand"
]

# =========================
# STATE
# =========================

history = {}
admin_menu = {}

# =========================
# KB
# =========================

def kb_main():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🌍 Страны", "⚡ Подбор")
    kb.add("👤 Кабинет")
    if ADMIN_ID:
        kb.add("📊 Админ")
    return kb

# =========================
# USER
# =========================

def ensure_user(uid):
    cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (uid,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, balance) VALUES (%s,0)", (uid,))

def get_balance(uid):
    cursor.execute("SELECT balance FROM users WHERE user_id=%s", (uid,))
    row = cursor.fetchone()
    return row["balance"] if row else 0

# =========================
# MAIN
# =========================

def show_main(chat_id, uid):
    bot.send_message(chat_id, "🌍 eSIM Bot", reply_markup=kb_main())

# =========================
# INLINE ADMIN PANEL
# =========================

def admin_panel():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"))
    kb.add(types.InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"))
    kb.add(types.InlineKeyboardButton("📦 Заказы", callback_data="admin_orders"))
    kb.add(types.InlineKeyboardButton("💰 Выручка", callback_data="admin_revenue"))
    return kb

# =========================
# START
# =========================

@bot.message_handler(commands=["start"])
def start(m):
    uid = m.from_user.id
    ensure_user(uid)
    show_main(m.chat.id, uid)

# =========================
# TEXT HANDLER
# =========================

@bot.message_handler(func=lambda m: True)
def handler(m):
    uid = m.from_user.id
    cid = m.chat.id
    text = m.text

    ensure_user(uid)

    if text == "👤 Кабинет":
        bot.send_message(cid, f"Баланс: {get_balance(uid)}₽")
        return

    if text == "📊 Админ" and uid == ADMIN_ID:
        bot.send_message(cid, "🛠 Админ панель", reply_markup=admin_panel())
        return

    if text == "🌍 Страны":
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for c in COUNTRIES[:60]:
            kb.add(c)
        bot.send_message(cid, "Выберите страну:", reply_markup=kb)
        return

# =========================
# INLINE CALLBACK ADMIN
# =========================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    data = call.data

    # STATS
    if data == "admin_stats":
        cursor.execute("SELECT COUNT(*) FROM users")
        users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM orders")
        orders = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(price),0) FROM orders")
        revenue = cursor.fetchone()[0]

        bot.edit_message_text(
            f"📊 СТАТИСТИКА\n\n"
            f"👤 Users: {users}\n"
            f"📦 Orders: {orders}\n"
            f"💰 Revenue: {revenue}₽",
            call.message.chat.id,
            call.message.message_id
        )

    # USERS
    elif data == "admin_users":
        cursor.execute("SELECT COUNT(*) FROM users")
        users = cursor.fetchone()[0]

        bot.edit_message_text(
            f"👥 Пользователи: {users}",
            call.message.chat.id,
            call.message.message_id
        )

    # ORDERS
    elif data == "admin_orders":
        cursor.execute("SELECT COUNT(*) FROM orders")
        orders = cursor.fetchone()[0]

        bot.edit_message_text(
            f"📦 Заказы: {orders}",
            call.message.chat.id,
            call.message.message_id
        )

    # REVENUE
    elif data == "admin_revenue":
        cursor.execute("SELECT COALESCE(SUM(price),0) FROM orders")
        revenue = cursor.fetchone()[0]

        bot.edit_message_text(
            f"💰 Выручка: {revenue}₽",
            call.message.chat.id,
            call.message.message_id
        )

# =========================
# RUN
# =========================

bot.polling(non_stop=True)
