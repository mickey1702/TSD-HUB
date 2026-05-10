from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# =========================================
# MAIN CONTROL PANEL
# =========================================

def main_panel():

    kb = InlineKeyboardMarkup(row_width=2)

    kb.add(
        InlineKeyboardButton("➕ Add Route", callback_data="addroute"),
        InlineKeyboardButton("📡 View Routes", callback_data="viewroutes")
    )

    kb.add(
        InlineKeyboardButton("⏯ Toggle Route", callback_data="toggle"),
        InlineKeyboardButton("🗑 Delete Route", callback_data="delete")
    )

    kb.add(
        InlineKeyboardButton("📝 Caption Tools", callback_data="captiontools"),
        InlineKeyboardButton("📊 Statistics", callback_data="stats")
    )

    kb.add(
        InlineKeyboardButton("💾 Saved Paths", callback_data="savedpaths")
    )

    return kb

# =========================================
# BACK BUTTON
# =========================================

def back_panel():

    kb = InlineKeyboardMarkup(row_width=1)

    kb.add(
        InlineKeyboardButton("⬅ Back To Main Menu", callback_data="backmain")
    )

    return kb

# =========================================
# EMPTY SAVED PATHS PANEL
# =========================================

def saved_paths_panel():

    kb = InlineKeyboardMarkup(row_width=1)

    kb.add(
        InlineKeyboardButton("➕ Create Saved Path", callback_data="createsavedpath")
    )

    kb.add(
        InlineKeyboardButton("⬅ Back", callback_data="backmain")
    )

    return kb

# =========================================
# ROUTE MANAGEMENT PANEL
# =========================================

def route_manage_panel(route_id):

    kb = InlineKeyboardMarkup(row_width=2)

    kb.add(
        InlineKeyboardButton("⏯ Toggle", callback_data=f"toggle_{route_id}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{route_id}")
    )

    kb.add(
        InlineKeyboardButton("📝 Prefix", callback_data=f"prefix_{route_id}"),
        InlineKeyboardButton("📄 Suffix", callback_data=f"suffix_{route_id}")
    )

    kb.add(
        InlineKeyboardButton("⬅ Back", callback_data="viewroutes")
    )

    return kb
