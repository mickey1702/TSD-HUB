import os
import json
import telebot
import threading
import time
import re
from queue import Queue
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=False)
app = Flask(__name__)

ROUTE_FILE = "routes.json"
SETTINGS_FILE = "user_settings.json"

ROUTES = []
user_sessions = {}
user_batches = {}
route_queues = {}
route_workers = {}
user_settings = {}  # per-user settings

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

def load_settings():
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(user_settings, f, indent=4)

ROUTES = load_routes()
user_settings = load_settings()

def get_user_settings(uid):
    uid = str(uid)
    if uid not in user_settings:
        user_settings[uid] = {
            "prefix": "",
            "suffix": "",
            "rename_template": "",       # e.g. "SarcasticDr {filename}"
            "caption": "",               # custom caption template
            "thumbnail_file_id": None,   # stored Telegram file_id
            "send_as_document": False,
            "replace_words": {},         # {"old": "new", ...}
            "destination": None,         # default dest chat id
            "dest_topic": None,
        }
        save_settings()
    return user_settings[uid]

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
                bot.copy_message(route["dest_chat"], msg.chat.id, msg.message_id,
                                 message_thread_id=route["dest_topic"])
            else:
                bot.copy_message(route["dest_chat"], msg.chat.id, msg.message_id)
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
# FILE PROCESSING HELPERS
# ==============================

def apply_rename(filename, uid):
    s = get_user_settings(uid)

    # Apply replace/remove words first
    for old, new in s.get("replace_words", {}).items():
        filename = filename.replace(old, new)

    # Apply template if set
    if s["rename_template"]:
        # Strip extension for template use
        name, ext = os.path.splitext(filename)
        filename = s["rename_template"].replace("{filename}", name).replace("{original}", name)
        filename = filename + ext

    # Apply prefix/suffix (around name, preserve extension)
    name, ext = os.path.splitext(filename)
    if s["prefix"]:
        name = s["prefix"] + name
    if s["suffix"]:
        name = name + s["suffix"]

    return name + ext

def apply_caption(original_caption, filename, uid):
    s = get_user_settings(uid)
    if s["caption"]:
        cap = s["caption"]
        cap = cap.replace("{filename}", filename)
        cap = cap.replace("{original}", original_caption or "")
        return cap
    return original_caption

def send_processed_file(uid, msg, dest_chat, dest_topic=None):
    """
    Download, rename, re-upload with custom caption + thumbnail.
    Handles document, photo, video.
    """
    s = get_user_settings(uid)

    content_type = msg.content_type
    original_caption = msg.caption or ""

    # ---- Determine file info ----
    file_id = None
    original_filename = "file"
    mime_type = None

    if content_type == "document":
        file_id = msg.document.file_id
        original_filename = msg.document.file_name or "file"
        mime_type = msg.document.mime_type
    elif content_type == "video":
        file_id = msg.video.file_id
        original_filename = f"video_{msg.video.file_unique_id}.mp4"
        mime_type = "video/mp4"
    elif content_type == "photo":
        file_id = msg.photo[-1].file_id
        original_filename = f"photo_{msg.photo[-1].file_unique_id}.jpg"
        mime_type = "image/jpeg"
    else:
        # Unsupported type — just copy
        if dest_topic:
            bot.copy_message(dest_chat, msg.chat.id, msg.message_id, message_thread_id=dest_topic)
        else:
            bot.copy_message(dest_chat, msg.chat.id, msg.message_id)
        return

    new_filename = apply_rename(original_filename, uid)
    new_caption = apply_caption(original_caption, new_filename, uid)

    # ---- Download file ----
    file_info = bot.get_file(file_id)
    downloaded = bot.download_file(file_info.file_path)
    tmp_path = f"/tmp/{new_filename}"
    with open(tmp_path, "wb") as f:
        f.write(downloaded)

    # ---- Thumbnail ----
    thumb = None
    if s["thumbnail_file_id"]:
        try:
            thumb_info = bot.get_file(s["thumbnail_file_id"])
            thumb_bytes = bot.download_file(thumb_info.file_path)
            thumb_path = "/tmp/thumb.jpg"
            with open(thumb_path, "wb") as tf:
                tf.write(thumb_bytes)
            thumb = open(thumb_path, "rb")
        except:
            thumb = None

    # ---- Upload ----
    try:
        with open(tmp_path, "rb") as f:
            kwargs = dict(
                chat_id=dest_chat,
                caption=new_caption or None,
                parse_mode="HTML"
            )
            if dest_topic:
                kwargs["message_thread_id"] = dest_topic

            if s["send_as_document"] or content_type == "document":
                kwargs["document"] = f
                kwargs["visible_file_name"] = new_filename
                if thumb:
                    kwargs["thumb"] = thumb
                bot.send_document(**kwargs)
            elif content_type == "video":
                kwargs["video"] = f
                if thumb:
                    kwargs["thumb"] = thumb
                bot.send_video(**kwargs)
            elif content_type == "photo":
                kwargs["photo"] = f
                bot.send_photo(**kwargs)
    except Exception as e:
        print("Upload error:", e)
        raise
    finally:
        if thumb:
            thumb.close()
        try:
            os.remove(tmp_path)
        except:
            pass

# ==============================
# MENUS
# ==============================

def main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🚀 Autoforward", callback_data="af"),
        InlineKeyboardButton("📡 Routes", callback_data="routes"),
        InlineKeyboardButton("🧠 Batch Mode", callback_data="batch"),
        InlineKeyboardButton("❌ Delete Route", callback_data="delroute"),
        InlineKeyboardButton("⚙️ File Settings", callback_data="settings"),
        InlineKeyboardButton("🖼 Set Thumbnail", callback_data="set_thumb"),
    )
    return kb

def settings_menu(uid):
    s = get_user_settings(uid)
    kb = InlineKeyboardMarkup(row_width=2)
    doc_label = "📄 Send As Doc: ON ✅" if s["send_as_document"] else "📄 Send As Doc: OFF"
    kb.add(
        InlineKeyboardButton("✏️ Set Prefix", callback_data="set_prefix"),
        InlineKeyboardButton("✏️ Set Suffix", callback_data="set_suffix"),
        InlineKeyboardButton("📝 Rename Template", callback_data="set_template"),
        InlineKeyboardButton("💬 Set Caption", callback_data="set_caption"),
        InlineKeyboardButton("🔁 Replace Words", callback_data="set_replace"),
        InlineKeyboardButton("🗑 Remove Word", callback_data="set_remove"),
        InlineKeyboardButton(doc_label, callback_data="toggle_doc"),
        InlineKeyboardButton("📤 Set Destination", callback_data="set_dest"),
        InlineKeyboardButton("📋 View Settings", callback_data="view_settings"),
        InlineKeyboardButton("🔄 Reset Settings", callback_data="reset_settings"),
        InlineKeyboardButton("🔙 Back", callback_data="back_main"),
    )
    return kb

# ==============================
# START + HELP
# ==============================

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id,
        "⚡ <b>TSD HUB READY</b>\n\nSend any file to process it, or use the menu below.",
        reply_markup=main_menu())

@bot.message_handler(commands=['help'])
def help_cmd(m):
    txt = """
📘 <b>TSD HUB COMMANDS</b>

🚀 <b>AUTOFORWARD:</b>
/addroute → setup auto-forward route

📡 <b>ROUTES:</b>
/routes → view all routes
/delroute 1 → delete route by number

🖼 <b>THUMBNAIL:</b>
/setthumb → send photo after this to set thumbnail
/removethumb → remove saved thumbnail

✏️ <b>RENAME:</b>
/prefix Text_ → set prefix
/suffix _Text → set suffix
/template SarcasticDr_{filename} → rename template
/replace old|new → replace word in filename
/remove word → remove word from filename

💬 <b>CAPTION:</b>
/caption 📚 {filename} → set caption template
/removecaption → clear caption

📤 <b>DESTINATION:</b>
/setdest -100xxxxxxxxx → set default dest chat
/setdesttopic 123 → set dest topic id

🧠 <b>BATCH:</b>
/done → finish batch upload

⚙️ <b>NOTES:</b>
• Bot must be admin in source & destination
• {filename} = new filename, {original} = original caption
"""
    bot.reply_to(m, txt)

# ==============================
# CALLBACKS
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    uid = call.from_user.id
    cid = call.message.chat.id

    if call.data == "af":
        user_sessions[uid] = {"mode": "af1"}
        bot.send_message(cid, "📥 Send <b>SOURCE CHAT ID</b>")

    elif call.data == "routes":
        if not ROUTES:
            bot.send_message(cid, "No routes configured.")
            return
        txt = "📡 <b>ROUTES:</b>\n\n"
        for i, r in enumerate(ROUTES, 1):
            txt += (f"{i}.\nSRC: <code>{r['source_chat']}</code> | topic: {r['source_topic']}\n"
                    f"DST: <code>{r['dest_chat']}</code> | topic: {r['dest_topic']}\n"
                    f"Delay: {r['delay']}s\n\n")
        bot.send_message(cid, txt)

    elif call.data == "batch":
        user_batches[uid] = []
        user_sessions[uid] = {"mode": "batch"}
        bot.send_message(cid, "📦 Batch mode ON. Send files then /done")

    elif call.data == "delroute":
        bot.send_message(cid, "Use: /delroute 1")

    elif call.data == "settings":
        bot.send_message(cid, "⚙️ <b>File Settings</b>", reply_markup=settings_menu(uid))

    elif call.data == "set_thumb":
        user_sessions[uid] = {"mode": "await_thumb"}
        bot.send_message(cid, "🖼 Send me a <b>photo</b> to use as thumbnail.")

    elif call.data == "set_prefix":
        user_sessions[uid] = {"mode": "await_prefix"}
        bot.send_message(cid, "✏️ Send the <b>prefix</b> text (e.g. <code>TSD_</code>)\nSend <code>none</code> to clear.")

    elif call.data == "set_suffix":
        user_sessions[uid] = {"mode": "await_suffix"}
        bot.send_message(cid, "✏️ Send the <b>suffix</b> text (e.g. <code>_HD</code>)\nSend <code>none</code> to clear.")

    elif call.data == "set_template":
        user_sessions[uid] = {"mode": "await_template"}
        bot.send_message(cid,
            "📝 Send rename template.\nUse <code>{filename}</code> as placeholder.\n"
            "Example: <code>SarcasticDr_{filename}</code>\nSend <code>none</code> to clear.")

    elif call.data == "set_caption":
        user_sessions[uid] = {"mode": "await_caption"}
        bot.send_message(cid,
            "💬 Send caption template.\nVariables:\n"
            "<code>{filename}</code> = new filename\n<code>{original}</code> = original caption\n"
            "Example: <code>📚 <b>{filename}</b></code>\nSend <code>none</code> to clear.")

    elif call.data == "set_replace":
        user_sessions[uid] = {"mode": "await_replace"}
        bot.send_message(cid, "🔁 Send: <code>old word|new word</code>\nExample: <code>480p|1080p</code>")

    elif call.data == "set_remove":
        user_sessions[uid] = {"mode": "await_remove"}
        bot.send_message(cid, "🗑 Send the word to <b>remove</b> from filenames/captions.\nExample: <code>[TSD]</code>")

    elif call.data == "toggle_doc":
        s = get_user_settings(uid)
        s["send_as_document"] = not s["send_as_document"]
        save_settings()
        status = "ON ✅" if s["send_as_document"] else "OFF ❌"
        bot.answer_callback_query(call.id, f"Send As Document: {status}")
        bot.edit_message_reply_markup(cid, call.message.message_id, reply_markup=settings_menu(uid))

    elif call.data == "set_dest":
        user_sessions[uid] = {"mode": "await_dest"}
        bot.send_message(cid, "📤 Send <b>destination chat ID</b>\nExample: <code>-1001234567890</code>")

    elif call.data == "view_settings":
        s = get_user_settings(uid)
        rw = "\n".join([f"  <code>{k}</code> → <code>{v}</code>" for k, v in s["replace_words"].items()]) or "  None"
        txt = (
            f"⚙️ <b>Your Settings:</b>\n\n"
            f"Prefix: <code>{s['prefix'] or 'None'}</code>\n"
            f"Suffix: <code>{s['suffix'] or 'None'}</code>\n"
            f"Template: <code>{s['rename_template'] or 'None'}</code>\n"
            f"Caption: <code>{s['caption'] or 'None'}</code>\n"
            f"Thumbnail: {'✅ Set' if s['thumbnail_file_id'] else '❌ Not set'}\n"
            f"Send As Doc: {'✅' if s['send_as_document'] else '❌'}\n"
            f"Destination: <code>{s['destination'] or 'Not set'}</code>\n"
            f"Dest Topic: <code>{s['dest_topic'] or 'None'}</code>\n"
            f"Replace Words:\n{rw}"
        )
        bot.send_message(cid, txt)

    elif call.data == "reset_settings":
        uid_str = str(uid)
        user_settings[uid_str] = {}
        get_user_settings(uid)  # reinit defaults
        save_settings()
        bot.answer_callback_query(call.id, "✅ Settings reset!")
        bot.send_message(cid, "✅ All settings reset to default.")

    elif call.data == "back_main":
        bot.send_message(cid, "🏠 Main Menu", reply_markup=main_menu())

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
        mode = s["mode"]

        # ---- THUMBNAIL ----
        if mode == "await_thumb":
            if m.content_type == "photo":
                file_id = m.photo[-1].file_id
                get_user_settings(uid)["thumbnail_file_id"] = file_id
                save_settings()
                bot.reply_to(m, "✅ Thumbnail saved!")
                user_sessions.pop(uid)
            else:
                bot.reply_to(m, "❗ Please send a photo.")
            return True

        # ---- SETTINGS TEXT INPUTS ----
        if mode == "await_prefix":
            val = "" if m.text.lower() == "none" else m.text
            get_user_settings(uid)["prefix"] = val
            save_settings()
            bot.reply_to(m, f"✅ Prefix set to: <code>{val or 'None'}</code>")
            user_sessions.pop(uid)
            return True

        if mode == "await_suffix":
            val = "" if m.text.lower() == "none" else m.text
            get_user_settings(uid)["suffix"] = val
            save_settings()
            bot.reply_to(m, f"✅ Suffix set to: <code>{val or 'None'}</code>")
            user_sessions.pop(uid)
            return True

        if mode == "await_template":
            val = "" if m.text.lower() == "none" else m.text
            get_user_settings(uid)["rename_template"] = val
            save_settings()
            bot.reply_to(m, f"✅ Template set to: <code>{val or 'None'}</code>")
            user_sessions.pop(uid)
            return True

        if mode == "await_caption":
            val = "" if m.text.lower() == "none" else m.text
            get_user_settings(uid)["caption"] = val
            save_settings()
            bot.reply_to(m, f"✅ Caption template set.")
            user_sessions.pop(uid)
            return True

        if mode == "await_replace":
            parts = m.text.split("|", 1)
            if len(parts) != 2:
                bot.reply_to(m, "❗ Format: <code>old|new</code>")
                return True
            old, new = parts[0].strip(), parts[1].strip()
            get_user_settings(uid)["replace_words"][old] = new
            save_settings()
            bot.reply_to(m, f"✅ Will replace <code>{old}</code> → <code>{new}</code>")
            user_sessions.pop(uid)
            return True

        if mode == "await_remove":
            word = m.text.strip()
            get_user_settings(uid)["replace_words"][word] = ""
            save_settings()
            bot.reply_to(m, f"✅ Will remove: <code>{word}</code>")
            user_sessions.pop(uid)
            return True

        if mode == "await_dest":
            try:
                dest = int(m.text.strip())
                get_user_settings(uid)["destination"] = dest
                save_settings()
                bot.reply_to(m, f"✅ Default destination set: <code>{dest}</code>")
                user_sessions.pop(uid)
            except:
                bot.reply_to(m, "❗ Invalid chat ID. Must be a number like <code>-1001234567890</code>")
            return True

        # ---- FILE PROCESSING: user sends file + asks where to send ----
        if mode == "process_file":
            # waiting for destination
            try:
                dest = int(m.text.strip())
            except:
                bot.reply_to(m, "❗ Invalid chat ID.")
                return True

            original_msg = s["file_msg"]
            dest_topic = s.get("dest_topic")

            bot.reply_to(m, "⏳ Processing...")
            try:
                send_processed_file(uid, original_msg, dest, dest_topic)
                bot.reply_to(m, "✅ Done!")
            except Exception as e:
                bot.reply_to(m, f"❌ Error: {e}")

            user_sessions.pop(uid)
            return True

        if mode == "process_file_topic":
            # waiting for topic id (or none)
            dest_topic = None if m.text.lower() == "none" else int(m.text.strip())
            s["dest_topic"] = dest_topic
            s["mode"] = "process_file"
            bot.reply_to(m, "📤 Send <b>destination chat ID</b>:")
            return True

        # ---- AUTOFORWARD SETUP ----
        if s["mode"] == "af1":
            s["src"] = int(m.text)
            s["mode"] = "af2"
            bot.reply_to(m, "Source topic ID or <code>none</code>:")

        elif s["mode"] == "af2":
            s["src_t"] = None if m.text.lower() == "none" else int(m.text)
            s["mode"] = "af3"
            bot.reply_to(m, "Destination chat ID:")

        elif s["mode"] == "af3":
            s["dst"] = int(m.text)
            s["mode"] = "af4"
            bot.reply_to(m, "Destination topic or <code>none</code>:")

        elif s["mode"] == "af4":
            s["dst_t"] = None if m.text.lower() == "none" else int(m.text)
            s["mode"] = "af5"
            bot.reply_to(m, "Delay in seconds (0 for none):")

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
            bot.reply_to(m, "✅ Autoforward route activated!")
            user_sessions.pop(uid)

        # ---- BATCH MODE ----
        elif s["mode"] == "batch":
            user_batches[uid].append(m)
            bot.reply_to(m, f"📦 Added #{len(user_batches[uid])}")

        elif s["mode"] == "batch_sort":
            batch = user_batches[uid]
            if m.text == "2":
                def get_name(msg):
                    if msg.caption:
                        return msg.caption.lower()
                    if msg.document and msg.document.file_name:
                        return msg.document.file_name.lower()
                    return str(msg.message_id)
                batch.sort(key=get_name)
            s["mode"] = "batch_dest"
            bot.reply_to(m, "Send DEST CHAT ID:")

        elif s["mode"] == "batch_dest":
            s["dest"] = int(m.text)
            s["mode"] = "batch_topic"
            bot.reply_to(m, "Send TOPIC ID or <code>none</code>:")

        elif s["mode"] == "batch_topic":
            dest_topic = None if m.text.lower() == "none" else int(m.text)
            dest = s["dest"]
            bot.reply_to(m, "⏳ Sending batch...")
            for msg in user_batches[uid]:
                try:
                    send_processed_file(uid, msg, dest, dest_topic)
                except Exception as e:
                    print("Batch error:", e)
                    try:
                        if dest_topic:
                            bot.copy_message(dest, msg.chat.id, msg.message_id, message_thread_id=dest_topic)
                        else:
                            bot.copy_message(dest, msg.chat.id, msg.message_id)
                    except:
                        pass
            bot.reply_to(m, "✅ Batch completed!")
            user_batches.pop(uid)
            user_sessions.pop(uid)

    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")
        user_sessions.pop(uid, None)

    return True

# ==============================
# COMMANDS
# ==============================

@bot.message_handler(commands=['addroute'])
def addroute(m):
    user_sessions[m.from_user.id] = {"mode": "af1"}
    bot.reply_to(m, "Send SOURCE CHAT ID:")

@bot.message_handler(commands=['routes'])
def show_routes(m):
    if not ROUTES:
        bot.reply_to(m, "No routes available.")
        return
    txt = "📡 <b>ROUTES:</b>\n\n"
    for i, r in enumerate(ROUTES, 1):
        txt += (f"{i}.\nSRC: <code>{r['source_chat']}</code> | topic: {r['source_topic']}\n"
                f"DST: <code>{r['dest_chat']}</code> | topic: {r['dest_topic']}\n"
                f"Delay: {r['delay']}s\n\n")
    bot.reply_to(m, txt)

@bot.message_handler(commands=['delroute'])
def delete_route(m):
    try:
        num = int(m.text.split()[1]) - 1
        if 0 <= num < len(ROUTES):
            ROUTES.pop(num)
            save_routes()
            bot.reply_to(m, "✅ Route deleted.")
        else:
            bot.reply_to(m, "Invalid route number.")
    except:
        bot.reply_to(m, "Usage: /delroute 1")

@bot.message_handler(commands=['setthumb'])
def setthumb(m):
    user_sessions[m.from_user.id] = {"mode": "await_thumb"}
    bot.reply_to(m, "🖼 Send me a photo to use as thumbnail.")

@bot.message_handler(commands=['removethumb'])
def removethumb(m):
    get_user_settings(m.from_user.id)["thumbnail_file_id"] = None
    save_settings()
    bot.reply_to(m, "✅ Thumbnail removed.")

@bot.message_handler(commands=['prefix'])
def set_prefix(m):
    parts = m.text.split(None, 1)
    val = parts[1].strip() if len(parts) > 1 else ""
    get_user_settings(m.from_user.id)["prefix"] = val
    save_settings()
    bot.reply_to(m, f"✅ Prefix: <code>{val or 'None'}</code>")

@bot.message_handler(commands=['suffix'])
def set_suffix(m):
    parts = m.text.split(None, 1)
    val = parts[1].strip() if len(parts) > 1 else ""
    get_user_settings(m.from_user.id)["suffix"] = val
    save_settings()
    bot.reply_to(m, f"✅ Suffix: <code>{val or 'None'}</code>")

@bot.message_handler(commands=['template'])
def set_template(m):
    parts = m.text.split(None, 1)
    val = parts[1].strip() if len(parts) > 1 else ""
    get_user_settings(m.from_user.id)["rename_template"] = val
    save_settings()
    bot.reply_to(m, f"✅ Template: <code>{val or 'None'}</code>")

@bot.message_handler(commands=['caption'])
def set_caption(m):
    parts = m.text.split(None, 1)
    val = parts[1].strip() if len(parts) > 1 else ""
    get_user_settings(m.from_user.id)["caption"] = val
    save_settings()
    bot.reply_to(m, f"✅ Caption template set.")

@bot.message_handler(commands=['removecaption'])
def remove_caption(m):
    get_user_settings(m.from_user.id)["caption"] = ""
    save_settings()
    bot.reply_to(m, "✅ Caption cleared.")

@bot.message_handler(commands=['replace'])
def replace_word(m):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or "|" not in parts[1]:
        bot.reply_to(m, "Usage: /replace old|new")
        return
    old, new = parts[1].split("|", 1)
    get_user_settings(m.from_user.id)["replace_words"][old.strip()] = new.strip()
    save_settings()
    bot.reply_to(m, f"✅ Replace: <code>{old.strip()}</code> → <code>{new.strip()}</code>")

@bot.message_handler(commands=['remove'])
def remove_word(m):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(m, "Usage: /remove word")
        return
    word = parts[1].strip()
    get_user_settings(m.from_user.id)["replace_words"][word] = ""
    save_settings()
    bot.reply_to(m, f"✅ Will remove: <code>{word}</code>")

@bot.message_handler(commands=['setdest'])
def set_dest(m):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(m, "Usage: /setdest -1001234567890")
        return
    try:
        dest = int(parts[1].strip())
        get_user_settings(m.from_user.id)["destination"] = dest
        save_settings()
        bot.reply_to(m, f"✅ Default destination: <code>{dest}</code>")
    except:
        bot.reply_to(m, "❗ Invalid chat ID.")

@bot.message_handler(commands=['setdesttopic'])
def set_dest_topic(m):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(m, "Usage: /setdesttopic 123  (or 0 to clear)")
        return
    val = int(parts[1].strip())
    get_user_settings(m.from_user.id)["dest_topic"] = val if val != 0 else None
    save_settings()
    bot.reply_to(m, f"✅ Dest topic: <code>{val or 'None'}</code>")

@bot.message_handler(commands=['settings'])
def show_settings(m):
    s = get_user_settings(m.from_user.id)
    rw = "\n".join([f"  <code>{k}</code> → <code>{v}</code>" for k, v in s["replace_words"].items()]) or "  None"
    txt = (
        f"⚙️ <b>Your Settings:</b>\n\n"
        f"Prefix: <code>{s['prefix'] or 'None'}</code>\n"
        f"Suffix: <code>{s['suffix'] or 'None'}</code>\n"
        f"Template: <code>{s['rename_template'] or 'None'}</code>\n"
        f"Caption: <code>{s['caption'] or 'None'}</code>\n"
        f"Thumbnail: {'✅ Set' if s['thumbnail_file_id'] else '❌ Not set'}\n"
        f"Send As Doc: {'✅' if s['send_as_document'] else '❌'}\n"
        f"Destination: <code>{s['destination'] or 'Not set'}</code>\n"
        f"Dest Topic: <code>{s['dest_topic'] or 'None'}</code>\n"
        f"Replace Words:\n{rw}"
    )
    bot.reply_to(m, txt)

@bot.message_handler(commands=['done'])
def done(m):
    uid = m.from_user.id
    if uid not in user_batches or not user_batches[uid]:
        bot.reply_to(m, "No files in batch.")
        return
    user_sessions[uid] = {"mode": "batch_sort"}
    bot.reply_to(m, "Sort order?\n1 = Original order\n2 = Alphabetical")

@bot.message_handler(commands=['list'])
def list_cmd(m):
    uid = m.from_user.id
    batch = user_batches.get(uid, [])
    if not batch:
        bot.reply_to(m, "No files in batch.")
        return
    txt = "📂 <b>FILE ORDER:</b>\n\n"
    for i, msg in enumerate(batch, 1):
        name = "file"
        if msg.caption:
            name = msg.caption
        elif msg.document and msg.document.file_name:
            name = msg.document.file_name
        txt += f"{i}. {name[:40]}\n"
    bot.reply_to(m, txt)

# ==============================
# RELAY ENGINE
# ==============================

def process_relay(m):
    src = m.chat.id
    topic = getattr(m, "message_thread_id", None)
    for r in ROUTES:
        if not r.get("enabled", True):
            continue
        if src != r["source_chat"]:
            continue
        if r["source_topic"] is not None:
            if topic != r["source_topic"]:
                continue
        ensure_worker(r)
        route_queues[get_key(r)].put(m)

# ==============================
# FILE HANDLER (direct to bot)
# ==============================

def handle_incoming_file(m):
    """
    When user sends a file directly to the bot (not in a relay),
    process it and ask for destination (or use default).
    """
    uid = m.from_user.id
    s = get_user_settings(uid)

    # If default destination is set, send immediately
    if s["destination"]:
        bot.reply_to(m, "⏳ Processing...")
        try:
            send_processed_file(uid, m, s["destination"], s.get("dest_topic"))
            bot.reply_to(m, "✅ Done!")
        except Exception as e:
            bot.reply_to(m, f"❌ Error: {e}")
    else:
        # Ask for destination
        user_sessions[uid] = {"mode": "process_file_topic", "file_msg": m}
        bot.reply_to(m, "📤 Send destination <b>topic ID</b> (or <code>none</code>):")

# ==============================
# HANDLERS
# ==============================

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'document'])
def handler(m):
    if handle_session(m):
        return

    # If it's a file sent directly to the bot (private chat)
    if m.chat.type == "private" and m.content_type in ("photo", "video", "document"):
        handle_incoming_file(m)
        return

    process_relay(m)

@bot.channel_post_handler(content_types=['text', 'photo', 'video', 'document'])
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
    return "TSD HUB RUNNING"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
