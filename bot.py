import os
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request
import telebot

from config      import BOT_TOKEN, DEFAULT_ORDER, ORDER_KEYS
from storage     import (load_routes, save_routes, load_settings, save_settings,
                         init_settings_ref, get_user_settings, load_sessions, save_sessions)
from caption_engine import send_processed_file, _plain_copy
from relay_engine   import process_relay, ensure_worker
from ui          import (main_menu, settings_menu, thumb_menu, route_add_menu,
                         add_dest_menu, order_display, format_settings, format_routes)

# ══════════════════════════════════════════
# BOT + FLASK  (threaded=True → each handler
# gets its own thread, buttons respond instantly)
# ══════════════════════════════════════════

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)

# thread pool for webhook dispatch
_pool = ThreadPoolExecutor(max_workers=20)

# ══════════════════════════════════════════
# LOAD STATE
# ══════════════════════════════════════════

ROUTES        = load_routes()
user_settings = load_settings()
user_sessions = load_sessions()
user_batches  = {}

init_settings_ref(user_settings)   # link debounced saver

# boot workers for persisted routes
def _getter(uid):
    return get_user_settings(user_settings, uid)

for r in ROUTES:
    ensure_worker(bot, r, _getter)

# ══════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════

def s(uid):
    """Shorthand: get settings for uid."""
    return get_user_settings(user_settings, uid)

def save_sess():
    save_sessions(user_sessions)

# ══════════════════════════════════════════
# /start  /help
# ══════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(m):
    bot.send_message(
        m.chat.id,
        "⚡ <b>TSD HUB READY</b>\n\n"
        "Send any file → processed and forwarded.\n"
        "Use the menu for routes, batch, and settings.",
        reply_markup=main_menu()
    )

@bot.message_handler(commands=["help"])
def cmd_help(m):
    bot.reply_to(m, """
📘 <b>TSD HUB — FULL GUIDE</b>

━━━━━━━━━━━━━━━━━━
🖼 <b>THUMBNAIL</b>
/setthumb       → send photo after this
/removethumb    → clear thumbnail
Menu → 🖼 Thumbnail → View / Set / Remove

━━━━━━━━━━━━━━━━━━
✏️ <b>RENAME</b>
/prefix  TSD_
/suffix  _HD
/template SarcasticDr_{filename}

🔁 <b>REPLACE / REMOVE</b>
/replace 480p|1080p
/remove  [TSD]

💬 <b>CAPTION</b>
/caption 📚 <b>{filename}</b>
/removecaption
Variables: {filename}  {original}

━━━━━━━━━━━━━━━━━━
🔢 <b>PROCESSING ORDER</b>
/setorder  (or menu → 🔢 Set Order)
Default: Prefix/Suffix → Replace → Template → Caption → Thumbnail

━━━━━━━━━━━━━━━━━━
📤 <b>DESTINATION</b>
/setdest          → step-by-step (chat + topic)
/setdesttopic 123 → update topic only (0 to clear)

━━━━━━━━━━━━━━━━━━
🚀 <b>AUTOFORWARD ROUTES</b>
/addroute  → step-by-step OR quick entry
  Quick: SOURCE | SOURCE_TOPIC | DEST1:TOPIC,DEST2:TOPIC | DELAY
  Example: -1001111 | 0 | -1002222:0,-1003333:5 | 3
/routes    → list all routes
/delroute 1

━━━━━━━━━━━━━━━━━━
🧠 <b>BATCH FORWARD</b>
Send files → /done → sort → destination
All settings applied to every file.

⚙️ /settings  → view current settings
""")

# ══════════════════════════════════════════
# CALLBACKS
# ══════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    uid = call.from_user.id
    cid = call.message.chat.id
    d   = call.data

    # answer immediately so button stops spinning
    bot.answer_callback_query(call.id)

    if d == "af":
        bot.send_message(cid, "🚀 <b>Add Route</b> — choose method:",
                         reply_markup=route_add_menu())

    elif d == "af_steps":
        user_sessions[uid] = {"mode": "af_s1"}
        save_sess()
        bot.send_message(cid,
            "📝 <b>Step-by-step Route Setup</b>\n\n"
            "<b>Step 1/5</b> — Send <b>SOURCE chat ID</b>:")

    elif d == "af_quick":
        user_sessions[uid] = {"mode": "af_quick"}
        save_sess()
        bot.send_message(cid,
            "⚡ <b>Quick Route Entry</b>\n\n"
            "<code>SOURCE_ID | SOURCE_TOPIC | DEST1_ID:TOPIC,DEST2_ID:TOPIC | DELAY</code>\n\n"
            "Examples:\n"
            "<code>-1001111111 | 0 | -1002222222:0 | 0</code>\n"
            "<code>-1001111111 | 5 | -1002222222:0,-1003333333:8 | 3</code>\n\n"
            "Use <code>0</code> for no topic.")

    elif d == "af_add_dest":
        if uid in user_sessions:
            user_sessions[uid]["mode"] = "af_s3"
            save_sess()
            n = len(user_sessions[uid].get("dests", [])) + 1
            bot.send_message(cid, f"Send <b>destination chat ID #{n}</b>:")

    elif d == "af_dest_done":
        if uid in user_sessions:
            user_sessions[uid]["mode"] = "af_s4"
            save_sess()
            bot.send_message(cid, "<b>Step 4/5</b> — Delay in <b>seconds</b> (0 for none):")

    elif d == "routes":
        bot.send_message(cid, format_routes(ROUTES))

    elif d == "batch":
        user_batches[uid] = []
        user_sessions[uid] = {"mode": "batch"}
        save_sess()
        bot.send_message(cid, "🧠 <b>Batch Forward ON</b>\nSend files then /done")

    elif d == "delroute":
        bot.send_message(cid, "Use: <code>/delroute 1</code>")

    elif d == "settings":
        bot.send_message(cid, "⚙️ <b>File Settings</b>",
                         reply_markup=settings_menu(s(uid)))

    elif d == "thumb_menu":
        bot.send_message(cid, "🖼 <b>Thumbnail</b>", reply_markup=thumb_menu())

    elif d == "thumb_view":
        st = s(uid)
        if st["thumbnail_file_id"]:
            bot.send_photo(cid, st["thumbnail_file_id"], caption="🖼 Your current thumbnail.")
        else:
            bot.send_message(cid, "❌ No thumbnail set.")

    elif d == "thumb_set":
        user_sessions[uid] = {"mode": "await_thumb"}
        save_sess()
        bot.send_message(cid, "📷 Send a <b>photo</b> to use as thumbnail:")

    elif d == "thumb_remove":
        s(uid)["thumbnail_file_id"] = None
        save_settings()
        bot.send_message(cid, "✅ Thumbnail removed.")

    elif d == "set_order":
        _prompt_order(cid, uid)

    elif d == "view_settings":
        bot.send_message(cid, format_settings(s(uid)))

    elif d == "set_prefix":
        user_sessions[uid] = {"mode": "await_prefix"}
        save_sess()
        bot.send_message(cid, "✏️ Send <b>prefix</b> (e.g. <code>TSD_</code>)\n<code>none</code> to clear:")

    elif d == "set_suffix":
        user_sessions[uid] = {"mode": "await_suffix"}
        save_sess()
        bot.send_message(cid, "✏️ Send <b>suffix</b> (e.g. <code>_HD</code>)\n<code>none</code> to clear:")

    elif d == "set_template":
        user_sessions[uid] = {"mode": "await_template"}
        save_sess()
        bot.send_message(cid,
            "📝 Send <b>rename template</b>\n"
            "Use <code>{filename}</code> as placeholder\n"
            "Example: <code>SarcasticDr_{filename}</code>\n"
            "<code>none</code> to clear:")

    elif d == "set_caption":
        user_sessions[uid] = {"mode": "await_caption"}
        save_sess()
        bot.send_message(cid,
            "💬 Send <b>caption template</b>\n"
            "<code>{filename}</code> = new filename\n"
            "<code>{original}</code> = original caption\n"
            "<code>none</code> to clear:")

    elif d == "set_replace":
        user_sessions[uid] = {"mode": "await_replace"}
        save_sess()
        bot.send_message(cid, "🔁 Format: <code>old|new</code>\nExample: <code>480p|1080p</code>")

    elif d == "set_remove":
        user_sessions[uid] = {"mode": "await_remove"}
        save_sess()
        bot.send_message(cid, "🗑 Send the word to <b>remove</b> from filenames/captions:")

    elif d == "toggle_doc":
        st = s(uid)
        st["send_as_document"] = not st["send_as_document"]
        save_settings()
        status = "ON ✅" if st["send_as_document"] else "OFF ❌"
        try:
            bot.edit_message_reply_markup(cid, call.message.message_id,
                                          reply_markup=settings_menu(st))
        except:
            pass
        bot.send_message(cid, f"📄 Send As Document: {status}")

    elif d == "set_dest":
        user_sessions[uid] = {"mode": "await_dest_chat"}
        save_sess()
        bot.send_message(cid,
            "📤 <b>Step 1/2</b> — Send destination <b>chat ID</b>\n"
            "Example: <code>-1001234567890</code>")

    elif d == "reset_settings":
        user_settings.pop(str(uid), None)
        s(uid)   # reinit defaults
        save_settings()
        bot.send_message(cid, "✅ All settings reset to default.")

    elif d == "back_main":
        bot.send_message(cid, "🏠 Main Menu", reply_markup=main_menu())

# ══════════════════════════════════════════
# SESSION HANDLER
# Returns True if message was consumed by a session.
# ══════════════════════════════════════════

def handle_session(m):
    if m.from_user is None:
        return False
    uid  = m.from_user.id
    if uid not in user_sessions:
        return False

    sess = user_sessions[uid]
    mode = sess.get("mode")

    try:
        # ── thumbnail ───────────────────────
        if mode == "await_thumb":
            if m.content_type == "photo":
                s(uid)["thumbnail_file_id"] = m.photo[-1].file_id
                save_settings()
                bot.reply_to(m, "✅ Thumbnail saved! Applied to all future uploads.")
                user_sessions.pop(uid)
                save_sess()
            else:
                bot.reply_to(m, "❗ Please send a photo.")
            return True

        # ── text settings ───────────────────
        if mode == "await_prefix":
            val = "" if m.text.strip().lower() == "none" else m.text.strip()
            s(uid)["prefix"] = val
            save_settings()
            bot.reply_to(m, f"✅ Prefix → <code>{val or 'cleared'}</code>")
            user_sessions.pop(uid); save_sess()
            return True

        if mode == "await_suffix":
            val = "" if m.text.strip().lower() == "none" else m.text.strip()
            s(uid)["suffix"] = val
            save_settings()
            bot.reply_to(m, f"✅ Suffix → <code>{val or 'cleared'}</code>")
            user_sessions.pop(uid); save_sess()
            return True

        if mode == "await_template":
            val = "" if m.text.strip().lower() == "none" else m.text.strip()
            s(uid)["rename_template"] = val
            save_settings()
            bot.reply_to(m, f"✅ Template → <code>{val or 'cleared'}</code>")
            user_sessions.pop(uid); save_sess()
            return True

        if mode == "await_caption":
            val = "" if m.text.strip().lower() == "none" else m.text.strip()
            s(uid)["caption"] = val
            save_settings()
            bot.reply_to(m, "✅ Caption template saved.")
            user_sessions.pop(uid); save_sess()
            return True

        if mode == "await_replace":
            if "|" not in m.text:
                bot.reply_to(m, "❗ Format: <code>old|new</code>")
                return True
            old, new = m.text.split("|", 1)
            s(uid)["replace_words"][old.strip()] = new.strip()
            save_settings()
            bot.reply_to(m, f"✅ <code>{old.strip()}</code> → <code>{new.strip()}</code>")
            user_sessions.pop(uid); save_sess()
            return True

        if mode == "await_remove":
            word = m.text.strip()
            s(uid)["replace_words"][word] = ""
            save_settings()
            bot.reply_to(m, f"✅ Will remove <code>{word}</code>")
            user_sessions.pop(uid); save_sess()
            return True

        # ── processing order ────────────────
        if mode == "await_order":
            nums = m.text.strip().split()
            if not all(n in ORDER_KEYS for n in nums):
                bot.reply_to(m, "❗ Use numbers 1–5 only. Example: <code>1 2 3 4 5</code>")
                return True
            new_order = [ORDER_KEYS[n] for n in nums]
            s(uid)["order"] = new_order
            save_settings()
            from config import ORDER_LABELS
            bot.reply_to(m,
                "✅ Order saved:\n" +
                "\n".join(f"{i+1}. {ORDER_LABELS[k]}" for i, k in enumerate(new_order)))
            user_sessions.pop(uid); save_sess()
            return True

        # ── destination 2-step ──────────────
        if mode == "await_dest_chat":
            try:
                dest = int(m.text.strip())
            except:
                bot.reply_to(m, "❗ Invalid. Must be a number.")
                return True
            sess["dest_chat_tmp"] = dest
            sess["mode"] = "await_dest_topic"
            save_sess()
            bot.reply_to(m,
                f"✅ Chat ID: <code>{dest}</code>\n\n"
                "<b>Step 2/2</b> — Send <b>topic/thread ID</b>\n"
                "<code>0</code> or <code>none</code> if no topic:")
            return True

        if mode == "await_dest_topic":
            val   = m.text.strip().lower()
            topic = None if val in ("0", "none") else int(m.text.strip())
            st    = s(uid)
            st["destination"] = sess["dest_chat_tmp"]
            st["dest_topic"]  = topic
            save_settings()
            bot.reply_to(m,
                f"✅ Destination saved!\n"
                f"Chat: <code>{st['destination']}</code>\n"
                f"Topic: <code>{topic or 'None'}</code>")
            user_sessions.pop(uid); save_sess()
            return True

        # ── direct file: ask destination ────
        if mode == "process_file_topic":
            val = m.text.strip().lower()
            sess["dest_topic"] = None if val in ("0", "none") else int(m.text.strip())
            sess["mode"] = "process_file_chat"
            save_sess()
            bot.reply_to(m, "<b>Step 2/2</b> — Send destination <b>chat ID</b>:")
            return True

        if mode == "process_file_chat":
            try:
                dest = int(m.text.strip())
            except:
                bot.reply_to(m, "❗ Invalid chat ID.")
                return True
            bot.reply_to(m, "⏳ Processing…")
            try:
                send_processed_file(bot, s(uid), sess["file_msg"], dest,
                                    sess.get("dest_topic"))
                bot.reply_to(m, "✅ Done!")
            except Exception as e:
                bot.reply_to(m, f"❌ Error: {e}")
            user_sessions.pop(uid); save_sess()
            return True

        # ── autoforward step-by-step ────────
        if mode == "af_s1":
            sess["src"]  = int(m.text.strip())
            sess["mode"] = "af_s2"
            save_sess()
            bot.reply_to(m, "<b>Step 2/5</b> — Source <b>topic ID</b> (<code>0</code> = none):")

        elif mode == "af_s2":
            val = m.text.strip().lower()
            sess["src_t"] = None if val in ("0","none") else int(m.text.strip())
            sess["dests"] = []
            sess["mode"]  = "af_s3"
            save_sess()
            bot.reply_to(m, "<b>Step 3/5</b> — Send <b>destination chat ID #1</b>:")

        elif mode == "af_s3":
            sess["cur_dest_chat"] = int(m.text.strip())
            sess["mode"] = "af_s3b"
            save_sess()
            n = len(sess.get("dests", [])) + 1
            bot.reply_to(m,
                f"Destination #{n} — Send <b>topic ID</b> (<code>0</code> = none):")

        elif mode == "af_s3b":
            val   = m.text.strip().lower()
            topic = None if val in ("0","none") else int(m.text.strip())
            sess.setdefault("dests", []).append({
                "chat_id": sess["cur_dest_chat"],
                "topic_id": topic
            })
            save_sess()
            bot.reply_to(m,
                f"✅ Destination added: <code>{sess['cur_dest_chat']}</code> "
                f"| topic: <code>{topic or 'None'}</code>\n"
                f"Total: <b>{len(sess['dests'])}</b>",
                reply_markup=add_dest_menu())

        elif mode == "af_s4":
            sess["delay"] = int(m.text.strip())
            sess["mode"]  = "af_confirm"
            save_sess()
            _confirm_route(m.chat.id, uid, sess)

        elif mode == "af_confirm":
            if m.text.strip() == "1":
                _save_route(uid, sess)
                bot.reply_to(m,
                    "✅ <b>Route saved!</b>\n"
                    "Your settings are applied automatically.")
            else:
                bot.reply_to(m, "❌ Route cancelled.")
            user_sessions.pop(uid); save_sess()

        # ── quick entry ─────────────────────
        elif mode == "af_quick":
            _parse_quick_route(m, uid)

        # ── batch ────────────────────────────
        elif mode == "batch":
            user_batches.setdefault(uid, []).append(m)
            bot.reply_to(m, f"📦 Added #{len(user_batches[uid])}")

        elif mode == "batch_sort":
            batch = user_batches.get(uid, [])
            if m.text.strip() == "2":
                def _sort_key(msg):
                    if msg.caption: return msg.caption.lower()
                    if msg.document and msg.document.file_name:
                        return msg.document.file_name.lower()
                    return str(msg.message_id)
                batch.sort(key=_sort_key)
            sess["mode"] = "batch_dest_chat"
            save_sess()
            bot.reply_to(m, "📤 <b>Step 1/2</b> — Send destination <b>chat ID</b>:")

        elif mode == "batch_dest_chat":
            sess["dest"] = int(m.text.strip())
            sess["mode"] = "batch_dest_topic"
            save_sess()
            bot.reply_to(m,
                "📤 <b>Step 2/2</b> — Send destination <b>topic ID</b>\n"
                "<code>0</code> or <code>none</code> if no topic:")

        elif mode == "batch_dest_topic":
            val        = m.text.strip().lower()
            dest_topic = None if val in ("0","none") else int(m.text.strip())
            dest       = sess["dest"]
            batch      = user_batches.get(uid, [])
            total      = len(batch)

            bot.reply_to(m,
                f"⏳ Sending <b>{total}</b> file(s)…\n"
                f"Settings applied:\n{order_display(s(uid))}")

            failed = 0
            for file_msg in batch:
                try:
                    send_processed_file(bot, s(uid), file_msg, dest, dest_topic)
                except Exception as e:
                    print("Batch error:", e)
                    failed += 1
                    try:
                        _plain_copy(bot, dest, file_msg, dest_topic)
                    except:
                        pass

            result = f"✅ Batch done! {total - failed}/{total} fully processed."
            if failed:
                result += f"\n⚠️ {failed} fell back to plain copy."
            bot.reply_to(m, result)
            user_batches.pop(uid, None)
            user_sessions.pop(uid); save_sess()

    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")
        user_sessions.pop(uid, None); save_sess()

    return True

# ══════════════════════════════════════════
# ROUTE HELPERS
# ══════════════════════════════════════════

def _prompt_order(cid, uid):
    st = s(uid)
    bot.send_message(cid,
        f"🔢 <b>Set Processing Order</b>\n\n"
        f"<b>Current:</b>\n{order_display(st)}\n\n"
        "Send new order as numbers (space-separated):\n"
        "1 = ✏️ Prefix/Suffix\n"
        "2 = 🔁 Replace/Remove\n"
        "3 = 📝 Rename Template\n"
        "4 = 💬 Caption\n"
        "5 = 🖼 Thumbnail\n\n"
        "Example: <code>1 2 3 4 5</code>\n"
        "Skip a step by omitting its number.")
    user_sessions[uid] = {"mode": "await_order"}
    save_sess()

def _confirm_route(cid, uid, sess):
    dests_txt = "\n".join(
        f"  → <code>{d['chat_id']}</code> | topic: <code>{d.get('topic_id') or 'None'}</code>"
        for d in sess.get("dests", [])
    )
    bot.send_message(cid,
        f"📋 <b>Confirm Route</b>\n\n"
        f"Source: <code>{sess['src']}</code> | topic: <code>{sess.get('src_t') or 'None'}</code>\n"
        f"Destinations ({len(sess.get('dests',[]))}):\n{dests_txt}\n"
        f"Delay: <b>{sess.get('delay',0)}s</b>\n\n"
        "Send <b>1</b> to confirm, anything else to cancel.")

def _save_route(uid, sess):
    route = {
        "source_chat":  sess["src"],
        "source_topic": sess.get("src_t"),
        "destinations": sess.get("dests", []),
        "delay":        sess.get("delay", 0),
        "enabled":      True,
        "owner_uid":    uid,
    }
    ROUTES.append(route)
    save_routes(ROUTES)
    ensure_worker(bot, route, _getter)

def _parse_quick_route(m, uid):
    try:
        parts = [p.strip() for p in m.text.strip().split("|")]
        if len(parts) != 4:
            raise ValueError("Need exactly 4 parts separated by |")

        src   = int(parts[0])
        raw   = parts[1].lower()
        src_t = None if raw in ("0","none") else int(parts[1])

        dests = []
        for item in parts[2].split(","):
            item = item.strip()
            if ":" in item:
                cid_s, tid_s = item.split(":", 1)
                chat_id  = int(cid_s.strip())
                topic_id = None if tid_s.strip() in ("0","none") else int(tid_s.strip())
            else:
                chat_id  = int(item)
                topic_id = None
            dests.append({"chat_id": chat_id, "topic_id": topic_id})

        delay = int(parts[3])

        route = {
            "source_chat":  src,
            "source_topic": src_t,
            "destinations": dests,
            "delay":        delay,
            "enabled":      True,
            "owner_uid":    uid,
        }
        ROUTES.append(route)
        save_routes(ROUTES)
        ensure_worker(bot, route, _getter)

        dests_txt = "\n".join(
            f"  → <code>{d['chat_id']}</code> | topic: <code>{d.get('topic_id') or 'None'}</code>"
            for d in dests
        )
        bot.reply_to(m,
            f"✅ <b>Route saved!</b>\n\n"
            f"Source: <code>{src}</code> | topic: <code>{src_t or 'None'}</code>\n"
            f"Destinations:\n{dests_txt}\n"
            f"Delay: {delay}s")

    except Exception as e:
        bot.reply_to(m,
            f"❌ Parse error: {e}\n\n"
            "Format:\n"
            "<code>SOURCE | TOPIC | DEST1:TOPIC,DEST2:TOPIC | DELAY</code>")

    user_sessions.pop(uid, None)
    save_sess()

# ══════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════

@bot.message_handler(commands=["addroute"])
def cmd_addroute(m):
    bot.reply_to(m, "🚀 <b>Add Route</b>", reply_markup=route_add_menu())

@bot.message_handler(commands=["routes"])
def cmd_routes(m):
    bot.reply_to(m, format_routes(ROUTES))

@bot.message_handler(commands=["delroute"])
def cmd_delroute(m):
    try:
        num = int(m.text.split()[1]) - 1
        if 0 <= num < len(ROUTES):
            ROUTES.pop(num)
            save_routes(ROUTES)
            bot.reply_to(m, "✅ Route deleted.")
        else:
            bot.reply_to(m, "Invalid number.")
    except:
        bot.reply_to(m, "Usage: /delroute 1")

@bot.message_handler(commands=["setthumb"])
def cmd_setthumb(m):
    user_sessions[m.from_user.id] = {"mode": "await_thumb"}
    save_sess()
    bot.reply_to(m, "📷 Send a photo to use as thumbnail:")

@bot.message_handler(commands=["removethumb"])
def cmd_removethumb(m):
    s(m.from_user.id)["thumbnail_file_id"] = None
    save_settings()
    bot.reply_to(m, "✅ Thumbnail removed.")

@bot.message_handler(commands=["prefix"])
def cmd_prefix(m):
    parts = m.text.split(None, 1)
    val   = parts[1].strip() if len(parts) > 1 else ""
    s(m.from_user.id)["prefix"] = val
    save_settings()
    bot.reply_to(m, f"✅ Prefix → <code>{val or '—'}</code>")

@bot.message_handler(commands=["suffix"])
def cmd_suffix(m):
    parts = m.text.split(None, 1)
    val   = parts[1].strip() if len(parts) > 1 else ""
    s(m.from_user.id)["suffix"] = val
    save_settings()
    bot.reply_to(m, f"✅ Suffix → <code>{val or '—'}</code>")

@bot.message_handler(commands=["template"])
def cmd_template(m):
    parts = m.text.split(None, 1)
    val   = parts[1].strip() if len(parts) > 1 else ""
    s(m.from_user.id)["rename_template"] = val
    save_settings()
    bot.reply_to(m, f"✅ Template → <code>{val or '—'}</code>")

@bot.message_handler(commands=["caption"])
def cmd_caption(m):
    parts = m.text.split(None, 1)
    val   = parts[1].strip() if len(parts) > 1 else ""
    s(m.from_user.id)["caption"] = val
    save_settings()
    bot.reply_to(m, "✅ Caption template saved.")

@bot.message_handler(commands=["removecaption"])
def cmd_removecaption(m):
    s(m.from_user.id)["caption"] = ""
    save_settings()
    bot.reply_to(m, "✅ Caption cleared.")

@bot.message_handler(commands=["replace"])
def cmd_replace(m):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or "|" not in parts[1]:
        bot.reply_to(m, "Usage: /replace old|new")
        return
    old, new = parts[1].split("|", 1)
    s(m.from_user.id)["replace_words"][old.strip()] = new.strip()
    save_settings()
    bot.reply_to(m, f"✅ <code>{old.strip()}</code> → <code>{new.strip()}</code>")

@bot.message_handler(commands=["remove"])
def cmd_remove(m):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(m, "Usage: /remove word")
        return
    word = parts[1].strip()
    s(m.from_user.id)["replace_words"][word] = ""
    save_settings()
    bot.reply_to(m, f"✅ Will remove <code>{word}</code>")

@bot.message_handler(commands=["setdest"])
def cmd_setdest(m):
    user_sessions[m.from_user.id] = {"mode": "await_dest_chat"}
    save_sess()
    bot.reply_to(m,
        "📤 <b>Step 1/2</b> — Send destination <b>chat ID</b>\n"
        "Example: <code>-1001234567890</code>")

@bot.message_handler(commands=["setdesttopic"])
def cmd_setdesttopic(m):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        bot.reply_to(m, "Usage: /setdesttopic 123  (0 to clear)")
        return
    val = int(parts[1].strip())
    s(m.from_user.id)["dest_topic"] = val if val != 0 else None
    save_settings()
    bot.reply_to(m, f"✅ Dest topic → <code>{val or '—'}</code>")

@bot.message_handler(commands=["setorder"])
def cmd_setorder(m):
    _prompt_order(m.chat.id, m.from_user.id)

@bot.message_handler(commands=["settings"])
def cmd_settings(m):
    bot.reply_to(m, format_settings(s(m.from_user.id)))

@bot.message_handler(commands=["done"])
def cmd_done(m):
    uid = m.from_user.id
    if uid not in user_batches or not user_batches[uid]:
        bot.reply_to(m, "No files in batch.")
        return
    user_sessions[uid] = {"mode": "batch_sort"}
    save_sess()
    bot.reply_to(m,
        f"📦 <b>{len(user_batches[uid])} file(s)</b> ready.\n\n"
        "Sort order?\n<b>1</b> = Original\n<b>2</b> = Alphabetical")

@bot.message_handler(commands=["list"])
def cmd_list(m):
    uid   = m.from_user.id
    batch = user_batches.get(uid, [])
    if not batch:
        bot.reply_to(m, "No files in batch.")
        return
    txt = "📂 <b>Batch queue:</b>\n\n"
    for i, msg in enumerate(batch, 1):
        name = "file"
        if msg.caption: name = msg.caption
        elif msg.document and msg.document.file_name:
            name = msg.document.file_name
        txt += f"{i}. {name[:50]}\n"
    bot.reply_to(m, txt)

# ══════════════════════════════════════════
# MAIN MESSAGE HANDLER
# ══════════════════════════════════════════

@bot.message_handler(
    func=lambda m: True,
    content_types=["text","photo","video","document","audio","voice","sticker"]
)
def on_message(m):
    if handle_session(m):
        return
    if m.chat.type == "private" and m.content_type in ("photo","video","document"):
        _handle_direct_file(m)
        return
    process_relay(bot, m, ROUTES, _getter)

@bot.channel_post_handler(
    content_types=["text","photo","video","document","audio"]
)
def on_channel_post(m):
    process_relay(bot, m, ROUTES, _getter)

def _handle_direct_file(m):
    uid = m.from_user.id
    st  = s(uid)
    if st["destination"]:
        bot.reply_to(m, "⏳ Processing…")
        try:
            send_processed_file(bot, st, m, st["destination"], st.get("dest_topic"))
            bot.reply_to(m, "✅ Done!")
        except Exception as e:
            bot.reply_to(m, f"❌ Error: {e}")
    else:
        user_sessions[uid] = {"mode": "process_file_topic", "file_msg": m}
        save_sess()
        bot.reply_to(m,
            "📤 No default destination set.\n\n"
            "<b>Step 1/2</b> — Send destination <b>topic ID</b>\n"
            "<code>0</code> or <code>none</code> if no topic:")

# ══════════════════════════════════════════
# WEBHOOK  — non-blocking
# Flask returns "ok" immediately.
# Update is dispatched to the thread pool.
# ══════════════════════════════════════════

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode())
    _pool.submit(bot.process_new_updates, [update])
    return "ok", 200

@app.route("/")
def home():
    return "TSD HUB RUNNING", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
