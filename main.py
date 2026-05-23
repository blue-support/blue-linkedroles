from fastapi import FastAPI
from fastapi.responses import RedirectResponse, HTMLResponse
from dotenv import load_dotenv
import requests
import os
import json
import time
import threading
import html

# ---------------------------
# LOAD ENV
# ---------------------------
load_dotenv()

# ---------------------------
# FASTAPI
# ---------------------------
app = FastAPI()

# ---------------------------
# ENV VARIABLES
# ---------------------------
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN")

REDIRECT_URI = os.getenv("REDIRECT_URI")

GUILD_ID = os.getenv("GUILD_ID")
TEAM_ROLE_ID = os.getenv("TEAM_ROLE_ID")
OWNER_ROLE_ID = os.getenv("OWNER_ROLE_ID")

# Die Keys müssen exakt so heißen wie in deiner Metadata-/Meta-Datei.
# Wichtig: Discord erlaubt für Metadata-Keys nur a-z, 0-9 und _.
TEAM_METADATA_KEY = os.getenv("TEAM_METADATA_KEY", "teammitglied")
OWNER_METADATA_KEY = os.getenv("OWNER_METADATA_KEY", "eigentuemer")

# Optional:
# - SYNC_SECRET schützt /sync-all und /sync/{user_id}
# - AUTO_SYNC_SECONDS bestimmt, wie oft automatisch geprüft wird.
#   300 = alle 5 Minuten, 0 = automatische Prüfung aus.
SYNC_SECRET = os.getenv("SYNC_SECRET")
AUTO_SYNC_SECONDS = int(os.getenv("AUTO_SYNC_SECONDS", "300"))

DISCORD_API = "https://discord.com/api/v10"
TOKEN_STORE_FILE = os.getenv("TOKEN_STORE_FILE", "role_connection_tokens.json")

token_lock = threading.Lock()
auto_sync_started = False


# ---------------------------
# HELPERS
# ---------------------------
def now_ts() -> int:
    return int(time.time())


def safe(value) -> str:
    return html.escape(str(value))


def load_token_store() -> dict:
    if not os.path.exists(TOKEN_STORE_FILE):
        return {}

    try:
        with open(TOKEN_STORE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_token_store(store: dict) -> None:
    with open(TOKEN_STORE_FILE, "w", encoding="utf-8") as file:
        json.dump(store, file, indent=4)


def save_user_token(user_id: str, username: str, token_data: dict) -> None:
    """Speichert OAuth2-Token, damit wir die Metadata später automatisch aktualisieren können."""
    expires_in = int(token_data.get("expires_in", 604800))

    with token_lock:
        store = load_token_store()
        store[user_id] = {
            "username": username,
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_at": now_ts() + expires_in - 60,
        }
        save_token_store(store)


def refresh_access_token(user_id: str, data: dict) -> str | None:
    """Erneuert den Access-Token über den Refresh-Token."""
    refresh_token = data.get("refresh_token")

    if not refresh_token:
        return None

    token_res = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=15
    )

    if token_res.status_code != 200:
        return None

    token_data = token_res.json()
    expires_in = int(token_data.get("expires_in", 604800))

    data["access_token"] = token_data["access_token"]
    data["refresh_token"] = token_data.get("refresh_token", refresh_token)
    data["expires_at"] = now_ts() + expires_in - 60

    with token_lock:
        store = load_token_store()
        store[user_id] = data
        save_token_store(store)

    return data["access_token"]


def get_valid_access_token(user_id: str) -> str | None:
    with token_lock:
        store = load_token_store()
        data = store.get(user_id)

    if not data:
        return None

    if data.get("expires_at", 0) > now_ts():
        return data.get("access_token")

    return refresh_access_token(user_id, data)


def get_discord_user(access_token: str) -> tuple[dict | None, requests.Response]:
    user_res = requests.get(
        f"{DISCORD_API}/users/@me",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=15
    )

    if user_res.status_code != 200:
        return None, user_res

    return user_res.json(), user_res


def get_member_roles(user_id: str) -> tuple[list[str], int, str]:
    """Liest die Rollen des Users aus deinem Server. Wenn User nicht gefunden wird, werden keine Rollen gesetzt."""
    member_res = requests.get(
        f"{DISCORD_API}/guilds/{GUILD_ID}/members/{user_id}",
        headers={
            "Authorization": f"Bot {BOT_TOKEN}"
        },
        timeout=15
    )

    if member_res.status_code != 200:
        return [], member_res.status_code, member_res.text

    member = member_res.json()
    return member.get("roles", []), member_res.status_code, member_res.text


def build_metadata_from_roles(roles: list[str]) -> dict:
    has_team_role = TEAM_ROLE_ID in roles
    has_owner_role = OWNER_ROLE_ID in roles

    # Discord speichert Role-Connection-Metadata als stringifizierte Werte.
    # Für BOOLEAN_EQUAL ist 1 = true und 0 = false.
    return {
        TEAM_METADATA_KEY: "1" if has_team_role else "0",
        OWNER_METADATA_KEY: "1" if has_owner_role else "0"
    }


def update_role_connection(access_token: str, username: str, metadata: dict) -> requests.Response:
    return requests.put(
        f"{DISCORD_API}/users/@me/applications/{CLIENT_ID}/role-connection",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        },
        json={
            "platform_name": "Blue",
            "platform_username": username,
            "metadata": metadata
        },
        timeout=15
    )


def sync_user(user_id: str) -> dict:
    """Synchronisiert einen bereits verifizierten User erneut mit seinen aktuellen Discord-Rollen."""
    access_token = get_valid_access_token(user_id)

    if not access_token:
        return {
            "ok": False,
            "user_id": user_id,
            "error": "Kein gespeicherter OAuth2-Token oder Refresh fehlgeschlagen."
        }

    with token_lock:
        store = load_token_store()
        saved_user = store.get(user_id, {})

    roles, member_status, member_text = get_member_roles(user_id)
    metadata = build_metadata_from_roles(roles)

    username = saved_user.get("username", user_id)
    update_res = update_role_connection(access_token, username, metadata)

    return {
        "ok": update_res.status_code in (200, 204),
        "user_id": user_id,
        "username": username,
        "member_status": member_status,
        "metadata": metadata,
        "update_status": update_res.status_code,
        "update_text": update_res.text,
        "member_text": member_text[:500]
    }


def sync_all_users() -> list[dict]:
    with token_lock:
        store = load_token_store()
        user_ids = list(store.keys())

    results = []

    for user_id in user_ids:
        try:
            results.append(sync_user(user_id))
        except Exception as error:
            results.append({
                "ok": False,
                "user_id": user_id,
                "error": str(error)
            })

    return results


def check_sync_key(key: str | None) -> HTMLResponse | None:
    if SYNC_SECRET and key != SYNC_SECRET:
        return HTMLResponse("<h1>❌ Forbidden</h1><p>Falscher oder fehlender Sync-Key.</p>", status_code=403)

    return None


def auto_sync_loop():
    while True:
        time.sleep(AUTO_SYNC_SECONDS)

        try:
            sync_all_users()
        except Exception:
            # Absichtlich nicht crashen, damit die API weiterläuft.
            pass


@app.on_event("startup")
def start_auto_sync():
    global auto_sync_started

    if AUTO_SYNC_SECONDS <= 0 or auto_sync_started:
        return

    auto_sync_started = True
    thread = threading.Thread(target=auto_sync_loop, daemon=True)
    thread.start()


# ---------------------------
# HOME
# ---------------------------
@app.get("/")
def home():
    with token_lock:
        user_count = len(load_token_store())

    return {
        "status": "Blue Linked Roles läuft",
        "saved_users": user_count,
        "auto_sync_seconds": AUTO_SYNC_SECONDS
    }


# ---------------------------
# VERIFY
# ---------------------------
@app.get("/verify")
def verify():
    url = (
        f"{DISCORD_API}/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20role_connections.write"
    )

    return RedirectResponse(url)


# ---------------------------
# CALLBACK
# ---------------------------
@app.get("/callback")
def callback(code: str):
    # ---------------------------
    # GET ACCESS TOKEN
    # ---------------------------
    token_res = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=15
    )

    token_data = token_res.json()

    if "access_token" not in token_data:
        return HTMLResponse(
            f"<h1>❌ Token Fehler</h1><p>{safe(token_data)}</p>",
            status_code=500
        )

    access_token = token_data["access_token"]

    # ---------------------------
    # GET USER
    # ---------------------------
    user, user_res = get_discord_user(access_token)

    if not user:
        return HTMLResponse(
            f"<h1>❌ User Fehler</h1><p>Status: {user_res.status_code}</p><pre>{safe(user_res.text)}</pre>",
            status_code=500
        )

    user_id = user["id"]
    username = user.get("username", user_id)

    save_user_token(user_id, username, token_data)

    # ---------------------------
    # GET MEMBER + METADATA
    # ---------------------------
    roles, member_status, member_text = get_member_roles(user_id)

    has_team_role = TEAM_ROLE_ID in roles
    has_owner_role = OWNER_ROLE_ID in roles
    metadata = build_metadata_from_roles(roles)

    # ---------------------------
    # UPDATE ROLE CONNECTION
    # ---------------------------
    update_res = update_role_connection(access_token, username, metadata)

    # ---------------------------
    # DEBUG PAGE
    # ---------------------------
    return HTMLResponse(f"""
    <html>
        <head>
            <title>Blue Debug</title>
        </head>

        <body style="
            background-color:#0f1117;
            color:white;
            font-family:sans-serif;
            padding:40px;
        ">

            <h1>🔍 DEBUG</h1>

            <hr>

            <p><b>USER:</b> {safe(username)}</p>

            <p><b>USER ID:</b> {safe(user_id)}</p>

            <p><b>TEAM ROLE ID:</b> {safe(TEAM_ROLE_ID)}</p>

            <p><b>OWNER ROLE ID:</b> {safe(OWNER_ROLE_ID)}</p>

            <p><b>TEAM METADATA KEY:</b> {safe(TEAM_METADATA_KEY)}</p>

            <p><b>OWNER METADATA KEY:</b> {safe(OWNER_METADATA_KEY)}</p>

            <p><b>MEMBER STATUS:</b> {safe(member_status)}</p>

            <p><b>HAS TEAM ROLE:</b> {safe(has_team_role)}</p>

            <p><b>HAS OWNER ROLE:</b> {safe(has_owner_role)}</p>

            <p><b>METADATA:</b> {safe(metadata)}</p>

            <p><b>UPDATE STATUS:</b> {safe(update_res.status_code)}</p>

            <p><b>UPDATE TEXT:</b></p>

            <pre>{safe(update_res.text)}</pre>

        </body>
    </html>
    """)


# ---------------------------
# MANUAL SYNC SINGLE USER
# Beispiel:
# /sync/123456789012345678?key=DEIN_SECRET
# ---------------------------
@app.get("/sync/{user_id}")
def manual_sync_user(user_id: str, key: str | None = None):
    forbidden = check_sync_key(key)
    if forbidden:
        return forbidden

    result = sync_user(user_id)

    return HTMLResponse(f"""
    <html>
        <body style="background-color:#0f1117;color:white;font-family:sans-serif;padding:40px;">
            <h1>🔄 Sync User</h1>
            <pre>{safe(json.dumps(result, indent=4, ensure_ascii=False))}</pre>
        </body>
    </html>
    """)


# ---------------------------
# MANUAL SYNC ALL USERS
# Beispiel:
# /sync-all?key=DEIN_SECRET
# ---------------------------
@app.get("/sync-all")
def manual_sync_all(key: str | None = None):
    forbidden = check_sync_key(key)
    if forbidden:
        return forbidden

    results = sync_all_users()

    return HTMLResponse(f"""
    <html>
        <body style="background-color:#0f1117;color:white;font-family:sans-serif;padding:40px;">
            <h1>🔄 Sync All</h1>
            <p>Synchronisierte User: {safe(len(results))}</p>
            <pre>{safe(json.dumps(results, indent=4, ensure_ascii=False))}</pre>
        </body>
    </html>
    """)
