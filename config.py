import os

BOT_TOKEN    = os.getenv("BOT_TOKEN")
ADMIN_ID     = str(os.getenv("ADMIN_ID", "0")).strip()
ROUTE_FILE   = "routes.json"
SESSION_FILE = "sessions.json"
SETTINGS_FILE = "user_settings.json"

DEFAULT_ORDER = ["prefix_suffix", "replace", "template", "caption", "thumbnail"]

ORDER_LABELS = {
    "prefix_suffix": "✏️ Prefix/Suffix",
    "replace":       "🔁 Replace/Remove",
    "template":      "📝 Rename Template",
    "caption":       "💬 Caption",
    "thumbnail":     "🖼 Thumbnail",
}

ORDER_KEYS = {
    "1": "prefix_suffix",
    "2": "replace",
    "3": "template",
    "4": "caption",
    "5": "thumbnail",
}
