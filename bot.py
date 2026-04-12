# --- ВИТРИНА СТРАН ---

regions = {
    "🌍 Европа": ["Германия","Франция","Италия","Испания","Нидерланды"],
    "🌏 Азия": ["Таиланд","ОАЭ","Япония","Турция"],
    "🌎 Америка": ["США"]
}

def show_regions(m):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for r in regions:
        k.add(r)
    k.add("🔙 Назад","🏠 В начало")
    bot.send_message(m.chat.id,"Выбери регион:",reply_markup=k)

def show_countries(m, region):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in regions[region]:
        k.add(c)
    k.add("🔙 Назад","🏠 В начало")
    bot.send_message(m.chat.id,"Выбери страну:",reply_markup=k)

# --- ВСТАВЬ В handler ---

elif t=="🌍 Путешествия":
    show_regions(m)

elif t in regions:
    show_countries(m,t)

elif t in sum(regions.values(), []):
    show_plans(m,t)
