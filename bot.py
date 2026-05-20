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

ROUTE_FILE    = "routes.json"
SETTINGS_FILE = "user_settings.json"

ROUTES        = []
user_sessions = {}
user_batches  = {}
route_queues  = {}
route_workers = {}
user_settings = {}

# ══════════════════════════════════════════════════════
# LOAD / SAVE
# ══════════════════════════════════════════════════════

def load_routes():
    try:
        with open(ROUTE_FILE) as f:
            return json.load(f)
    except:
        return []

def save_routes():
    with open(ROUTE_FILE, "w") as f:
        json.dump(ROUTES, f, indent=4)

def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(user_settings, f, indent=4)

ROUTES        = load_routes()
user_settings = load_settings()

DEFAULT_ORDER = ["prefix_suffix", "replace", "template", "caption", "thumbnail"]

def get_user_settings(uid):
    key = str(uid)
    if key not in user_settings:
        user_settings[key] = {
            "prefix":            "",
            "suffix":            "",
            "rename_template":   "",
            "caption":           "",
            "thumbnail_file_id": None,
            "send_as_document":  False,
            "replace_words":     {},
            "destination":       None,
            "dest_topic":        None,
            # processing order — list of step keys
            "order": DEFAULT_ORDER.copy(),
        }
        save_settings()
    # back-fill order if missing (existing users)
    if "order" not in user_settings[key]:
        user_settings[key]["order"] = DEFAULT_ORDER.copy()
        save_settings()
    return user_settings[key]

# ══════════════════════════════════════════════════════
# PROCESSING PIPELINE
# Order is driven by s["order"] — fully user-defined.
# ══════════════════════════════════════════════════════

def run_pipeline(uid, filename, original_caption):
    """
    Returns (new_filename, new_caption) after applying
    all steps in the user-defined order.
    Thumbnail is handled separately at upload time.
    """
    s            = get_user_settings(uid)
    name, ext    = os.path.splitext(filename)
    cur_filename = filename
    cur_caption  = original_caption or ""

    for step in s.get("order", DEFAULT_ORDER):

        if step == "prefix_suffix":
            n, e = os.path.splitext(cur_filename)
            if s["prefix"]:
                n = s["prefix"] + n
            if s["suffix"]:
                n = n + s["suffix"]
            cur_filename = n + e

        elif step == "replace":
            for old, new in s.get("replace_words", {}).items():
                cur_filename = cur_filename.replace(old, new)
                cur_caption  = cur_caption.replace(old, new)

        elif step == "template":
            if s["rename_template"]:
                n, e = os.path.splitext(cur_filename)
                new_name = (s["rename_template"]
                            .replace("{filename}", n)
                            .replace("{original}", name))
                cur_filename = new_name + e

        elif step == "caption":
            if s["caption"]:
                cur_caption = (s["caption"]
                               .replace("{filename}", cur_filename)
                               .replace("{original}", original_caption or ""))

        # "thumbnail" step is a marker only — applied during upload

    return cur_filename, cur_caption


def send_processed_file(uid, msg, dest_chat, dest_topic=None):
    """
    Full pipeline: download → rename → caption → thumbnail → upload.
    Falls back to plain copy for unsupported types.
    """
    s            = get_user_settings(uid)
    content_type = msg.content_type
    orig_caption = msg.caption or ""

    # ── resolve file ─────────────────────────────────
    file_id           = None
    original_filename = "file"

    if content_type == "document":
        file_id           = msg.document.file_id
        original_filename = msg.document.file_name or "file"
    elif content_type == "video":
        file_id           = msg.video.file_id
        original_filename = f"video_{msg.video.file_unique_id}.mp4"
    elif content_type == "photo":
        file_id           = msg.photo[-1].file_id
        original_filename = f"photo_{msg.photo[-1].file_unique_id}.jpg"
    else:
        _copy(dest_chat, msg, dest_topic)
        return

    new_filename, new_caption = run_pipeline(uid, original_filename, orig_caption)

    # ── download ─────────────────────────────────────
    file_info = bot.get_file(file_id)
    raw       = bot.download_file(file_info.file_path)
    tmp_path  = f"/tmp/tsd_{new_filename}"
    with open(tmp_path, "wb") as f:
        f.write(raw)

    # ── thumbnail (only if step is in order) ─────────
    thumb = None
    if "thumbnail" in s.get("order", DEFAULT_ORDER) and s["thumbnail_file_id"]:
        try:
            ti   = bot.get_file(s["thumbnail_file_id"])
            tb   = bot.download_file(ti.file_path)
            tp   = "/tmp/_tsd_thumb.jpg"
            with open(tp, "wb") as tf:
                tf.write(tb)
            thumb = open(tp, "rb")
        except Exception as e:
            print("Thumb error:", e)
            thumb = None

    # ── upload ───────────────────────────────────────
    try:
        with open(tmp_path, "rb") as f:
            kw = dict(chat_id=dest_chat,
                      caption=new_caption or None,
                      parse_mode="HTML")
            if dest_topic:
                kw["message_thread_id"] = dest_topic

            if s["send_as_document"] or content_type == "document":
                kw["document"]          = f
                kw["visible_file_name"] = new_filename
                if thumb:
                    kw["thumb"] = thumb
                bot.send_document(**kw)
            elif content_type == "video":
                kw["video"] = f
                if thumb:
                    kw["thumb"] = thumb
                bot.send_video(**kw)
            elif content_type == "photo":
                kw["photo"] = f
                bot.send_photo(**kw)
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


def _copy(dest_chat, msg, dest_topic=None):
    if dest_topic:
        bot.copy_message(dest_chat, msg.chat.id, msg.message_id,
                         message_thread_id=dest_topic)
    else:
        bot.copy_message(dest_chat, msg.chat.id, msg.message_id)


# ══════════════════════════════════════════════════════
# ROUTE WORKER ENGINE
# Each route stores owner_uid + list of destinations.
# ══════════════════════════════════════════════════════

def get_key(r):
    return f"{r['source_chat']}_{r['source_topic']}"

def worker(route):
    key = get_key(route)
    q   = route_queues[key]
    while True:
        msg = q.get()
        try:
            time.sleep(route.get("delay", 0))
            owner_uid = route.get("owner_uid")
            for dest in route.get("destinations", []):
                dest_chat  = dest["chat_id"]
                dest_topic = dest.get("topic_id")
                try:
                    if owner_uid:
                        send_processed_file(owner_uid, msg, dest_chat, dest_topic)
                    else:
                        _copy(dest_chat, msg, dest_topic)
                except Exception as e:
                    print(f"Worker dest error ({dest_chat}):", e)
        except Exception as e:
            print("Worker error:", e)
        q.task_done()

def ensure_worker(route):
    key = get_key(route)
    if key not in route_queues:
        route_queues[key] = Queue()
    if key not in route_workers or not route_workers[key].is_alive():
        t = threading.Thread(target=worker, args=(route,), daemon=True)
        t.start()
        route_workers[key] = t

for r in ROUTES:
    ensure_worker(r)

# ══════════════════════════════════════════════════════
# ORDER LABELS
# ══════════════════════════════════════════════════════

ORDER_LABELS = {
    "prefix_suffix": "✏️ Prefix/Suffix",
    "replace":       "🔁 Replace/Remove",
    "template":      "📝 Rename Template",
    "caption":       "💬 Caption",
    "thumbnail":     "🖼 Thumbnail",
}

def order_display(uid):
    s = get_user_settings(uid)
    lines = []
    for i, step in enumerate(s["order"], 1):
        lines.append(f"{i}. {ORDER_LABELS.get(step, step)}")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════
# MENUS
# ══════════════════════════════════════════════════════

def main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🚀 Add Route",       callback_data="af"),
        InlineKeyboardButton("📡 View Routes",     callback_data="routes"),
        InlineKeyboardButton("🧠 Batch Forward",   callback_data="batch"),
        InlineKeyboardButton("❌ Delete Route",    callback_data="delroute"),
        InlineKeyboardButton("⚙️ File Settings",   callback_data="settings"),
        InlineKeyboardButton("🖼 Thumbnail",        callback_data="thumb_menu"),
        InlineKeyboardButton("🔢 Set Order",        callback_data="set_order"),
        InlineKeyboardButton("📋 My Settings",     callback_data="view_settings"),
    )
    return kb

def settings_menu(uid):
    s      = get_user_settings(uid)
    doc_lbl = "📄 As Document: ✅" if s["send_as_document"] else "📄 As Document: ❌"
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ Prefix",          callback_data="set_prefix"),
        InlineKeyboardButton("✏️ Suffix",          callback_data="set_suffix"),
        InlineKeyboardButton("📝 Rename Template", callback_data="set_template"),
        InlineKeyboardButton("💬 Caption",         callback_data="set_caption"),
        InlineKeyboardButton("🔁 Replace Word",    callback_data="set_replace"),
        InlineKeyboardButton("🗑 Remove Word",     callback_data="set_remove"),
        InlineKeyboardButton(doc_lbl,              callback_data="toggle_doc"),
        InlineKeyboardButton("📤 Set Destination", callback_data="set_dest"),
        InlineKeyboardButton("🔄 Reset All",       callback_data="reset_settings"),
        InlineKeyboardButton("🔙 Back",            callback_data="back_main"),
    )
    return kb

def thumb_menu(uid):
    s  = get_user_settings(uid)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👁 View Thumbnail",   callback_data="thumb_view"),
        InlineKeyboardButton("📷 Set Thumbnail",    callback_data="thumb_set"),
        InlineKeyboardButton("🗑 Remove Thumbnail", callback_data="thumb_remove"),
        InlineKeyboardButton("🔙 Back",             callback_data="back_main"),
    )
    return kb

def route_add_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📝 Step-by-step",              callback_data="af_steps"),
        InlineKeyboardButton("⚡ All-in-one (quick entry)",  callback_data="af_quick"),
        InlineKeyboardButton("🔙 Back",                      callback_data="back_main"),
    )
    return kb

# ══════════════════════════════════════════════════════
# /start  /help
# ══════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "⚡ <b>TSD HUB READY</b>\n\n"
        "Send any file directly → it gets processed and forwarded.\n"
        "Use the menu for routes, batch, and settings.",
        reply_markup=main_menu()
    )

@bot.message_handler(commands=["help"])
def help_cmd(m):
    bot.reply_to(m, """
📘 <b>TSD HUB — FULL GUIDE</b>

━━━━━━━━━━━━━━━━━━
🖼 <b>THUMBNAIL</b>
/setthumb       → send photo to set
/removethumb    → clear thumbnail
(View via menu → Thumbnail)

━━━━━━━━━━━━━━━━━━
✏️ <b>RENAME</b>
/prefix  TSD_              → add prefix
/suffix  _HD               → add suffix
/template SarcasticDr_{filename}

🔁 <b>REPLACE / REMOVE</b>
/replace 480p|1080p        → replace in name & caption
/remove  [TSD]             → remove from name & caption

💬 <b>CAPTION</b>
/caption 📚 <b>{filename}</b>
/removecaption
Variables: {filename} {original}

━━━━━━━━━━━━━━━━━━
🔢 <b>PROCESSING ORDER</b>
/setorder → set order via menu
Default: Prefix/Suffix → Replace → Template → Caption → Thumbnail

━━━━━━━━━━━━━━━━━━
📤 <b>DESTINATION</b>
/setdest              → step-by-step (chat + topic)
/setdesttopic 123     → update topic only (0 to clear)

━━━━━━━━━━━━━━━━━━
🚀 <b>AUTOFORWARD ROUTES</b>
/addroute             → step-by-step OR quick entry
  Step-by-step: guides you through each field
  Quick entry:  SOURCE_ID | SOURCE_TOPIC | DEST1_ID:DEST1_TOPIC,DEST2_ID:DEST2_TOPIC | DELAY
  (use 0 for no topic, e.g.  -1001111 | 0 | -1002222:0,-1003333:5 | 0)
/routes               → list all routes
/delroute 1           → delete by number

━━━━━━━━━━━━━━━━━━
🧠 <b>BATCH FORWARD</b>
Send files → /done → choose sort → enter destination
All settings (rename/caption/thumb) applied to every file.

⚙️ /settings   → view all current settings
""")

# ══════════════════════════════════════════════════════
# CALLBACKS
# ══════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    uid = call.from_user.id
    cid = call.message.chat.id

    # ── main menu ──────────────────────────────────
    if call.data == "af":
        bot.send_message(cid, "🚀 <b>Add Route</b>\nChoose setup method:",
                         reply_markup=route_add_menu())

    elif call.data == "af_steps":
        user_sessions[uid] = {"mode": "af_s1"}
        bot.send_message(cid,
            "📝 <b>Step-by-step Route Setup</b>\n\n"
            "<b>Step 1/5</b> — Send <b>SOURCE chat ID</b>:")

    elif call.data == "af_quick":
        user_sessions[uid] = {"mode": "af_quick"}
        bot.send_message(cid,
            "⚡ <b>Quick Route Entry</b>\n\n"
            "Send one line in this format:\n"
            "<code>SOURCE_ID | SOURCE_TOPIC | DEST1_ID:TOPIC,DEST2_ID:TOPIC | DELAY</code>\n\n"
            "Examples:\n"
            "<code>-1001111111 | 0 | -1002222222:0 | 0</code>\n"
            "<code>-1001111111 | 5 | -1002222222:0,-1003333333:8 | 3</code>\n\n"
            "Use <code>0</code> for no topic.")

    elif call.data == "routes":
        _show_routes(cid)

    elif call.data == "batch":
        user_batches[uid] = []
        user_sessions[uid] = {"mode": "batch"}
        bot.send_message(cid,
            "🧠 <b>Batch Forward ON</b>\n"
            "Send all your files, then send /done")

    elif call.data == "delroute":
        bot.send_message(cid, "Use: <code>/delroute 1</code>")

    elif call.data == "settings":
        bot.send_message(cid, "⚙️ <b>File Settings</b>",
                         reply_markup=settings_menu(uid))

    elif call.data == "thumb_menu":
        bot.send_message(cid, "🖼 <b>Thumbnail Settings</b>",
                         reply_markup=thumb_menu(uid))

    elif call.data == "thumb_view":
        s = get_user_settings(uid)
        if s["thumbnail_file_id"]:
            bot.send_photo(cid, s["thumbnail_file_id"],
                           caption="🖼 Your current thumbnail.")
        else:
            bot.send_message(cid, "❌ No thumbnail set.")

    elif call.data == "thumb_set":
        user_sessions[uid] = {"mode": "await_thumb"}
        bot.send_message(cid, "📷 Send a <b>photo</b> to use as thumbnail:")

    elif call.data == "thumb_remove":
        get_user_settings(uid)["thumbnail_file_id"] = None
        save_settings()
        bot.answer_callback_query(call.id, "✅ Thumbnail removed.")
        bot.send_message(cid, "✅ Thumbnail removed.")

    elif call.data == "set_order":
        _ask_order(cid, uid)

    elif call.data == "view_settings":
        _send_settings(cid, uid)

    # ── settings menu ──────────────────────────────
    elif call.data == "set_prefix":
        user_sessions[uid] = {"mode": "await_prefix"}
        bot.send_message(cid,
            "✏️ Send <b>prefix</b> (e.g. <code>TSD_</code>)\n"
            "Send <code>none</code> to clear:")

    elif call.data == "set_suffix":
        user_sessions[uid] = {"mode": "await_suffix"}
        bot.send_message(cid,
            "✏️ Send <b>suffix</b> (e.g. <code>_HD</code>)\n"
            "Send <code>none</code> to clear:")

    elif call.data == "set_template":
        user_sessions[uid] = {"mode": "await_template"}
        bot.send_message(cid,
            "📝 Send <b>rename template</b>\n"
            "Use <code>{filename}</code> as placeholder\n"
            "Example: <code>SarcasticDr_{filename}</code>\n"
            "Send <code>none</code> to clear:")

    elif call.data == "set_caption":
        user_sessions[uid] = {"mode": "await_caption"}
        bot.send_message(cid,
            "💬 Send <b>caption template</b>\n"
            "<code>{filename}</code> = new filename\n"
            "<code>{original}</code> = original caption\n"
            "Example: <code>📚 <b>{filename}</b></code>\n"
            "Send <code>none</code> to clear:")

    elif call.data == "set_replace":
        user_sessions[uid] = {"mode": "await_replace"}
        bot.send_message(cid,
            "🔁 Format: <code>old|new</code>\n"
            "Example: <code>480p|1080p</code>")

    elif call.data == "set_remove":
        user_sessions[uid] = {"mode": "await_remove"}
        bot.send_message(cid, "🗑 Send the word to <b>remove</b> from filenames/captions:")

    elif call.data == "toggle_doc":
        s = get_user_settings(uid)
        s["send_as_document"] = not s["send_as_document"]
        save_settings()
        status = "ON ✅" if s["send_as_document"] else "OFF ❌"
        bot.answer_callback_query(call.id, f"Send As Document: {status}")
        try:
            bot.edit_message_reply_markup(cid, call.message.message_id,
                                          reply_markup=settings_menu(uid))
        except:
            pass

    elif call.data == "set_dest":
        user_sessions[uid] = {"mode": "await_dest_chat"}
        bot.send_message(cid,
            "📤 <b>Step 1/2</b> — Send destination <b>chat ID</b>\n"
            "Example: <code>-1001234567890</code>")

    elif call.data == "reset_settings":
        user_settings.pop(str(uid), None)
        get_user_settings(uid)
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
