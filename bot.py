import os
import json
import telebot
import threading
import time
from queue import Queue
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=False)
app = Flask(__name__)

ROUTE_FILE = "routes.json"

ROUTES = []
user_sessions = {}
user_modes = {}  # 👈 KEY CHANGE

route_queues = {}
route_workers = {}

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
# WORKER
# ==============================

def get_key(r):
    return f"{r['source_chat']}_{r['dest_chat']}"

def worker(route):
    key = get_key(route)
    q = route_queues[key]

    while True:
        msg = q.get()
        try:
            time.sleep(route.get("delay", 0))

            bot.copy_message(
                route["dest_chat"],
                msg.chat.id,
                msg.message_id,
                message_thread_id=route["dest_topic"]
            )

        except Exception as e:
            print("Worker error:", e)

        q.task_done()

def ensure_worker(route):
    key = get_key(route)

    if key not in route_queues:
        route_queues[key] = Queue()

    if key not in route_workers:
        t = threading.Thread(target=worker, args=(route,), daemon=True)
        t.start()
        route_workers[key] = t

for r in ROUTES:
    ensure_worker(r)

# ==============================
# UI
# ==============================

def main_menu():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🚀 Autoforward Mode", callback_data="mode_af"))
    kb.add(InlineKeyboardButton("🧠 Batch Mode", callback_data="mode_batch"))
    return kb

def af_menu():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➕ Add Route", callback_data="add_route"))
    kb.add(InlineKeyboardButton("📡 View Routes", callback_data="view_routes"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="back_main"))
    return kb

# ==============================
# START
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "⚡ Select Mode", reply_markup=main_menu())

# ==============================
# CALLBACK
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):

    uid = call.from_user.id
    data = call.data

    # SELECT MODE
    if data == "mode_af":
        user_modes[uid] = "af"
        bot.edit_message_text("🚀 Autoforward Mode", call.message.chat.id, call.message.message_id, reply_markup=af_menu())

    elif data == "mode_batch":
        user_modes[uid] = "batch"
        user_sessions[uid] = {"mode": "batch_collect"}
        bot.edit_message_text("🧠 Send files now. Then type /done", call.message.chat.id, call.message.message_id)

    elif data == "back_main":
        bot.edit_message_text("⚡ Select Mode", call.message.chat.id, call.message.message_id, reply_markup=main_menu())

    # AUTOFORWARD
    elif data == "add_route":
        user_sessions[uid] = {"mode": "add"}
        bot.send_message(call.message.chat.id, "Send:\n/addroute src src_topic dest dest_topic delay")

    elif data == "view_routes":
        if not ROUTES:
            bot.send_message(call.message.chat.id, "No routes")
            return

        txt = ""
        for i, r in enumerate(ROUTES, 1):
            txt += f"{i}. {r['source_chat']} ➜ {r['dest_chat']}\n"

        bot.send_message(call.message.chat.id, txt)

# ==============================
# MESSAGE HANDLER
# ==============================

@bot.message_handler(func=lambda m: True, content_types=['text','photo','video','document'])
def handler(m):

    uid = m.from_user.id

    # ================= SESSION =================
    if uid in user_sessions:

        s = user_sessions[uid]

        try:
            # ADD ROUTE
            if s["mode"] == "add":

                parts = m.text.split()

                src = int(parts[1])
                src_t = None if parts[2]=="none" else int(parts[2])
                dst = int(parts[3])
                dst_t = None if parts[4]=="none" else int(parts[4])
                delay = int(parts[5])

                route = {
                    "source_chat": src,
                    "source_topic": src_t,
                    "dest_chat": dst,
                    "dest_topic": dst_t,
                    "delay": delay
                }

                ROUTES.append(route)
                save_routes()
                ensure_worker(route)

                bot.reply_to(m, "✅ Route Added")
                user_sessions.pop(uid)

            # BATCH COLLECT
            elif s["mode"] == "batch_collect":
                user_sessions[uid].setdefault("files", []).append(m)

        except Exception as e:
            bot.reply_to(m, f"Error: {e}")
            user_sessions.pop(uid)

        return

    # ================= AUTOFORWARD =================

    if user_modes.get(uid) != "af":
        return

    if m.text and m.text.startswith("/"):
        return

    for r in ROUTES:
        if m.chat.id == r["source_chat"]:
            ensure_worker(r)
            route_queues[get_key(r)].put(m)

# ==============================
# BATCH DONE
# ==============================

@bot.message_handler(commands=['done'])
def done(m):
    uid = m.from_user.id

    if uid not in user_sessions:
        return

    batch = user_sessions[uid].get("files", [])

    bot.send_message(m.chat.id, "Send destination chat ID")
    user_sessions[uid] = {"mode": "batch_send", "files": batch}

@bot.message_handler(func=lambda m: True)
def batch_send(m):
    uid = m.from_user.id

    if uid not in user_sessions:
        return

    s = user_sessions[uid]

    if s["mode"] != "batch_send":
        return

    dest = int(m.text)

    for msg in s["files"]:
        bot.copy_message(dest, msg.chat.id, msg.message_id)

    bot.send_message(m.chat.id, "✅ Batch Done")
    user_sessions.pop(uid)

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
