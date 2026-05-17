# 🔥 ONLY SHOWING CHANGED + IMPORTANT PARTS (DON'T REWRITE EVERYTHING MANUALLY)

# ==============================
# ADD THIS INSIDE ROUTE CREATION
# ==============================

"mode": "caption",   # default


# ==============================
# UPDATE CAPTION ENGINE
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
# MODIFY WORKER (IMPORTANT)
# ==============================

def worker(route):
    key = get_key(route)
    q = route_queues[key]

    while True:
        msg = q.get()
        try:
            time.sleep(route.get("delay", 0))

            caption = None
            filename = None

            # ===== MODE HANDLING =====
            if route.get("mode") == "caption":

                if msg.caption:
                    caption = process_text(msg.caption, route)

                elif msg.text:
                    caption = process_text(msg.text, route)

            elif route.get("mode") == "filename":

                if msg.document:
                    filename = process_text(msg.document.file_name, route)

            # ===== SEND =====

            if msg.content_type == "text":
                bot.send_message(route["dest_chat"], caption or msg.text)

            elif msg.document:

                file = bot.get_file(msg.document.file_id)
                downloaded = bot.download_file(file.file_path)

                bot.send_document(
                    route["dest_chat"],
                    downloaded,
                    visible_file_name=filename or msg.document.file_name,
                    caption=caption or msg.caption
                )

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


# ==============================
# ADD PROCESSING BUTTON UI
# ==============================

def process_menu(route_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ Prefix", callback_data=f"prefix_{route_id}"),
        InlineKeyboardButton("🔚 Suffix", callback_data=f"suffix_{route_id}"),
        InlineKeyboardButton("🔁 Replace", callback_data=f"replace_{route_id}"),
        InlineKeyboardButton("❌ Remove", callback_data=f"clean_{route_id}"),
        InlineKeyboardButton("⚙️ Mode", callback_data=f"mode_{route_id}")
    )
    return kb


# ==============================
# SHOW ROUTE WITH BUTTON
# ==============================

@bot.message_handler(commands=['routes'])
def routes_cmd(m):
    if not ROUTES:
        bot.reply_to(m, "No routes")
        return

    for i, r in enumerate(ROUTES, 1):
        txt = f"{i}. {r['source_chat']} → {r['dest_chat']}\nMode: {r.get('mode','caption')}"
        bot.send_message(m.chat.id, txt, reply_markup=process_menu(i-1))


# ==============================
# HANDLE BUTTONS
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def cb(call):

    data = call.data

    if data.startswith("mode_"):
        idx = int(data.split("_")[1])

        ROUTES[idx]["mode"] = "filename" if ROUTES[idx]["mode"] == "caption" else "caption"
        save_routes()

        bot.answer_callback_query(call.id, f"Mode: {ROUTES[idx]['mode']}")

    elif data.startswith("prefix_"):
        user_sessions[call.from_user.id] = {"mode": "setprefix", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send prefix")

    elif data.startswith("suffix_"):
        user_sessions[call.from_user.id] = {"mode": "setsuffix", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send suffix")

    elif data.startswith("replace_"):
        user_sessions[call.from_user.id] = {"mode": "replace", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send: old new")

    elif data.startswith("clean_"):
        user_sessions[call.from_user.id] = {"mode": "clean", "route": int(data.split("_")[1])}
        bot.send_message(call.message.chat.id, "Send word to remove")


# ==============================
# SESSION HANDLER ADD
# ==============================

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
