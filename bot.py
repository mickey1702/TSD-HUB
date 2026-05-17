import os
import json
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

ROUTE_FILE = "routes.json"

ROUTES = []
user_sessions = {}

# ==============================
# LOAD / SAVE
# ==============================

def load_routes():
    try:
        with open(ROUTE_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_routes():
    with open(ROUTE_FILE, "w") as f:
        json.dump(ROUTES, f, indent=4)

ROUTES = load_routes()

# ==============================
# MAIN MENU
# ==============================

def main_menu():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🚀 Autoforward", callback_data="add_route"))
    kb.add(InlineKeyboardButton("📡 Routes", callback_data="routes"))
    return kb

# ==============================
# ROUTES PANEL
# ==============================

def routes_menu():
    kb = InlineKeyboardMarkup()
    for i, r in enumerate(ROUTES):
        kb.add(InlineKeyboardButton(f"Route {i+1}", callback_data=f"route_{i}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="back_main"))
    return kb

# ==============================
# ROUTE SETTINGS PANEL
# ==============================

def route_panel(i):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚙️ Processing", callback_data=f"proc_{i}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"del_{i}")
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="routes"))
    return kb

# ==============================
# PROCESSING PANEL
# ==============================

def processing_panel(i):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔁 Replace", callback_data=f"replace_{i}"),
        InlineKeyboardButton("❌ Remove", callback_data=f"clean_{i}")
    )
    kb.add(
        InlineKeyboardButton("✏️ Prefix", callback_data=f"prefix_{i}"),
        InlineKeyboardButton("🔚 Suffix", callback_data=f"suffix_{i}")
    )
    kb.add(
        InlineKeyboardButton("⚙️ Mode", callback_data=f"mode_{i}"),
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"route_{i}"))
    return kb

# ==============================
# START
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "⚡ TSD HUB CONTROL PANEL", reply_markup=main_menu())

# ==============================
# CALLBACK HANDLER
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    data = call.data
    uid = call.from_user.id

    # MAIN NAVIGATION
    if data == "routes":
        bot.edit_message_text("📡 ROUTES", call.message.chat.id, call.message.message_id, reply_markup=routes_menu())

    elif data == "back_main":
        bot.edit_message_text("⚡ MAIN MENU", call.message.chat.id, call.message.message_id, reply_markup=main_menu())

    # ADD ROUTE
    elif data == "add_route":
        user_sessions[uid] = {"mode": "add1"}
        bot.send_message(call.message.chat.id, "Send SOURCE CHAT ID")

    # OPEN ROUTE
    elif data.startswith("route_"):
        i = int(data.split("_")[1])
        r = ROUTES[i]
        txt = f"""📡 Route {i+1}

SRC: {r['source_chat']}
DST: {r['dest_chat']}
Mode: {r.get('mode','caption')}
"""
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=route_panel(i))

    # DELETE ROUTE
    elif data.startswith("del_"):
        i = int(data.split("_")[1])
        ROUTES.pop(i)
        save_routes()
        bot.answer_callback_query(call.id, "Deleted")
        bot.edit_message_text("Updated Routes", call.message.chat.id, call.message.message_id, reply_markup=routes_menu())

    # PROCESSING PANEL
    elif data.startswith("proc_"):
        i = int(data.split("_")[1])
        bot.edit_message_text("⚙️ Processing Engine", call.message.chat.id, call.message.message_id, reply_markup=processing_panel(i))

    # MODE SWITCH
    elif data.startswith("mode_"):
        i = int(data.split("_")[1])
        ROUTES[i]["mode"] = "filename" if ROUTES[i].get("mode") == "caption" else "caption"
        save_routes()
        bot.answer_callback_query(call.id, f"Mode: {ROUTES[i]['mode']}")

    # PREFIX
    elif data.startswith("prefix_"):
        i = int(data.split("_")[1])
        user_sessions[uid] = {"mode": "prefix", "route": i}
        bot.send_message(call.message.chat.id, "Send prefix")

    # SUFFIX
    elif data.startswith("suffix_"):
        i = int(data.split("_")[1])
        user_sessions[uid] = {"mode": "suffix", "route": i}
        bot.send_message(call.message.chat.id, "Send suffix")

    # REPLACE
    elif data.startswith("replace_"):
        i = int(data.split("_")[1])
        user_sessions[uid] = {"mode": "replace", "route": i}
        bot.send_message(call.message.chat.id, "Send: old new")

    # CLEAN
    elif data.startswith("clean_"):
        i = int(data.split("_")[1])
        user_sessions[uid] = {"mode": "clean", "route": i}
        bot.send_message(call.message.chat.id, "Send word to remove")

# ==============================
# SESSION HANDLER
# ==============================

@bot.message_handler(func=lambda m: True)
def handler(m):
    uid = m.from_user.id

    if uid not in user_sessions:
        return

    s = user_sessions[uid]

    try:
        if s["mode"] == "add1":
            s["src"] = int(m.text)
            s["mode"] = "add2"
            bot.reply_to(m, "Send DEST CHAT ID")

        elif s["mode"] == "add2":
            route = {
                "source_chat": s["src"],
                "dest_chat": int(m.text),
                "mode": "caption",
                "prefix": "",
                "suffix": "",
                "replace": {},
                "clean_words": []
            }
            ROUTES.append(route)
            save_routes()
            bot.reply_to(m, "✅ Route added")
            user_sessions.pop(uid)

        elif s["mode"] == "prefix":
            ROUTES[s["route"]]["prefix"] = m.text
            save_routes()
            bot.reply_to(m, "Prefix set")
            user_sessions.pop(uid)

        elif s["mode"] == "suffix":
            ROUTES[s["route"]]["suffix"] = m.text
            save_routes()
            bot.reply_to(m, "Suffix set")
            user_sessions.pop(uid)

        elif s["mode"] == "replace":
            old, new = m.text.split()
            ROUTES[s["route"]]["replace"][old] = new
            save_routes()
            bot.reply_to(m, "Replace added")
            user_sessions.pop(uid)

        elif s["mode"] == "clean":
            ROUTES[s["route"]]["clean_words"].append(m.text)
            save_routes()
            bot.reply_to(m, "Removed word added")
            user_sessions.pop(uid)

    except Exception as e:
        bot.reply_to(m, f"Error: {e}")
        user_sessions.pop(uid, None)

# ==============================
# WEBHOOK
# ==============================

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode())
    bot.process_new_updates([update])
    return "ok"

@app.route("/")
def home():
    return "RUNNING"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
