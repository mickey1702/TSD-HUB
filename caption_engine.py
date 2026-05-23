import os
from config import DEFAULT_ORDER

# ══════════════════════════════════════════
# PIPELINE
# Applies all steps in user-defined order.
# Returns (new_filename, new_caption).
# "thumbnail" is a marker only — handled at upload.
# ══════════════════════════════════════════

def run_pipeline(s, filename, original_caption):
    """
    s               : user settings dict
    filename        : original filename string
    original_caption: original caption string or ""
    """
    orig_name    = os.path.splitext(filename)[0]   # for {original} placeholder
    cur_filename = filename
    cur_caption  = original_caption or ""

    for step in s.get("order", DEFAULT_ORDER):

        if step == "prefix_suffix":
            n, e = os.path.splitext(cur_filename)
            if s.get("prefix"):
                n = s["prefix"] + n
            if s.get("suffix"):
                n = n + s["suffix"]
            cur_filename = n + e

        elif step == "replace":
            for old, new in s.get("replace_words", {}).items():
                if old:
                    cur_filename = cur_filename.replace(old, new)
                    cur_caption  = cur_caption.replace(old, new)

        elif step == "template":
            if s.get("rename_template"):
                n, e = os.path.splitext(cur_filename)
                new_name = (s["rename_template"]
                            .replace("{filename}", n)
                            .replace("{original}", orig_name))
                cur_filename = new_name + e

        elif step == "caption":
            if s.get("caption"):
                cur_caption = (s["caption"]
                               .replace("{filename}", cur_filename)
                               .replace("{original}", original_caption or ""))

        # "thumbnail" step: marker only, applied during upload

    return cur_filename, cur_caption


# ══════════════════════════════════════════
# UPLOAD  (download → pipeline → re-upload)
# ══════════════════════════════════════════

def send_processed_file(bot, s, msg, dest_chat, dest_topic=None):
    """
    Full pipeline: download → rename → caption → thumbnail → upload.
    Falls back to plain copy for unsupported content types.
    """
    content_type = msg.content_type
    orig_caption = msg.caption or ""

    # ── resolve file ──────────────────────
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
        _plain_copy(bot, dest_chat, msg, dest_topic)
        return

    new_filename, new_caption = run_pipeline(s, original_filename, orig_caption)

    # ── download ──────────────────────────
    file_info = bot.get_file(file_id)
    raw       = bot.download_file(file_info.file_path)
    tmp_path  = f"/tmp/tsd_{new_filename}"
    with open(tmp_path, "wb") as f:
        f.write(raw)

    # ── thumbnail ─────────────────────────
    thumb = None
    if "thumbnail" in s.get("order", DEFAULT_ORDER) and s.get("thumbnail_file_id"):
        try:
            ti = bot.get_file(s["thumbnail_file_id"])
            tb = bot.download_file(ti.file_path)
            tp = "/tmp/_tsd_thumb.jpg"
            with open(tp, "wb") as tf:
                tf.write(tb)
            thumb = open(tp, "rb")
        except Exception as e:
            print("Thumb error:", e)
            thumb = None

    # ── upload ────────────────────────────
    try:
        with open(tmp_path, "rb") as f:
            kw = dict(
                chat_id    = dest_chat,
                caption    = new_caption or None,
                parse_mode = "HTML",
            )
            if dest_topic:
                kw["message_thread_id"] = dest_topic

            if s.get("send_as_document") or content_type == "document":
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


def _plain_copy(bot, dest_chat, msg, dest_topic=None):
    if dest_topic:
        bot.copy_message(dest_chat, msg.chat.id, msg.message_id,
                         message_thread_id=dest_topic)
    else:
        bot.copy_message(dest_chat, msg.chat.id, msg.message_id)
      
