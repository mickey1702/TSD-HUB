import json
from config import ROUTE_FILE, SESSION_FILE

# =========================
# ROUTES
# =========================

def load_routes():
    try:
        with open(ROUTE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_routes(routes):
    with open(ROUTE_FILE, "w", encoding="utf-8") as f:
        json.dump(routes, f, indent=4)

# =========================
# SESSIONS
# =========================

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
            f,
            indent=4
        )
