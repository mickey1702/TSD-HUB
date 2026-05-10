import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = str(os.getenv("ADMIN_ID", "0")).strip()

ROUTE_FILE = "routes.json"
SESSION_FILE = "sessions.json"
