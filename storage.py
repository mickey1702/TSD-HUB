import json
import threading
from config import ROUTE_FILE, SESSION_FILE, SETTINGS_FILE, DEFAULT_ORDER

# ══════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════

def load_routes():
    try:
        with open(ROUTE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_routes(routes):
    with open(ROUTE_FILE, "w", encoding="utf-8") as f:
        json.dump(routes, f, indent=4)

# ══════════════════════════════════════════
# SESSIONS
# ══════════════════════════════════════════

def load_sessions():
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    except:
        return {}

def save_sessions(user_sessions):
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {str(k): v for k, v in user_sessions.items()},
            f, indent=4
        )

# ══════════════════════════════════════════
# USER SETTINGS  (debounced writes)
# save_settings() batches rapid calls into
# one disk write per 2 seconds.
# ══════════════════════════════════════════

_save_timer   = None
_save_lock    = threading.Lock()
_settings_ref = None          # set by bot.py after load

def _flush_settings():
    global _save_timer
    with _save_lock:
        _save_timer = None
        if _settings_ref is not None:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(_settings_ref, f, indent=4)

def save_settings():
    global _save_timer
    with _save_lock:
        if _save_timer is not None:
            _save_timer.cancel()
        _save_timer = threading.Timer(2.0, _flush_settings)
        _save_timer.daemon = True
        _save_timer.start()

def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def init_settings_ref(ref):
    """Call once from bot.py after loading: init_settings_ref(user_settings)"""
    global _settings_ref
    _settings_ref = ref

def get_user_settings(user_settings, uid):
    key = str(uid)
    if key not in user_settings:
        user_settings[key] = {
            "prefix":            "",
            "suffix":            "",
            "rename_template":   "",
            "caption":           "",
            "thumbnail_file_id": None,
            "send_as_document":  False,
            "replace_words":     {},
            "destination":       None,
            "dest_topic":        None,
            "order":             DEFAULT_ORDER.copy(),
        }
        save_settings()
    if "order" not in user_settings[key]:
        user_settings[key]["order"] = DEFAULT_ORDER.copy()
        save_settings()
    return user_settings[key]
    
