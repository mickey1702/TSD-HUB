import os
import json
import telebot
import threading
import time
from queue import Queue
from flask import Flask, request

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=False)
app = Flask(__name__)

ROUTE_FILE = "routes.json"
SESSION_FILE = "sessions.json"

ROUTES = []
user_sessions = {}
user_batches = {}

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

def load_sessions():
    try:
        with open(SESSION_FILE, "r") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except:
        return {}

def save_sessions():
    with open(SESSION_FILE, "w") as f:
        json.dump({str(k): v for k, v in user_sessions.items()}, f, indent=4)

ROUTES = load_routes()
user_sessions = load_sessions()

# 🔥 IMPORTANT: restart workers for saved routes
for r in ROUTES:
    def init_worker(route):
        key = f"{route['source_chat']}_{route['source_topic']}_{route['dest_chat']}_{route['dest_topic']}"
        if key not in route_queues:
            route_queues[key] = Queue()
        if key not in route_workers:
            t = threading.Thread(target=worker, args=(route,), daemon=True)
            t.start()
            route_workers[key] = t
    try:
        init_worker(r)
    except:
        pass

# ==============================
# UI PANEL
# ==============================

def main_panel():
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🚀 Start Autoforward", callback_data="autoforward"),
        InlineKeyboardButton("🧠 Start Batch Mode", callback_data="batch"),
        InlineKeyboardButton("📡 View Routes", callback_data="viewroutes"),
        InlineKeyboardButton("🗑 Clear Routes", callback_data="clearroutes")
    )
    return kb

# ==============================
# WORKER ENGINE
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

            if route["dest_topic"] is not None:
                bot.copy_message(
                    route["dest_chat"],
                    msg.chat.id,
                    msg.message_id,
                    message_thread_id=route["dest_topic"]
                )
            else:
                bot.copy_message(
                    route["dest_chat"],
                    msg.chat.id,
                    msg.message_id
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

# ==============================
# START
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "TSD HUB CONTROL PANEL", reply_markup=main_panel())

# ==============================
# CALLBACKS
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    uid = call.from_user.id

    if call.data == "viewroutes":
        if not ROUTES:
            bot.send_message(call.message.chat.id, "No routes")
            return

        txt = "📡 ROUTES:\n\n"
        for i, r in enumerate(ROUTES, 1):
            txt += (
                f"{i}.\n"
                f"SRC: {r['source_chat']} | {r['source_topic']}\n"
                f"DST: {r['dest_chat']} | {r['dest_topic']}\n"
                f"Delay: {r['delay']} sec\n\n"
            )
        bot.send_message(call.message.chat.id, txt)

    elif call.data == "clearroutes":
        ROUTES.clear()
        save_routes()
        bot.send_message(call.message.chat.id, "🗑 All routes cleared")

    elif call.data == "autoforward":
        user_sessions[uid] = {"mode": "af1"}
        save_sessions()
        bot.send_message(call.message.chat.id, "🚀 Autoforward Mode Started\nSend SOURCE CHAT ID")

    elif call.data == "batch":
        user_batches[uid] = []
        user_sessions[uid] = {"mode": "batch"}
        save_sessions()
        bot.send_message(call.message.chat.id, "🧠 Batch Mode Started\nSend files, then /done")

# ==============================
# SESSION HANDLER
# ==============================

def handle_session(m):
    uid = m.from_user.id

    if uid not in user_sessions:
        return False

    s = user_sessions[uid]

    try:
        # AUTOFORWARD SETUP
        if s["mode"] == "af1":
            s["src"] = int(m.text)
            s["mode"] = "af2"
            bot.reply_to(m, "Source topic or none")

        elif s["mode"] == "af2":
            s["src_t"] = None if m.text.lower() == "none" else int(m.text)
            s["mode"] = "af3"
            bot.reply_to(m, "Destination chat")

        elif s["mode"] == "af3":
            s["dst"] = int(m.text)
            s["mode"] = "af4"
            bot.reply_to(m, "Destination topic or none")

        elif s["mode"] == "af4":
            s["dst_t"] = None if m.text.lower() == "none" else int(m.text)
            s["mode"] = "af5"
            bot.reply_to(m, "Delay seconds")

        elif s["mode"] == "af5":
            route = {
                "source_chat": s["src"],
                "source_topic": s["src_t"],
                "dest_chat": s["dst"],
                "dest_topic": s["dst_t"],
                "delay": int(m.text),
                "enabled": True
            }

            ROUTES.append(route)
            save_routes()
            ensure_worker(route)

            bot.reply_to(m, "✅ Autoforward Activated")

            user_sessions.pop(uid)
            save_sessions()

        # BATCH MODE
        elif s["mode"] == "batch":
            user_batches[uid].append(m)
            bot.reply_to(m, f"Added ({len(user_batches[uid])})")

        elif s["mode"] == "batch_sort":
            batch = user_batches[uid]

            if m.text == "2":
                batch.sort(key=lambda x: (x.caption or "").lower())

            s["mode"] = "batch_dest"
            bot.reply_to(m, "Send DESTINATION CHAT ID")

        elif s["mode"] == "batch_dest":
            s["dest"] = int(m.text)
            s["mode"] = "batch_topic"
            bot.reply_to(m, "Send DESTINATION TOPIC or none")

        elif s["mode"] == "batch_topic":
            dest_topic = None if m.text.lower() == "none" else int(m.text)
            dest = s["dest"]

            batch = user_batches.get(uid, [])

            bot.reply_to(m, f"Sending {len(batch)} files...")

            for msg in batch:
                try:
                    if dest_topic:
                        bot.copy_message(dest, msg.chat.id, msg.message_id, message_thread_id=dest_topic)
                    else:
                        bot.copy_message(dest, msg.chat.id, msg.message_id)
                except Exception as e:
                    print(e)

            bot.reply_to(m, "✅ Batch Completed")

            user_batches.pop(uid, None)
            user_sessions.pop(uid, None)
            save_sessions()

    except Exception as e:
        bot.reply_to(m, f"Error: {e}")
        user_sessions.pop(uid, None)
        save_sessions()

    return True

# ==============================
# DONE
# ==============================

@bot.message_handler(commands=['done'])
def done(m):
    uid = m.from_user.id

    if uid not in user_batches or not user_batches[uid]:
        bot.reply_to(m, "No files")
        return

    user_sessions[uid] = {"mode": "batch_sort"}
    save_sessions()

    bot.reply_to(m, "Choose order:\n1 = As Uploaded\n2 = Alphabetical")

# ==============================
# RELAY ENGINE (FIXED)
# ==============================

@bot.message_handler(func=lambda m: True, content_types=['text','photo','video','document'])
def handler(m):
    if handle_session(m):
        return

    src = m.chat.id
    topic = getattr(m, "message_thread_id", None)

    for r in ROUTES:
        if src != r["source_chat"]:
            continue

        if r["source_topic"] not in [None, topic]:
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
