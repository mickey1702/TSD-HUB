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

ROUTES = load_routes()

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

# restart workers
for r in ROUTES:
    ensure_worker(r)

# ==============================
# START
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "TSD HUB READY")

# ==============================
# SESSION HANDLER
# ==============================

def handle_session(m):

    # 🔥 FIX: skip channel posts
    if m.from_user is None:
        return False

    uid = m.from_user.id

    if uid not in user_sessions:
        return False

    s = user_sessions[uid]

    try:
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
            bot.reply_to(m, "Delay")

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

            bot.reply_to(m, "✅ Route Added")
            user_sessions.pop(uid)

    except Exception as e:
        bot.reply_to(m, f"Error: {e}")
        user_sessions.pop(uid, None)

    return True

# ==============================
# ADD ROUTE COMMAND
# ==============================

@bot.message_handler(commands=['addroute'])
def addroute(m):
    user_sessions[m.from_user.id] = {"mode": "af1"}
    bot.reply_to(m, "Send SOURCE CHAT ID")

# ==============================
# RELAY ENGINE (FINAL)
# ==============================

def process_relay(m):

    src = m.chat.id
    topic = getattr(m, "message_thread_id", None)

    print("RECEIVED:", src, topic)

    for r in ROUTES:

        if not r.get("enabled", True):
            continue

        if src != r["source_chat"]:
            continue

        if r["source_topic"] is not None:
            if topic != r["source_topic"]:
                continue

        print("MATCHED:", r)

        ensure_worker(r)
        route_queues[get_key(r)].put(m)

# ==============================
# HANDLERS
# ==============================

@bot.message_handler(func=lambda m: True, content_types=['text','photo','video','document'])
def handler(m):
    if handle_session(m):
        return
    process_relay(m)

@bot.channel_post_handler(content_types=['text','photo','video','document'])
def channel_handler(m):
    process_relay(m)

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
