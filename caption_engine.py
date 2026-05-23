import os
import traceback
from config import DEFAULT_ORDER

# ══════════════════════════════════════════
# PIPELINE
# ══════════════════════════════════════════

def run_pipeline(s, filename, original_caption):
    orig_name    = os.path.splitext(filename)[0]
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

    return cur_filename, cur_caption


# ══════════════════════════════════════════
# THUMBNAIL DOWNLOAD
# ══════════════════════════════════════════

def _get_thumb(bot, s):
    """Download thumbnail to /tmp, return open file handle or None."""
    if "thumbnail" not in s.get("order", DEFAULT_ORDER):
        return None
    if not s.get("thumbnail_file_id"):
        return None
    try:
        ti = bot.get_file(s["thumbnail_file_id"])
        tb = bot.download_file(ti.file_path)
        tp = "/tmp/_tsd_thumb.jpg"
        with open(tp, "wb") as tf:
            tf.write(tb)
        return open(tp, "rb")
    except Exception as e:
        print(f"[TSD] Thumb download error: {e}")
        return None


# ══════════════════════════════════════════
# SEND PROCESSED FILE
# ══════════════════════════════════════════

def send_processed_file(bot, s, msg, dest_chat, dest_topic=None):
    """
    Full pipeline: download → rename → caption → thumbnail → upload.
    Raises on error so caller can decide to fallback or report.
    """
    content_type = msg.content_type
    orig_caption = msg.caption or ""

    # ── resolve file info ─────────────────
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
        # unsupported type — plain copy, no error
        _plain_copy(bot, dest_chat, msg, dest_topic)
        return

    # ── run text pipeline ─────────────────
    new_filename, new_caption = run_pipeline(s, original_filename, orig_caption)
    print(f"[TSD] Pipeline: '{original_filename}' → '{new_filename}' | caption: '{new_caption[:60]}'")

    # ── download file ─────────────────────
    file_info = bot.get_file(file_id)
    raw       = bot.download_file(file_info.file_path)

    # use a safe tmp name (strip path separators)
    safe_name = new_filename.replace("/", "_").replace("\\", "_")
    tmp_path  = f"/tmp/tsd_{safe_name}"

    with open(tmp_path, "wb") as fh:
        fh.write(raw)

    # ── thumbnail ─────────────────────────
    thumb = _get_thumb(bot, s)

    # ── upload ────────────────────────────
    try:
        with open(tmp_path, "rb") as fh:
            kw = dict(
                chat_id    = dest_chat,
                caption    = new_caption if new_caption else None,
                parse_mode = "HTML",
            )
            if dest_topic:
                kw["message_thread_id"] = dest_topic

            if s.get("send_as_document") or content_type == "document":
                # For documents: pass file as InputFile tuple (bytes, filename)
                # This is how pyTelegramBotAPI sets the filename on upload
                kw["document"] = (new_filename, fh)
                if thumb:
                    kw["thumbnail"] = thumb
                bot.send_document(**kw)

            elif content_type == "video":
                kw["video"] = fh
                if thumb:
                    kw["thumbnail"] = thumb
                bot.send_video(**kw)

            elif content_type == "photo":
                kw["photo"] = fh
                bot.send_photo(**kw)

        print(f"[TSD] Upload OK → {dest_chat} topic={dest_topic}")

    except Exception as e:
        print(f"[TSD] Upload error: {e}")
        traceback.print_exc()
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
        
