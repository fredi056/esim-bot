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
    balance INTEGER DEFAULT 0,
    ref INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    price INTEGER,
    status TEXT
)
""")

conn.commit()

# --- ДАННЫЕ ---
popular = ["🇹🇷 Турция","🇦🇪 ОАЭ","🇹🇭 Таиланд","🇬🇪 Грузия","🇰🇿 Казахстан","🇦🇲 Армения","🇮🇩 Индонезия (Бали)"]

regions = {
    "🌍 Европа": ["🇩🇪 Германия","🇫🇷 Франция","🇮🇹 Италия","🇪🇸 Испания","🇳🇱 Нидерланды"],
    "🌏 Азия": ["🇹🇭 Таиланд","🇦🇪 ОАЭ","🇯🇵 Япония","🇮🇩 Индонезия (Бали)","🇨🇳 Китай"],
    "🌎 Америка": ["🇺🇸 США","🇧🇷 Бразилия"],
    "🌐 СНГ": ["🇬🇪 Грузия","🇰🇿 Казахстан","🇦🇲 Армения","🇺🇿 Узбекистан"]
}

plans = {
    "1GB": 300,
    "3GB": 700,
    "5GB": 1000,
    "10GB": 1800
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

# --- МЕНЮ ---
def main():
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.add("🇷🇺 Интернет РФ","🌍 Путешествия")
    k.add("👤 Кабинет","❓ Помощь")
    return k

def nav():
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.add("🔙 Назад","🏠 В начало")
    return k

# --- СТАРТ ---
@bot.message_handler(commands=['start'])
def start(m):
    uid = m.from_user.id
    ref = None

    if len(m.text.split()) > 1:
        ref = int(m.text.split()[1])

    cursor.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users VALUES (?,?,?)",(uid,0,ref))
        conn.commit()

    bot.send_message(
        m.chat.id,
        "🚀 esimlime\n\n📶 Интернет по миру и РФ\n🔓 Без VPN\n⚡ Подключение за 1 минуту",
        reply_markup=main()
    )

# --- ВИТРИНА ---
def show_travel(m):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.add("🔥 Популярные")
    for r in regions:
        k.add(r)
    k.add("🔙 Назад","🏠 В начало")

    bot.send_message(m.chat.id,"🌍 Выбери направление:",reply_markup=k)

def show_popular(m):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in popular:
        k.add(c)
    k.add("🔙 Назад","🏠 В начало")

    bot.send_message(m.chat.id,"🔥 Популярные страны:",reply_markup=k)

def show_countries(m, region):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in regions[region]:
        k.add(c)
    k.add("🔙 Назад","🏠 В начало")

    bot.send_message(m.chat.id,region,reply_markup=k)

def show_plans(m, country):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for gb,price in plans.items():
        k.add(f"{country} {gb} — {price}₽")
    k.add("🔙 Назад","🏠 В начало")

    bot.send_message(m.chat.id,f"{country} тарифы:",reply_markup=k)

# --- РФ ---
def show_rf(m):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for n,p in rf_plans.items():
        k.add(f"{n} — {p}₽")
    k.add("🔙 Назад","🏠 В начало")

    bot.send_message(m.chat.id,"Тарифы РФ:",reply_markup=k)

# --- ОБРАБОТКА ---
@bot.message_handler(func=lambda m: True)
def handler(m):
    t = m.text

    if t == "🏠 В начало":
        start(m)

    elif t == "🌍 Путешествия":
        show_travel(m)

    elif t == "🔥 Популярные":
        show_popular(m)

    elif t in regions:
        show_countries(m, t)

    elif t in popular or t in sum(regions.values(), []):
        show_plans(m, t)

    elif t == "🇷🇺 Интернет РФ":
        show_rf(m)

    elif t == "👤 Кабинет":
        uid = m.from_user.id
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (uid,))
        orders = cursor.fetchone()[0]

        link = f"https://t.me/esimlimebot?start={uid}"

        bot.send_message(
            m.chat.id,
            f"👤 Кабинет\n\nБаланс: {bal}₽\nЗаказы: {orders}\n\nРеферальная ссылка:\n{link}"
        )

    elif t == "❓ Помощь":
        bot.send_message(m.chat.id,"Поддержка: @F_Evdokimov")

    elif "₽" in t:
        buy(m)

# --- ПОКУПКА ---
def buy(m):
    uid = m.from_user.id
    price = int(m.text.split("—")[1].replace("₽","").strip())

    cursor.execute(
        "INSERT INTO orders (user_id,text,price,status) VALUES (?,?,?,?)",
        (uid,m.text,price,"wait")
    )
    conn.commit()

    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.add("📸 Отправить чек","🏠 В начало")

    bot.send_message(
        m.chat.id,
        f"{m.text}\n\nОплата:\n89870005569\nТ-Банк\nФедор Е.",
        reply_markup=k
    )

# --- ЧЕК ---
@bot.message_handler(content_types=['photo'])
def check(m):
    uid = m.from_user.id

    cursor.execute("SELECT id,text,price FROM orders WHERE user_id=? AND status='wait' ORDER BY id DESC",(uid,))
    o = cursor.fetchone()
    if not o:
        return

    oid,text,price = o

    k = types.InlineKeyboardMarkup()
    k.add(
        types.InlineKeyboardButton("✅ Подтвердить",callback_data=f"ok_{oid}_{uid}_{price}"),
        types.InlineKeyboardButton("❌ Отклонить",callback_data=f"no_{oid}")
    )

    bot.send_photo(
        ADMIN_ID,
        m.photo[-1].file_id,
        caption=f"Чек\n{uid}\n{text}",
        reply_markup=k
    )

    bot.send_message(m.chat.id,"Чек отправлен, ожидайте")

# --- АДМИН ---
@bot.callback_query_handler(func=lambda c: True)
def admin(c):
    if c.data.startswith("ok"):
        _,oid,uid,price = c.data.split("_")
        uid = int(uid); price = int(price)

        cursor.execute("UPDATE orders SET status='paid' WHERE id=?", (oid,))

        cursor.execute("SELECT ref FROM users WHERE user_id=?", (uid,))
        ref = cursor.fetchone()[0]

        if ref:
            bonus = int(price * 0.1)
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (bonus, ref))

        conn.commit()

        bot.send_message(uid,"✅ Заказ принят, ожидайте QR с инструкцией")
        bot.send_message(ADMIN_ID,f"Отправь QR пользователю {uid}")

    elif c.data.startswith("no"):
        oid = c.data.split("_")[1]
        cursor.execute("UPDATE orders SET status='cancel' WHERE id=?", (oid,))
        conn.commit()
        bot.send_message(ADMIN_ID,"❌ Отклонено")

# --- ЗАПУСК ---
bot.polling(none_stop=True)
