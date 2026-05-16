import os
import json
import telebot
import traceback
import threading
import time
from queue import Queue
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=False)
app = Flask(__name__)

ROUTE_FILE = "routes.json"
SESSION_FILE = "sessions.json"

recent_relays = set()
route_queues = {}
route_workers = {}

user_sessions = {}
user_batches = {}

# ================= FILE SYSTEM =================

def load_routes():
    try:
        with open(ROUTE_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_routes():
    with open(ROUTE_FILE, "w") as f:
        json.dump(ROUTES, f, indent=4)

def load_sessions():
    try:
        with open(SESSION_FILE, "r") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    except:
        return {}

def save_sessions():
    with open(SESSION_FILE, "w") as f:
        json.dump({str(k): v for k, v in user_sessions.items()}, f, indent=4)

ROUTES = load_routes()
user_sessions = load_sessions()

# ================= UI =================

def main_panel():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add Route", callback_data="addroute"),
        InlineKeyboardButton("📡 Routes", callback_data="routes"),
        InlineKeyboardButton("📦 Batch Mode", callback_data="batch")
    )
    return kb

def batch_buttons():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🔤 Alphanumeric Order", callback_data="order_alpha"),
        InlineKeyboardButton("📥 Upload Order", callback_data="order_upload"),
        InlineKeyboardButton("▶️ Process Now", callback_data="process_batch")
    )
    return kb

# ================= START =================

@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.send_message(message.chat.id, "⚡ TSD HUB READY", reply_markup=main_panel())

# ================= CALLBACK =================

@bot.callback_query_handler(func=lambda call: True)
def callback_router(call):
    uid = call.from_user.id

    if call.data == "batch":
        user_sessions[uid] = {"mode": "batch"}
        save_sessions()
        bot.send_message(call.message.chat.id, "📦 Send files now. Then type /done")

    elif call.data == "routes":
        if not ROUTES:
            bot.send_message(call.message.chat.id, "No routes.")
            return

        txt = ""
        for i, r in enumerate(ROUTES, 1):
            txt += f"{i}. {r['source_chat']} → {r['dest_chat']}\n"
        bot.send_message(call.message.chat.id, txt)

    elif call.data == "order_alpha":
        user_sessions[uid]["order"] = "alpha"
        save_sessions()
        bot.answer_callback_query(call.id, "Alphabetical selected")

    elif call.data == "order_upload":
        user_sessions[uid]["order"] = "upload"
        save_sessions()
        bot.answer_callback_query(call.id, "Upload order selected")

    elif call.data == "process_batch":
        process_batch(uid, call.message)

# ================= ROUTE ENGINE =================

def get_key(route):
    return f"{route['source_chat']}_{route['dest_chat']}"

def worker(route):
    key = get_key(route)
    q = route_queues[key]

    while True:
        msg = q.get()
        try:
            if route.get("delay"):
                time.sleep(route["delay"])

            bot.copy_message(
                route["dest_chat"],
                msg.chat.id,
                msg.message_id
            )
        except Exception as e:
            print(e)
        q.task_done()

def ensure_worker(route):
    key = get_key(route)

    if key not in route_queues:
        route_queues[key] = Queue()

    if key not in route_workers:
        t = threading.Thread(target=worker, args=(route,), daemon=True)
        t.start()
        route_workers[key] = t

def process_relay(message):
    for r in ROUTES:
        if message.chat.id == r["source_chat"]:
            ensure_worker(r)
            route_queues[get_key(r)].put(message)

# ================= BATCH =================

@bot.message_handler(commands=['done'])
def done_batch(message):
    uid = message.from_user.id

    if uid not in user_batches:
        bot.reply_to(message, "No files")
        return

    user_sessions[uid] = {"mode": "confirm", "order": "upload"}
    save_sessions()

    bot.send_message(
        message.chat.id,
        f"{len(user_batches[uid])} files ready",
        reply_markup=batch_buttons()
    )

def process_batch(uid, message):
    batch = user_batches.get(uid, [])
    order = user_sessions[uid].get("order", "upload")

    if order == "alpha":
        batch.sort(key=lambda m: (m.caption or "").lower())

    for msg in batch:
        process_relay(msg)

    bot.send_message(message.chat.id, "✅ Done")

    user_batches.pop(uid, None)
    user_sessions.pop(uid, None)
    save_sessions()

@bot.message_handler(commands=['list'])
def list_batch(message):
    uid = message.from_user.id

    if uid not in user_batches:
        bot.reply_to(message, "No batch")
        return

    txt = ""
    for i, m in enumerate(user_batches[uid], 1):
        txt += f"{i}. {(m.caption or 'file')[:40]}\n"

    bot.reply_to(message, txt)

# ================= ROUTE COMMAND =================

@bot.message_handler(commands=['addroute'])
def add_route(message):
    try:
        p = message.text.split()
        route = {
            "source_chat": int(p[1]),
            "dest_chat": int(p[3]),
            "delay": int(p[5])
        }
        ROUTES.append(route)
        save_routes()
        bot.reply_to(message, "Route added")
    except:
        bot.reply_to(message, "Format:\n/addroute source none dest none delay")

# ================= HANDLER =================

@bot.message_handler(func=lambda m: True, content_types=[
    'text','photo','video','document','audio','voice','sticker'
])
def handler(message):

    uid = message.from_user.id

    if uid in user_sessions and user_sessions[uid].get("mode") == "batch":
        if uid not in user_batches:
            user_batches[uid] = []
        user_batches[uid].append(message)
        bot.reply_to(message, f"Added ({len(user_batches[uid])})")
        return

    process_relay(message)

# ================= WEBHOOK =================

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
        return "ok", 200
    except:
        traceback.print_exc()
        return "error", 500

@app.route("/")
def home():
    return "TSD HUB Running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
