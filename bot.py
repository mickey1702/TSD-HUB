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
# TEXT PROCESSING
# ==============================

def process_text(text, route):
    if not text:
        return text

    for k, v in route.get("replace", {}).items():
        text = text.replace(k, v)

    for w in route.get("clean_words", []):
        text = text.replace(w, "")

    text = f"{route.get('prefix','')}{text}{route.get('suffix','')}"
    return text

# ==============================
# WORKER
# ==============================

def get_key(r):
    return f"{r['source_chat']}_{r['source_topic']}_{r['dest_chat']}_{r['dest_topic']}"

def worker(route):
    key = get_key(route)
    q = route_queues[key]

    while True:
        msg = q.get()
        try:
            time.sleep(route.get("delay", 0))

            caption = process_text(msg.caption or msg.text, route)

            if msg.content_type == "text":
                bot.send_message(route["dest_chat"], caption or msg.text)

            else:
                bot.copy_message(
                    route["dest_chat"],
                    msg.chat.id,
                    msg.message_id,
                    message_thread_id=route["dest_topic"],
                    caption=caption if caption else None
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
    kb.add(InlineKeyboardButton("➕ Add Route", callback_data="add"))
    kb.add(InlineKeyboardButton("📡 Routes", callback_data="routes"))
    return kb

def routes_menu():
    kb = InlineKeyboardMarkup()
    for i in range(len(ROUTES)):
        kb.add(InlineKeyboardButton(f"Route {i+1}", callback_data=f"r_{i}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="main"))
    return kb

# ==============================
# START
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "⚡ TSD HUB READY", reply_markup=main_menu())

# ==============================
# ADD ROUTE COMMAND (SAFE)
# ==============================

@bot.message_handler(commands=['addroute'])
def addroute_cmd(m):
    user_sessions[m.from_user.id] = {"mode": "add"}
    bot.reply_to(m, "Paste full route:\n/addroute src src_topic dest dest_topic delay")

# ==============================
# CALLBACK
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):

    if call.data == "routes":
        bot.edit_message_text("📡 ROUTES", call.message.chat.id, call.message.message_id, reply_markup=routes_menu())

    elif call.data == "main":
        bot.edit_message_text("MAIN MENU", call.message.chat.id, call.message.message_id, reply_markup=main_menu())

# ==============================
# MAIN HANDLER
# ==============================

@bot.message_handler(func=lambda m: True, content_types=['text','photo','video','document'])
def handler(m):

    uid = m.from_user.id

    # ================= SESSION =================
    if uid in user_sessions:

        s = user_sessions[uid]

        try:
            if s["mode"] == "add":

                parts = m.text.split()

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
                ensure_worker(route)

                bot.reply_to(m, "✅ Route Added")
                user_sessions.pop(uid)

        except Exception as e:
            bot.reply_to(m, f"Error: {e}")
            user_sessions.pop(uid)

        return  # IMPORTANT: stop here

    # ================= AUTOFORWARD =================

    # ❌ Ignore commands
    if m.text and m.text.startswith("/"):
        return

    # ❌ Ignore bot messages
    if m.from_user and m.from_user.is_bot:
        return

    for r in ROUTES:

        if m.chat.id != r["source_chat"]:
            continue

        if r["source_topic"] is not None:
            if getattr(m, "message_thread_id", None) != r["source_topic"]:
                continue

        ensure_worker(r)
        route_queues[get_key(r)].put(m)

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
