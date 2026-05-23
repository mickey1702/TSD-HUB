from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import ORDER_LABELS

# ══════════════════════════════════════════
# MAIN MENU
# ══════════════════════════════════════════

def main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🚀 Add Route",      callback_data="af"),
        InlineKeyboardButton("📡 View Routes",    callback_data="routes"),
        InlineKeyboardButton("🧠 Batch Forward",  callback_data="batch"),
        InlineKeyboardButton("❌ Delete Route",   callback_data="delroute"),
        InlineKeyboardButton("⚙️ File Settings",  callback_data="settings"),
        InlineKeyboardButton("🖼 Thumbnail",       callback_data="thumb_menu"),
        InlineKeyboardButton("🔢 Set Order",       callback_data="set_order"),
        InlineKeyboardButton("📋 My Settings",    callback_data="view_settings"),
    )
    return kb

# ══════════════════════════════════════════
# SETTINGS MENU
# ══════════════════════════════════════════

def settings_menu(s):
    doc_lbl = "📄 As Document: ✅" if s.get("send_as_document") else "📄 As Document: ❌"
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

# ══════════════════════════════════════════
# THUMBNAIL MENU
# ══════════════════════════════════════════

def thumb_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👁 View Thumbnail",   callback_data="thumb_view"),
        InlineKeyboardButton("📷 Set Thumbnail",    callback_data="thumb_set"),
        InlineKeyboardButton("🗑 Remove Thumbnail", callback_data="thumb_remove"),
        InlineKeyboardButton("🔙 Back",             callback_data="back_main"),
    )
    return kb

# ══════════════════════════════════════════
# ROUTE ADD MENU
# ══════════════════════════════════════════

def route_add_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📝 Step-by-step",             callback_data="af_steps"),
        InlineKeyboardButton("⚡ All-in-one (quick entry)", callback_data="af_quick"),
        InlineKeyboardButton("🔙 Back",                     callback_data="back_main"),
    )
    return kb

# ══════════════════════════════════════════
# ADD DESTINATION PROMPT
# ══════════════════════════════════════════

def add_dest_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("➕ Add another destination", callback_data="af_add_dest"),
        InlineKeyboardButton("✅ Done, next step",         callback_data="af_dest_done"),
    )
    return kb

# ══════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════

def order_display(s):
    from config import DEFAULT_ORDER
    lines = []
    for i, step in enumerate(s.get("order", DEFAULT_ORDER), 1):
        lines.append(f"{i}. {ORDER_LABELS.get(step, step)}")
    return "\n".join(lines)

def format_settings(s):
    rw = "\n".join(
        f"  <code>{k}</code> → <code>{v if v else '∅ remove'}</code>"
        for k, v in s.get("replace_words", {}).items()
    ) or "  None"
    return (
        f"⚙️ <b>Current Settings</b>\n\n"
        f"Prefix:       <code>{s.get('prefix')      or '—'}</code>\n"
        f"Suffix:       <code>{s.get('suffix')      or '—'}</code>\n"
        f"Template:     <code>{s.get('rename_template') or '—'}</code>\n"
        f"Caption:      <code>{s.get('caption')     or '—'}</code>\n"
        f"Thumbnail:    {'✅ Set' if s.get('thumbnail_file_id') else '❌ Not set'}\n"
        f"Send As Doc:  {'✅' if s.get('send_as_document') else '❌'}\n"
        f"Destination:  <code>{s.get('destination') or '—'}</code>\n"
        f"Dest Topic:   <code>{s.get('dest_topic')  or '—'}</code>\n"
        f"Replace/Remove:\n{rw}\n\n"
        f"🔢 <b>Processing Order:</b>\n{order_display(s)}"
    )

def format_routes(routes):
    if not routes:
        return "No routes configured."
    txt = "📡 <b>ROUTES:</b>\n\n"
    for i, r in enumerate(routes, 1):
        dests = r.get("destinations", [])
        dests_txt = "\n".join(
            f"    → <code>{d['chat_id']}</code> | topic: <code>{d.get('topic_id') or '—'}</code>"
            for d in dests
        ) or "    None"
        txt += (
            f"<b>{i}.</b>\n"
            f"SRC: <code>{r['source_chat']}</code> | topic: <code>{r.get('source_topic') or '—'}</code>\n"
            f"DST:\n{dests_txt}\n"
            f"Delay: {r.get('delay', 0)}s | Owner: <code>{r.get('owner_uid', '—')}</code>\n\n"
        )
    return txt
    
