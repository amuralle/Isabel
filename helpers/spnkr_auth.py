import json
import os

from aiohttp import ClientSession
from dotenv import load_dotenv
from spnkr import AzureApp, HaloInfiniteClient, refresh_player_tokens

load_dotenv()

TOKEN_FILE = os.getenv("TOKEN_FILE_PATH", "tokens.json")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("AZURE_REDIRECT_URI", "http://localhost:8000/callback")


def load_tokens() -> dict:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    raise RuntimeError(f"No token file found at: {TOKEN_FILE}")


async def get_authenticated_client(session: ClientSession) -> HaloInfiniteClient:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("AZURE_CLIENT_ID / AZURE_CLIENT_SECRET are not configured.")

    tokens = load_tokens()
    refresh_token = tokens["refresh_token"]

    app = AzureApp(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
    player = await refresh_player_tokens(session, app, refresh_token)

    return HaloInfiniteClient(
        session=session,
        spartan_token=player.spartan_token.token,
        clearance_token=player.clearance_token.token,
        requests_per_second=5,
    )
