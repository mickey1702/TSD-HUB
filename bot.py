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
                "dest_chat"
