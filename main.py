from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse
import requests
import os

app = FastAPI()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN")

REDIRECT_URI = os.getenv("REDIRECT_URI")

GUILD_ID = os.getenv("GUILD_ID")
TEAM_ROLE_ID = os.getenv("TEAM_ROLE_ID")

DISCORD_API = "https://discord.com/api/v10"


@app.get("/")
def home():
    return {"status": "Blue Linked Roles läuft"}


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


@app.get("/callback")
def callback(code: str):

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
        }
    )

    token_data = token_res.json()

    access_token = token_data["access_token"]

    user_res = requests.get(
        f"{DISCORD_API}/users/@me",
        headers={
            "Authorization": f"Bearer {access_token}"
        }
    )

    user = user_res.json()

    user_id = user["id"]

    member_res = requests.get(
        f"{DISCORD_API}/guilds/{GUILD_ID}/members/{user_id}",
        headers={
            "Authorization": f"Bot {BOT_TOKEN}"
        }
    )

    has_team_role = False

    if member_res.status_code == 200:
        member = member_res.json()

        has_team_role = TEAM_ROLE_ID in member.get("roles", [])

    metadata = {
        "teammitglied": "1" if has_team_role else "0"
    }

    update_res = requests.put(
        f"{DISCORD_API}/users/@me/applications/{CLIENT_ID}/role-connection",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        },
        json={
            "platform_name": "Blue",
            "platform_username": user["username"],
            "metadata": metadata
        }
    )

    return HTMLResponse("""
    <h1>✅ Erfolgreich verknüpft!</h1>
    <p>Du kannst dieses Fenster jetzt schließen.</p>
    """)
