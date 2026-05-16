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
SESSION_FILE = "sessions.json"

route_queues = {}
route_workers = {}
user_sessions = {}
upload_batches = {}

# ================= LOAD/SAVE =================

def load_routes():
    try:
        with open(ROUTE_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_routes(routes):
    with open(ROUTE_FILE, "w") as f:
        json.dump(routes, f, indent=4)

ROUTES = load_routes()

# ================= PANEL =================

def main_panel():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add Route", callback_data="addroute"),
        InlineKeyboardButton("📡 Routes", callback_data="viewroutes"),
        InlineKeyboardButton("📦 Batch Mode", callback_data="batch"),
    )
    return kb

# ================= START =================

@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.send_message(message.chat.id, "⚡ TSD HUB READY", reply_markup=main_panel())

# ================= ROUTE WORKER =================

def route_worker(route):
    key = str(route)
    q = route_queues[key]

    while True:
        message = q.get()
        try:
            time.sleep(route.get("delay", 0))

            text = message.text or message.caption or ""

            # APPLY SMART ENGINE
            for k, v in route.get("replace_words", {}).items():
                text = text.replace(k, v)

            for w in route.get("remove_words", []):
                text = text.replace(w, "")

            text = route.get("prefix", "") + text + route.get("suffix", "")

            bot.send_message(route["dest_chat"], text)

        except Exception as e:
            print(e)

        q.task_done()

def ensure_worker(route):
    key = str(route)
    if key not in route_queues:
        route_queues[key] = Queue()
        t = threading.Thread(target=route_worker, args=(route,), daemon=True)
        t.start()
        route_workers[key] = t

# ================= RELAY =================

def process_relay(message):
    for route in ROUTES:
        if message.chat.id == route["source_chat"]:
            ensure_worker(route)
            route_queues[str(route)].put(message)

# ================= BATCH MODE =================

@bot.callback_query_handler(func=lambda c: c.data == "batch")
def batch_start(call):
    upload_batches[call.from_user.id] = []
    bot.send_message(call.message.chat.id, "📦 Send files now. Then press /done")

@bot.message_handler(content_types=['document','video','photo'])
def collect_files(message):
    uid = message.from_user.id

    if uid in upload_batches:
        upload_batches[uid].append(message)
        bot.reply_to(message, f"Added {len(upload_batches[uid])} files")

    else:
        process_relay(message)

@bot.message_handler(commands=['done'])
def done_upload(message):
    uid = message.from_user.id

    if uid not in upload_batches:
        return

    files = upload_batches[uid]

    txt1 = "📑 ALPHANUMERIC ORDER\n"
    txt2 = "\n📑 UPLOAD ORDER\n"

    names = []

    for i, msg in enumerate(files, 1):
        name = msg.document.file_name if msg.document else f"file_{i}"
        names.append(name)

    for i, n in enumerate(sorted(names), 1):
        txt1 += f"{i}. {n}\n"

    for i, n in enumerate(names, 1):
        txt2 += f"{i}. {n}\n"

    bot.send_message(message.chat.id, txt1 + txt2)

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("▶️ Process Now", callback_data="process_batch")
    )

    bot.send_message(message.chat.id, "Confirm processing", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "process_batch")
def process_batch(call):
    uid = call.from_user.id

    files = upload_batches.get(uid, [])

    for msg in files:
        process_relay(msg)

    upload_batches.pop(uid, None)

    bot.send_message(call.message.chat.id, "✅ Batch processed")

# ================= ROUTES =================

@bot.message_handler(commands=['addroute'])
def add_route(message):
    parts = message.text.split()

    new_route = {
        "source_chat": int(parts[1]),
        "source_topic": None,
        "dest_chat": int(parts[3]),
        "dest_topic": None,
        "delay": int(parts[5]),

        "enabled": True,
        "prefix": "",
        "suffix": "",
        "replace_words": {},
        "remove_words": []
    }

    ROUTES.append(new_route)
    save_routes(ROUTES)

    bot.reply_to(message, "Route added")

@bot.message_handler(commands=['routes'])
def show_routes(message):
    txt = ""
    for i, r in enumerate(ROUTES, 1):
        txt += f"{i}. {r['source_chat']} → {r['dest_chat']}\n"
    bot.reply_to(message, txt or "No routes")

# ================= MAIN =================

@bot.message_handler(func=lambda m: True)
def handler(message):
    process_relay(message)

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "ok"

@app.route("/")
def home():
    return "TSD HUB Running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
