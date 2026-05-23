import threading
import time
from queue import Queue
from caption_engine import send_processed_file, _plain_copy

# ══════════════════════════════════════════
# RUNTIME STATE
# ══════════════════════════════════════════

recent_relays = set()
route_queues  = {}
route_workers = {}

# ══════════════════════════════════════════
# ROUTE KEY  (source only — one worker per source)
# ══════════════════════════════════════════

def get_route_key(route):
    return f"{route['source_chat']}_{route.get('source_topic')}"

# ══════════════════════════════════════════
# WORKER
# Runs in its own daemon thread per route.
# Applies full pipeline to every message.
# ══════════════════════════════════════════

def route_worker(bot, route, user_settings_getter):
    """
    bot                 : telebot.TeleBot instance
    route               : route dict
    user_settings_getter: callable(uid) → settings dict
    """
    key = get_route_key(route)
    q   = route_queues[key]

    while True:
        msg = q.get()
        try:
            delay = route.get("delay", 0)
            if delay:
                time.sleep(delay)

            owner_uid = route.get("owner_uid")

            for dest in route.get("destinations", []):
                dest_chat  = dest["chat_id"]
                dest_topic = dest.get("topic_id")
                try:
                    if owner_uid:
                        s = user_settings_getter(owner_uid)
                        send_processed_file(bot, s, msg, dest_chat, dest_topic)
                    else:
                        _plain_copy(bot, dest_chat, msg, dest_topic)
                except Exception as e:
                    print(f"Worker dest error ({dest_chat}):", e)
                    try:
                        _plain_copy(bot, dest_chat, msg, dest_topic)
                    except:
                        pass

        except Exception as e:
            print("Worker error:", e)
        q.task_done()

# ══════════════════════════════════════════
# ENSURE WORKER
# ══════════════════════════════════════════

def ensure_worker(bot, route, user_settings_getter):
    key = get_route_key(route)
    if key not in route_queues:
        route_queues[key] = Queue()
    if key not in route_workers or not route_workers[key].is_alive():
        t = threading.Thread(
            target=route_worker,
            args=(bot, route, user_settings_getter),
            daemon=True
        )
        t.start()
        route_workers[key] = t

# ══════════════════════════════════════════
# PROCESS RELAY
# Called on every incoming message/channel post.
# Matches against all routes, queues matched ones.
# ══════════════════════════════════════════

def process_relay(bot, message, routes, user_settings_getter):
    global recent_relays

    src   = message.chat.id
    topic = getattr(message, "message_thread_id", None)

    # dedupe: skip messages that we just sent ourselves
    sig = f"{src}:{topic}:{message.message_id}"
    if sig in recent_relays:
        recent_relays.discard(sig)
        return

    for route in routes:
        if not route.get("enabled", True):
            continue
        if src != route["source_chat"]:
            continue
        if route.get("source_topic") is not None and topic != route["source_topic"]:
            continue

        ensure_worker(bot, route, user_settings_getter)
        route_queues[get_route_key(route)].put(message)
        
