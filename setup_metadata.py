import os
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
BOT_TOKEN = os.getenv("BOT_TOKEN")

print("CLIENT_ID:", CLIENT_ID)
print("BOT_TOKEN:", BOT_TOKEN[:20] if BOT_TOKEN else "NONE")

url = f"https://discord.com/api/v10/applications/{CLIENT_ID}/role-connections/metadata"

headers = {
    "Authorization": f"Bot {BOT_TOKEN}",
    "Content-Type": "application/json"
}

json_data = [
    {
        "type": 7,
        "key": "teammitglied",
        "name": "Teammitglied",
        "description": "Diese Rolle besitzen alle Teammitglieder"
    },
    {
        "type": 7,
        "key": "eigentümer",
        "name": "Eigentümer",
        "description": "Diese Rolle besitzen alle Eigentümer"
    }
]

response = requests.put(
    url,
    headers=headers,
    json=json_data
)

print(response.status_code)
print(response.text)
