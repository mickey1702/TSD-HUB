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
# CAPTION ENGINE
# ==============================

def process_caption(text, route):
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

            caption = ""

            if hasattr(msg, "caption") and msg.caption:
                caption = process_caption(msg.caption, route)

            elif hasattr(msg, "text") and msg.text:
                caption = process_caption(msg.text, route)

            if msg.content_type == "text":
                if route["dest_topic"]:
                    bot.send_message(route["dest_chat"], caption, message_thread_id=route["dest_topic"])
                else:
                    bot.send_message(route["dest_chat"], caption)

            else:
                if route["dest_topic"]:
                    bot.copy_message(
                        route["dest_chat"],
                        msg.chat.id,
                        msg.message_id,
                        message_thread_id=route["dest_topic"],
                        caption=caption if caption else None
                    )
                else:
                    bot.copy_message(
                        route["dest_chat"],
                        msg.chat.id,
                        msg.message_id,
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
        InlineKeyboardButton("🧠 Batch Mode", callback_data="batch"),
        InlineKeyboardButton("❌ Delete Route", callback_data="delroute")
    )
    return kb

# ==============================
# START / HELP
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "⚡ TSD HUB READY", reply_markup=main_menu())

@bot.message_handler(commands=['help'])
def help_cmd(m):
    bot.reply_to(m, """
📘 COMMANDS

/addroute → create route  
/routes → view routes  
/delroute 1 → delete route  

/setprefix 1 TEXT  
/setsuffix 1 TEXT  
/replace 1 old new  
/clean 1 word  

Batch:
/done  
/list  

⚙️ Bot must be admin in source & destination
""")

# ==============================
# CALLBACKS
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    uid = call.from_user.id

    if call.data == "af":
        user_sessions[uid] = {"mode": "af1"}
        bot.send_message(call.message.chat.id, "Send SOURCE CHAT ID")

    elif call.data == "routes":
        if not ROUTES:
            bot.send_message(call.message.chat.id, "No routes")
            return

        txt = "📡 ROUTES:\n\n"
        for i, r in enumerate(ROUTES, 1):
            txt += f"{i}. {r['source_chat']} → {r['dest_chat']}\n"
        bot.send_message(call.message.chat.id, txt)

    elif call.data == "batch":
        user_batches[uid] = []
        user_sessions[uid] = {"mode": "batch"}
        bot.send_message(call.message.chat.id, "Send files then /done")

    elif call.data == "delroute":
        bot.send_message(call.message.chat.id, "Use /delroute 1")

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
            bot.reply_to(m, "Delay seconds")

        elif s["mode"] == "af5":
            route = {
                "source_chat": s["src"],
                "source_topic": s["src_t"],
                "dest_chat": s["dst"],
                "dest_topic": s["dst_t"],
                "delay": int(m.text),
                "enabled": True,
                "prefix": "",
                "suffix": "",
                "replace": {},
                "clean_words": []
            }

            ROUTES.append(route)
            save_routes()
            ensure_worker(route)

            bot.reply_to(m, "✅ Autoforward Activated")
            user_sessions.pop(uid)

        elif s["mode"] == "batch":
            user_batches[uid].append(m)

        elif s["mode"] == "batch_sort":
            batch = user_batches[uid]

            if m.text == "2":
                batch.sort(key=lambda x: (x.caption or "").lower())

            s["mode"] = "batch_dest"
            bot.reply_to(m, "Send DEST CHAT ID")

        elif s["mode"] == "batch_dest":
            s["dest"] = int(m.text)
            s["mode"] = "batch_topic"
            bot.reply_to(m, "Send TOPIC or none")

        elif s["mode"] == "batch_topic":
            topic = None if m.text.lower() == "none" else int(m.text)
            dest = s["dest"]

            for msg in user_batches[uid]:
                if topic:
                    bot.copy_message(dest, msg.chat.id, msg.message_id, message_thread_id=topic)
                else:
                    bot.copy_message(dest, msg.chat.id, msg.message_id)

            bot.reply_to(m, "✅ Batch Completed")

            user_batches.pop(uid)
            user_sessions.pop(uid)

    except Exception as e:
        bot.reply_to(m, f"Error: {e}")
        user_sessions.pop(uid, None)

    return True

# ==============================
# COMMANDS
# ==============================

@bot.message_handler(commands=['addroute'])
def addroute(m):
    user_sessions[m.from_user.id] = {"mode": "af1"}
    bot.reply_to(m, "Send SOURCE CHAT ID")

@bot.message_handler(commands=['routes'])
def routes_cmd(m):
    if not ROUTES:
        bot.reply_to(m, "No routes")
        return

    txt = ""
    for i, r in enumerate(ROUTES, 1):
        txt += f"{i}. {r['source_chat']} → {r['dest_chat']}\n"
    bot.reply_to(m, txt)

@bot.message_handler(commands=['delroute'])
def delroute(m):
    try:
        num = int(m.text.split()[1]) - 1
        ROUTES.pop(num)
        save_routes()
        bot.reply_to(m, "Deleted")
    except:
        bot.reply_to(m, "Usage /delroute 1")

# ==============================
# CAPTION COMMANDS
# ==============================

@bot.message_handler(commands=['setprefix'])
def setprefix(m):
    parts = m.text.split()
    ROUTES[int(parts[1])-1]["prefix"] = " ".join(parts[2:])
    save_routes()
    bot.reply_to(m, "Prefix set")

@bot.message_handler(commands=['setsuffix'])
def setsuffix(m):
    parts = m.text.split()
    ROUTES[int(parts[1])-1]["suffix"] = " ".join(parts[2:])
    save_routes()
    bot.reply_to(m, "Suffix set")

@bot.message_handler(commands=['replace'])
def replace_cmd(m):
    parts = m.text.split()
    ROUTES[int(parts[1])-1]["replace"][parts[2]] = parts[3]
    save_routes()
    bot.reply_to(m, "Replace added")

@bot.message_handler(commands=['clean'])
def clean_cmd(m):
    parts = m.text.split()
    ROUTES[int(parts[1])-1]["clean_words"].append(parts[2])
    save_routes()
    bot.reply_to(m, "Clean word added")

# ==============================
# BATCH COMMANDS
# ==============================

@bot.message_handler(commands=['done'])
def done(m):
    user_sessions[m.from_user.id] = {"mode": "batch_sort"}
    bot.reply_to(m, "1 Normal\n2 Alphabetical")

@bot.message_handler(commands=['list'])
def list_cmd(m):
    batch = user_batches.get(m.from_user.id, [])
    txt = ""
    for i, msg in enumerate(batch, 1):
        txt += f"{i}. {msg.caption or 'file'}\n"
    bot.reply_to(m, txt or "Empty")

# ==============================
# RELAY
# ==============================

def process_relay(m):

    src = m.chat.id
    topic = getattr(m, "message_thread_id", None)

    for r in ROUTES:

        if not r.get("enabled", True):
            continue

        if src != r["source_chat"]:
            continue

        if r["source_topic"] is not None and topic != r["source_topic"]:
            continue

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
