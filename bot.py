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

countries = ["Германия","Франция","Италия","США","Турция","Таиланд","ОАЭ"]
plans = {"1GB":300,"3GB":700,"5GB":1000,"10GB":1800}

rf_plans = {
    "1GB / 7 дней": 499,
    "3GB / 30 дней": 990,
    "5GB / 30 дней": 1290,
    "10GB / 30 дней": 2190,
    "20GB / 30 дней": 3590,
    "50GB / 90 дней": 7990,
    "100GB / 180 дней": 15990
}

def main():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("🇷🇺 РФ","🌍 Путешествия")
    m.add("👤 Кабинет","❓ Помощь")
    return m

def nav():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("🔙 Назад","🏠 В начало")
    return m

@bot.message_handler(commands=['start'])
def start(m):
    uid = m.from_user.id
    ref = None
    if len(m.text.split())>1:
        ref = int(m.text.split()[1])

    cursor.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users VALUES (?,?,?)",(uid,0,ref))
        conn.commit()

    bot.send_message(m.chat.id,"🚀 esimlime\nИнтернет без VPN",reply_markup=main())

@bot.message_handler(func=lambda m: True)
def handler(m):
    t = m.text

    if t=="🏠 В начало":
        start(m)

    elif t=="🌍 Путешествия":
        bot.send_message(m.chat.id,"Напиши страну",reply_markup=nav())

    elif t=="🇷🇺 РФ":
        k=types.ReplyKeyboardMarkup(resize_keyboard=True)
        for n,p in rf_plans.items():
            k.add(f"{n} — {p}₽")
        k.add("🔙 Назад","🏠 В начало")
        bot.send_message(m.chat.id,"РФ тарифы",reply_markup=k)

    elif t=="👤 Кабинет":
        uid=m.from_user.id
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal=cursor.fetchone()[0]
        bot.send_message(m.chat.id,f"Баланс: {bal}₽")

    elif t=="❓ Помощь":
        bot.send_message(m.chat.id,"@F_Evdokimov")

    elif "₽" in t:
        buy(m)

def buy(m):
    price=int(m.text.split("—")[1].replace("₽","").strip())
    uid=m.from_user.id

    cursor.execute("INSERT INTO orders (user_id,text,price,status) VALUES (?,?,?,?)",
                   (uid,m.text,price,"wait"))
    conn.commit()

    k=types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.add("📸 Отправить чек","🏠 В начало")

    bot.send_message(
        m.chat.id,
        f"{m.text}\n\nОплата:\n89870005569\nТ-Банк\nФедор Е.",
        reply_markup=k
    )

@bot.message_handler(content_types=['photo'])
def check(m):
    uid=m.from_user.id

    cursor.execute("SELECT id,text,price FROM orders WHERE user_id=? AND status='wait' ORDER BY id DESC",(uid,))
    o=cursor.fetchone()
    if not o:
        return

    oid,text,price=o

    k=types.InlineKeyboardMarkup()
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

@bot.callback_query_handler(func=lambda c: True)
def admin(c):
    if c.data.startswith("ok"):
        _,oid,uid,price=c.data.split("_")
        uid=int(uid); price=int(price)

        cursor.execute("UPDATE orders SET status='paid' WHERE id=?", (oid,))
        cursor.execute("SELECT ref FROM users WHERE user_id=?", (uid,))
        ref=cursor.fetchone()[0]

        if ref:
            bonus=int(price*0.1)
            cursor.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (bonus,ref))

        conn.commit()

        bot.send_message(uid,"✅ Заказ принят, ожидайте QR с инструкцией")

        bot.send_message(uid,"📷 Отправьте QR сюда (администратор)")
        
        bot.send_message(ADMIN_ID,f"Отправь QR пользователю {uid}")

    if c.data.startswith("no"):
        oid=c.data.split("_")[1]
        cursor.execute("UPDATE orders SET status='cancel' WHERE id=?", (oid,))
        conn.commit()
        bot.send_message(ADMIN_ID,"❌ Отклонено")

bot.polling(none_stop=True)
