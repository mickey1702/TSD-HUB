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
# MENUS
# ==============================

def main_menu():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➕ Add Route", callback_data="add_route"))
    kb.add(InlineKeyboardButton("📡 Routes", callback_data="routes"))
    return kb

def routes_menu():
    kb = InlineKeyboardMarkup()
    for i, r in enumerate(ROUTES):
        kb.add(InlineKeyboardButton(f"Route {i+1}", callback_data=f"route_{i}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="back_main"))
    return kb

def route_panel(i):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚙️ Processing", callback_data=f"proc_{i}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"del_{i}")
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="routes"))
    return kb

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
    kb.add(InlineKeyboardButton("⚙️ Mode", callback_data=f"mode_{i}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"route_{i}"))
    return kb

# ==============================
# START
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "⚡ TSD HUB CONTROL PANEL", reply_markup=main_menu())

# ==============================
# CALLBACK
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    data = call.data
    uid = call.from_user.id

    if data == "routes":
        bot.edit_message_text("📡 ROUTES", call.message.chat.id, call.message.message_id, reply_markup=routes_menu())

    elif data == "back_main":
        bot.edit_message_text("⚡ MAIN MENU", call.message.chat.id, call.message.message_id, reply_markup=main_menu())

    elif data == "add_route":
        user_sessions[uid] = {"mode": "add_line"}
        bot.send_message(call.message.chat.id, "Send route in format:\n/addroute src src_topic dest dest_topic delay")

    elif data.startswith("route_"):
        i = int(data.split("_")[1])
        r = ROUTES[i]

        txt = f"""📡 Route {i+1}

SRC: {r['source_chat']} | {r['source_topic']}
DST: {r['dest_chat']} | {r['dest_topic']}
Delay: {r['delay']}
Mode: {r['mode']}
"""
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=route_panel(i))

    elif data.startswith("del_"):
        i = int(data.split("_")[1])
        ROUTES.pop(i)
        save_routes()
        bot.answer_callback_query(call.id, "Deleted")
        bot.edit_message_text("Updated Routes", call.message.chat.id, call.message.message_id, reply_markup=routes_menu())

    elif data.startswith("proc_"):
        i = int(data.split("_")[1])
        bot.edit_message_text("⚙️ Processing", call.message.chat.id, call.message.message_id, reply_markup=processing_panel(i))

    elif data.startswith("mode_"):
        i = int(data.split("_")[1])
        ROUTES[i]["mode"] = "filename" if ROUTES[i]["mode"] == "caption" else "caption"
        save_routes()
        bot.answer_callback_query(call.id, ROUTES[i]["mode"])

    elif data.startswith("prefix_"):
        user_sessions[uid] = {"mode": "prefix", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send prefix")

    elif data.startswith("suffix_"):
        user_sessions[uid] = {"mode": "suffix", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send suffix")

    elif data.startswith("replace_"):
        user_sessions[uid] = {"mode": "replace", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send: old new")

    elif data.startswith("clean_"):
        user_sessions[uid] = {"mode": "clean", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send word")

# ==============================
# MESSAGE HANDLER
# ==============================

@bot.message_handler(func=lambda m: True)
def handler(m):
    uid = m.from_user.id

    # SESSION
    if uid in user_sessions:
        s = user_sessions[uid]

        try:
            # ONE LINE ROUTE
            if s["mode"] == "add_line":
                parts = m.text.split()

                if parts[0] != "/addroute":
                    bot.reply_to(m, "Use format:\n/addroute src src_topic dest dest_topic delay")
                    return

                src = int(parts[1])
                src_t = None if parts[2].lower() == "none" else int(parts[2])
                dst = int(parts[3])
                dst_t = None if parts[4].lower() == "none" else int(parts[4])
                delay = int(parts[5])

                route = {
                    "source_chat": src,
                    "source_topic": src_t,
                    "dest_chat": dst,
                    "dest_topic": dst_t,
                    "delay": delay,
                    "mode": "caption",
                    "prefix": "",
                    "suffix": "",
                    "replace": {},
                    "clean_words": []
                }

                ROUTES.append(route)
                save_routes()

                bot.reply_to(m, "✅ Route Added")
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
                bot.reply_to(m, "Word added")
                user_sessions.pop(uid)

        except Exception as e:
            bot.reply_to(m, f"Error: {e}")
            user_sessions.pop(uid)

        return

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
