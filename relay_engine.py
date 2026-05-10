import threading
import time

from queue import Queue

# =========================================
# GLOBAL RUNTIME OBJECTS
# =========================================

recent_relays = set()

route_queues = {}
route_workers = {}

# =========================================
# ROUTE KEY
# =========================================

def get_route_key(route):

    return (
        f"{route['source_chat']}_"
        f"{route['source_topic']}_"
        f"{route['dest_chat']}_"
        f"{route['dest_topic']}"
    )

# =========================================
# WORKER ENGINE
# =========================================

def route_worker(bot, route):

    key = get_route_key(route)

    q = route_queues[key]

    while True:

        message = q.get()

        try:

            delay = route.get("delay", 0)

            time.sleep(delay)

            src_chat = message.chat.id

            prefix = route.get("prefix", "")

            original_caption = ""

            if hasattr(message, "caption") and message.caption:
                original_caption = message.caption

            elif hasattr(message, "text") and message.text:
                original_caption = message.text

            final_caption = (
                f"{prefix}{original_caption}"
                if prefix else original_caption
            )

            # =====================================
            # TEXT MESSAGE
            # =====================================

            if message.content_type == "text":

                if route["dest_topic"] is not None:

                    sent = bot.send_message(
                        route["dest_chat"],
                        final_caption,
                        message_thread_id=route["dest_topic"]
                    )

                else:

                    sent = bot.send_message(
                        route["dest_chat"],
                        final_caption
                    )

            # =====================================
            # MEDIA MESSAGE
            # =====================================

            else:

                if route["dest_topic"] is not None:

                    sent = bot.copy_message(
                        chat_id=route["dest_chat"],
                        from_chat_id=src_chat,
                        message_id=message.message_id,
                        message_thread_id=route["dest_topic"]
                    )

                else:

                    sent = bot.copy_message(
                        chat_id=route["dest_chat"],
                        from_chat_id=src_chat,
                        message_id=message.message_id
                    )

            recent_relays.add(
                f"{route['dest_chat']}:"
                f"{route['dest_topic']}:"
                f"{sent.message_id}"
            )

        except Exception as e:

            print("WORKER ERROR:", e)

        q.task_done()

# =========================================
# ENSURE WORKER
# =========================================

def ensure_worker(bot, route):

    key = get_route_key(route)

    if key not in route_queues:

        route_queues[key] = Queue()

    if key not in route_workers:

        t = threading.Thread(
            target=route_worker,
            args=(bot, route),
            daemon=True
        )

        t.start()

        route_workers[key] = t

# =========================================
# RELAY PROCESSOR
# =========================================

def process_relay(bot, message, routes):

    global recent_relays

    try:

        src_chat = message.chat.id

        src_topic = getattr(
            message,
            "message_thread_id",
            None
        )

        signature = (
            f"{src_chat}:"
            f"{src_topic}:"
            f"{message.message_id}"
        )

        if signature in recent_relays:
            return

        for route in routes:

            # ROUTE DISABLED
            if not route.get("enabled", True):
                continue

            # WRONG SOURCE CHAT
            if src_chat != route["source_chat"]:
                continue

            # WRONG TOPIC
            if (
                route["source_topic"] is not None
                and
                src_topic != route["source_topic"]
            ):
                continue

            ensure_worker(bot, route)

            key = get_route_key(route)

            route_queues[key].put(message)

    except Exception as e:

        print("RELAY ENGINE ERROR:", e)
