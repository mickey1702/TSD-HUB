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
# TEXT ENGINE
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
# RENAME ENGINE
# ==============================

def apply_template(filename, route):
    template = route.get("template", "{filename}")
    return template.replace("{filename}", filename)

# ==============================
# WORKER (AUTOFORWARD CORE)
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

            # PROCESS
            if route["mode"] == "caption":
                caption = process_text(msg.caption or msg.text, route)

            elif route["mode"] == "filename" and msg.document:
                filename = process_text(msg.document.file_name, route)
                filename = apply_template(filename, route)

            # SEND
            if msg.content_type == "text":
                bot.send_message(route["dest_chat"], caption or msg.text)

            elif msg.document:
                file = bot.get_file(msg.document.file_id)
                data = bot.download_file(file.file_path)

                bot.send_document(
                    route["dest_chat"],
                    data,
                    visible_file_name=filename or msg.document.file_name,
                    caption=caption or msg.caption
                )

            else:
                bot.copy_message(
                    route["dest_chat"],
                    msg.chat.id,
                    msg.message_id,
                    message_thread_id=route["dest_topic"],
                    caption=caption
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
    kb.add(InlineKeyboardButton("🧠 Batch", callback_data="batch"))
    return kb

def routes_menu():
    kb = InlineKeyboardMarkup()
    for i in range(len(ROUTES)):
        kb.add(InlineKeyboardButton(f"Route {i+1}", callback_data=f"r_{i}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="main"))
    return kb

def route_panel(i):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚙️ Processing", callback_data=f"p_{i}"),
        InlineKeyboardButton("🖼 Thumbnail", callback_data=f"thumb_{i}")
    )
    kb.add(
        InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{i}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"del_{i}")
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="routes"))
    return kb

def processing_panel(i):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔁 Replace", callback_data=f"rep_{i}"),
        InlineKeyboardButton("❌ Remove", callback_data=f"cln_{i}")
    )
    kb.add(
        InlineKeyboardButton("✏️ Prefix", callback_data=f"pre_{i}"),
        InlineKeyboardButton("🔚 Suffix", callback_data=f"suf_{i}")
    )
    kb.add(
        InlineKeyboardButton("⚙️ Mode", callback_data=f"mode_{i}")
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"r_{i}"))
    return kb

# ==============================
# START
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "⚡ TSD HUB READY", reply_markup=main_menu())

# ==============================
# CALLBACK
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    uid = call.from_user.id
    d = call.data

    if d == "main":
        bot.edit_message_text("Main Menu", call.message.chat.id, call.message.message_id, reply_markup=main_menu())

    elif d == "routes":
        bot.edit_message_text("Routes", call.message.chat.id, call.message.message_id, reply_markup=routes_menu())

    elif d == "add":
        user_sessions[uid] = {"mode": "add"}
        bot.send_message(call.message.chat.id, "Send:\n/addroute src src_topic dest dest_topic delay")

    elif d.startswith("r_"):
        i = int(d.split("_")[1])
        bot.edit_message_text(f"Route {i+1}", call.message.chat.id, call.message.message_id, reply_markup=route_panel(i))

    elif d.startswith("del_"):
        i = int(d.split("_")[1])
        ROUTES.pop(i)
        save_routes()
        bot.answer_callback_query(call.id, "Deleted")

    elif d.startswith("p_"):
        i = int(d.split("_")[1])
        bot.edit_message_text("Processing", call.message.chat.id, call.message.message_id, reply_markup=processing_panel(i))

    elif d.startswith("mode_"):
        i = int(d.split("_")[1])
        ROUTES[i]["mode"] = "filename" if ROUTES[i]["mode"] == "caption" else "caption"
        save_routes()
        bot.answer_callback_query(call.id, ROUTES[i]["mode"])

    elif d.startswith("pre_"):
        user_sessions[uid] = {"mode": "prefix", "r": int(d.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send prefix")

    elif d.startswith("suf_"):
        user_sessions[uid] = {"mode": "suffix", "r": int(d.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send suffix")

    elif d.startswith("rep_"):
        user_sessions[uid] = {"mode": "replace", "r": int(d.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send old new")

    elif d.startswith("cln_"):
        user_sessions[uid] = {"mode": "clean", "r": int(d.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send word")

    elif d.startswith("rename_"):
        user_sessions[uid] = {"mode": "template", "r": int(d.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send template like:\n{filename}_TSD")

    elif d == "batch":
        user_batches[uid] = []
        user_sessions[uid] = {"mode": "batch"}
        bot.send_message(call.message.chat.id, "Send files then /done")

# ==============================
# MESSAGE HANDLER
# ==============================

@bot.message_handler(func=lambda m: True, content_types=['text','photo','video','document'])
def handler(m):

    uid = m.from_user.id

    # SESSION
    if uid in user_sessions:
        s = user_sessions[uid]

        try:
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
                    "delay": delay,
                    "mode": "caption",
                    "prefix": "",
                    "suffix": "",
                    "replace": {},
                    "clean_words": [],
                    "template": "{filename}",
                    "thumbnail": None
                }

                ROUTES.append(route)
                save_routes()
                ensure_worker(route)

                bot.reply_to(m, "Route Added")
                user_sessions.pop(uid)

            elif s["mode"] == "prefix":
                ROUTES[s["r"]]["prefix"] = m.text

            elif s["mode"] == "suffix":
                ROUTES[s["r"]]["suffix"] = m.text

            elif s["mode"] == "replace":
                a,b = m.text.split()
                ROUTES[s["r"]]["replace"][a] = b

            elif s["mode"] == "clean":
                ROUTES[s["r"]]["clean_words"].append(m.text)

            elif s["mode"] == "template":
                ROUTES[s["r"]]["template"] = m.text

            elif s["mode"] == "batch":
                user_batches[uid].append(m)

            save_routes()
            bot.reply_to(m, "Saved")
            user_sessions.pop(uid)

        except Exception as e:
            bot.reply_to(m, f"Error: {e}")
            user_sessions.pop(uid)

        return

    # AUTOFORWARD ENGINE
    for r in ROUTES:
        if m.chat.id == r["source_chat"]:
            if r["source_topic"] is None or getattr(m, "message_thread_id", None) == r["source_topic"]:
                ensure_worker(r)
                route_queues[get_key(r)].put(m)

# ==============================
# BATCH COMMANDS
# ==============================

@bot.message_handler(commands=['done'])
def done(m):
    uid = m.from_user.id
    bot.send_message(m.chat.id, "Send destination chat ID")
    user_sessions[uid] = {"mode": "batch_send"}

@bot.message_handler(commands=['list'])
def list_cmd(m):
    batch = user_batches.get(m.from_user.id, [])
    txt = "\n".join([f"{i+1}. {x.caption or 'file'}" for i,x in enumerate(batch)])
    bot.reply_to(m, txt or "Empty")

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
