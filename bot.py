import os
import telebot
import traceback

from flask import Flask, request

from config import BOT_TOKEN
from storage import (
    load_routes,
    save_routes,
    load_sessions,
    save_sessions
)

from ui import (
    main_panel,
    back_panel,
    saved_paths_panel
)

from relay_engine import (
    process_relay,
    route_workers
)

# =========================================
# BOT INITIALIZATION
# =========================================

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML",
    threaded=False
)

app = Flask(__name__)

# =========================================
# GLOBAL DATA
# =========================================

ROUTES = load_routes()

user_sessions = load_sessions()

# =========================================
# START PANEL
# =========================================

@bot.message_handler(commands=['start'])

def start_cmd(message):

    txt = """
<b>⚡ TSD HUB CONTROL CENTER ⚡</b>

Unified Telegram Automation System

━━━━━━━━━━━━━━━━━━

➤ Smart Autoforward System
➤ Topic ↔ Topic Relay
➤ Saved Route Memory
➤ Caption Processing
➤ Prefix / Suffix Engine
➤ Queue Based Delivery
➤ Route Management Console

━━━━━━━━━━━━━━━━━━

<b>Status:</b> 🟢 ONLINE
"""

    bot.send_message(
        message.chat.id,
        txt,
        reply_markup=main_panel()
    )

# =========================================
# VIEW ROUTES
# =========================================

def build_routes_text():

    global ROUTES

    if not ROUTES:
        return "❌ No routes configured."

    txt = "📡 <b>ACTIVE ROUTES</b>\n\n"

    for i, r in enumerate(ROUTES, start=1):

        state = (
            "🟢 ON"
            if r.get("enabled", True)
            else "🔴 OFF"
        )

        txt += (
            f"{i}. {state}\n"
            f"FROM: {r['source_chat']} | {r['source_topic']}\n"
            f"TO: {r['dest_chat']} | {r['dest_topic']}\n"
            f"DELAY: {r.get('delay',0)} sec\n"
            f"PREFIX: {r.get('prefix','(none)')}\n\n"
        )

    return txt

# =========================================
# CALLBACKS
# =========================================

@bot.callback_query_handler(func=lambda call: True)

def callback_router(call):

    global ROUTES
    global user_sessions

    try:

        uid = call.from_user.id

        # =====================================
        # VIEW ROUTES
        # =====================================

        if call.data == "viewroutes":

            bot.send_message(
                call.message.chat.id,
                build_routes_text(),
                reply_markup=back_panel()
            )

        # =====================================
        # STATS
        # =====================================

        elif call.data == "stats":

            total = len(ROUTES)

            active = len([
                r for r in ROUTES
                if r.get("enabled", True)
            ])

            paused = total - active

            txt = (
                "📊 <b>TSD HUB STATISTICS</b>\n\n"
                f"Total Routes: {total}\n"
                f"Active Routes: {active}\n"
                f"Paused Routes: {paused}\n"
                f"Queue Workers: {len(route_workers)}"
            )

            bot.send_message(
                call.message.chat.id,
                txt,
                reply_markup=back_panel()
            )

        # =====================================
        # ADD ROUTE
        # =====================================

        elif call.data == "addroute":

            user_sessions[uid] = {
                "mode": "addroute_step1"
            }

            save_sessions(user_sessions)

            bot.send_message(
                call.message.chat.id,
                "➕ Send SOURCE CHAT ID"
            )

        # =====================================
        # TOGGLE ROUTE
        # =====================================

        elif call.data == "toggle":

            user_sessions[uid] = {
                "mode": "toggle_route"
            }

            save_sessions(user_sessions)

            bot.send_message(
                call.message.chat.id,
                "⏯ Send route number"
            )

        # =====================================
        # DELETE ROUTE
        # =====================================

        elif call.data == "delete":

            user_sessions[uid] = {
                "mode": "delete_route"
            }

            save_sessions(user_sessions)

            bot.send_message(
                call.message.chat.id,
                "🗑 Send route number"
            )

        # =====================================
        # CAPTION TOOLS
        # =====================================

        elif call.data == "captiontools":

            bot.send_message(
                call.message.chat.id,
                "📝 Caption Engine coming in next phase.",
                reply_markup=back_panel()
            )

        # =====================================
        # SAVED PATHS
        # =====================================

        elif call.data == "savedpaths":

            bot.send_message(
                call.message.chat.id,
                "💾 Saved Paths System",
                reply_markup=saved_paths_panel()
            )

        # =====================================
        # BACK MAIN
        # =====================================

        elif call.data == "backmain":

            start_cmd(call.message)

        else:

            bot.answer_callback_query(
                call.id,
                "Unknown action."
            )

    except Exception as e:

        print("CALLBACK ERROR:", e)

# =========================================
# USER SESSION ENGINE
# =========================================

def process_user_session(message):

    global ROUTES
    global user_sessions

    uid = message.from_user.id

    if uid not in user_sessions:
        return False

    session = user_sessions[uid]

    mode = session["mode"]

    try:

        # =====================================
        # STEP 1
        # =====================================

        if mode == "addroute_step1":

            session["source_chat"] = int(message.text)

            session["mode"] = "addroute_step2"

            save_sessions(user_sessions)

            bot.reply_to(
                message,
                "Send SOURCE TOPIC ID (or none)"
            )

        # =====================================
        # STEP 2
        # =====================================

        elif mode == "addroute_step2":

            session["source_topic"] = (
                None
                if message.text.lower() == "none"
                else int(message.text)
            )

            session["mode"] = "addroute_step3"

            save_sessions(user_sessions)

            bot.reply_to(
                message,
                "Send DESTINATION CHAT ID"
            )

        # =====================================
        # STEP 3
        # =====================================

        elif mode == "addroute_step3":

            session["dest_chat"] = int(message.text)

            session["mode"] = "addroute_step4"

            save_sessions(user_sessions)

            bot.reply_to(
                message,
                "Send DESTINATION TOPIC ID (or none)"
            )

        # =====================================
        # STEP 4
        # =====================================

        elif mode == "addroute_step4":

            session["dest_topic"] = (
                None
                if message.text.lower() == "none"
                else int(message.text)
            )

            session["mode"] = "addroute_step5"

            save_sessions(user_sessions)

            bot.reply_to(
                message,
                "Send DELAY in seconds"
            )

        # =====================================
        # STEP 5
        # =====================================

        elif mode == "addroute_step5":

            delay = int(message.text)

            new_route = {
                "source_chat": session["source_chat"],
                "source_topic": session["source_topic"],
                "dest_chat": session["dest_chat"],
                "dest_topic": session["dest_topic"],
                "delay": delay,
                "enabled": True,
                "prefix": ""
            }

            ROUTES.append(new_route)

            save_routes(ROUTES)

            bot.reply_to(
                message,
                "✅ Route added successfully."
            )

            del user_sessions[uid]

            save_sessions(user_sessions)

        # =====================================
        # TOGGLE
        # =====================================

        elif mode == "toggle_route":

            num = int(message.text) - 1

            if 0 <= num < len(ROUTES):

                ROUTES[num]["enabled"] = (
                    not ROUTES[num].get("enabled", True)
                )

                save_routes(ROUTES)

                state = (
                    "ON"
                    if ROUTES[num]["enabled"]
                    else "OFF"
                )

                bot.reply_to(
                    message,
                    f"⏯ Route {num+1} switched {state}"
                )

            del user_sessions[uid]

            save_sessions(user_sessions)

        # =====================================
        # DELETE
        # =====================================

        elif mode == "delete_route":

            num = int(message.text) - 1

            if 0 <= num < len(ROUTES):

                ROUTES.pop(num)

                save_routes(ROUTES)

                bot.reply_to(
                    message,
                    "🗑 Route deleted."
                )

            del user_sessions[uid]

            save_sessions(user_sessions)

    except Exception as e:

        bot.reply_to(
            message,
            f"❌ Session Error:\n{e}"
        )

        if uid in user_sessions:

            del user_sessions[uid]

            save_sessions(user_sessions)

    return True

# =========================================
# UNIVERSAL MESSAGE HANDLER
# =========================================

@bot.message_handler(
    func=lambda m: True,
    content_types=[
        'text',
        'photo',
        'video',
        'document',
        'audio',
        'voice',
        'sticker',
        'animation'
    ]
)

def universal_handler(message):

    if process_user_session(message):
        return

    process_relay(
        bot,
        message,
        ROUTES
    )

# =========================================
# CHANNEL POSTS
# =========================================

@bot.channel_post_handler(
    func=lambda m: True,
    content_types=[
        'text',
        'photo',
        'video',
        'document',
        'audio',
        'voice',
        'sticker',
        'animation'
    ]
)

def channel_handler(message):

    process_relay(
        bot,
        message,
        ROUTES
    )

# =========================================
# WEBHOOK
# =========================================

@app.route(f"/{BOT_TOKEN}", methods=["POST"])

def webhook():

    try:

        json_str = request.get_data().decode("utf-8")

        update = telebot.types.Update.de_json(json_str)

        bot.process_new_updates([update])

        return "ok", 200

    except Exception:

        print("WEBHOOK ERROR:")

        traceback.print_exc()

        return "error", 500

@app.route("/")

def home():

    return "TSD HUB ONLINE"

# =========================================
# RUN SERVER
# =========================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", 10000)
        )
      )
