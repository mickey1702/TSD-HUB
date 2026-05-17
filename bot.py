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
# TEXT PROCESSOR
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

            caption = None
            filename = None

            # MODE: caption OR filename
            if route.get("mode") == "caption":
                if msg.caption:
                    caption = process_text(msg.caption, route)
                elif msg.text:
                    caption = process_text(msg.text, route)

            elif route.get("mode") == "filename":
                if msg.document:
                    filename = process_text(msg.document.file_name, route)

            # TEXT
            if msg.content_type == "text":
                bot.send_message(route["dest_chat"], caption or msg.text)

            # DOCUMENT (file rename)
            elif msg.document:
                file = bot.get_file(msg.document.file_id)
                downloaded = bot.download_file(file.file_path)

                bot.send_document(
                    route["dest_chat"],
                    downloaded,
                    visible_file_name=filename or msg.document.file_name,
                    caption=caption or msg.caption
                )

            # OTHER MEDIA
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
# MENU
# ==============================

def main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🚀 Autoforward", callback_data="af"),
        InlineKeyboardButton("📡 Routes", callback_data="routes"),
        InlineKeyboardButton("🧠 Batch", callback_data="batch")
    )
    return kb

def process_menu(idx):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ Prefix", callback_data=f"prefix_{idx}"),
        InlineKeyboardButton("🔚 Suffix", callback_data=f"suffix_{idx}"),
        InlineKeyboardButton("🔁 Replace", callback_data=f"replace_{idx}"),
        InlineKeyboardButton("❌ Remove", callback_data=f"clean_{idx}"),
        InlineKeyboardButton("⚙️ Mode", callback_data=f"mode_{idx}")
    )
    return kb

# ==============================
# START
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "⚡ TSD HUB READY", reply_markup=main_menu())

# ==============================
# CALLBACKS
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):

    uid = call.from_user.id
    data = call.data

    if data == "af":
        user_sessions[uid] = {"mode": "af1"}
        bot.send_message(call.message.chat.id, "Send SOURCE CHAT ID")

    elif data == "routes":
        if not ROUTES:
            bot.send_message(call.message.chat.id, "No routes")
            return

        for i, r in enumerate(ROUTES):
            txt = f"{i+1}. {r['source_chat']} → {r['dest_chat']}\nMode: {r.get('mode','caption')}"
            bot.send_message(call.message.chat.id, txt, reply_markup=process_menu(i))

    elif data == "batch":
        user_batches[uid] = []
        user_sessions[uid] = {"mode": "batch"}
        bot.send_message(call.message.chat.id, "Send files then /done")

    elif data.startswith("mode_"):
        idx = int(data.split("_")[1])
        ROUTES[idx]["mode"] = "filename" if ROUTES[idx]["mode"] == "caption" else "caption"
        save_routes()
        bot.answer_callback_query(call.id, f"Mode: {ROUTES[idx]['mode']}")

    elif data.startswith("prefix_"):
        user_sessions[uid] = {"mode": "setprefix", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send prefix")

    elif data.startswith("suffix_"):
        user_sessions[uid] = {"mode": "setsuffix", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send suffix")

    elif data.startswith("replace_"):
        user_sessions[uid] = {"mode": "replace", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send: old new")

    elif data.startswith("clean_"):
        user_sessions[uid] = {"mode": "clean", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send word")

# ==============================
# SESSION HANDLER
# ==============================

def handle_session(m):

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
                "enabled": True,
                "mode": "caption",
                "prefix": "",
                "suffix": "",
                "replace": {},
                "clean_words": []
            }

            ROUTES.append(route)
            save_routes()
            ensure_worker(route)

            bot.reply_to(m, "✅ Autoforward ON")
            user_sessions.pop(uid)

        elif s["mode"] == "setprefix":
            ROUTES[s["route"]]["prefix"] = m.text
            save_routes()
            bot.reply_to(m, "Prefix set")

        elif s["mode"] == "setsuffix":
            ROUTES[s["route"]]["suffix"] = m.text
            save_routes()
            bot.reply_to(m, "Suffix set")

        elif s["mode"] == "replace":
            old, new = m.text.split()
            ROUTES[s["route"]]["replace"][old] = new
            save_routes()
            bot.reply_to(m, "Replace added")

        elif s["mode"] == "clean":
            ROUTES[s["route"]]["clean_words"].append(m.text)
            save_routes()
            bot.reply_to(m, "Word removed")

        elif s["mode"] == "batch":
            user_batches[uid].append(m)

        elif s["mode"] == "batch_sort":
            if m.text == "2":
                user_batches[uid].sort(key=lambda x: (x.caption or "").lower())

            s["mode"] = "batch_dest"
            bot.reply_to(m, "Send DEST chat")

        elif s["mode"] == "batch_dest":
            s["dest"] = int(m.text)
            s["mode"] = "batch_topic"
            bot.reply_to(m, "Send topic or none")

        elif s["mode"] == "batch_topic":
            topic = None if m.text.lower() == "none" else int(m.text)

            for msg in user_batches[uid]:
                if topic:
                    bot.copy_message(s["dest"], msg.chat.id, msg.message_id, message_thread_id=topic)
                else:
                    bot.copy_message(s["dest"], msg.chat.id, msg.message_id)

            bot.reply_to(m, "✅ Batch done")
            user_batches.pop(uid)
            user_sessions.pop(uid)

    except Exception as e:
        bot.reply_to(m, f"Error: {e}")
        user_sessions.pop(uid, None)

    return True

# ==============================
# COMMANDS
# ==============================

@bot.message_handler(commands=['done'])
def done(m):
    user_sessions[m.from_user.id] = {"mode": "batch_sort"}
    bot.reply_to(m, "1 Normal\n2 Alphabetical")

@bot.message_handler(commands=['list'])
def list_cmd(m):
    batch = user_batches.get(m.from_user.id, [])
    txt = "\n".join([f"{i+1}. {x.caption or 'file'}" for i,x in enumerate(batch)])
    bot.reply_to(m, txt or "Empty")

# ==============================
# RELAY
# ==============================

def process_relay(m):
    for r in ROUTES:
        if m.chat.id == r["source_chat"]:
            if r["source_topic"] is None or getattr(m, "message_thread_id", None) == r["source_topic"]:
                ensure_worker(r)
                route_queues[get_key(r)].put(m)

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
