import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiohttp import ClientSession
from dotenv import load_dotenv
from spnkr import AzureApp, HaloInfiniteClient
from spnkr.auth import halo, oauth, xbox
from spnkr.auth.core import XSTS_V3_HALO_AUDIENCE

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


TOKEN_FILE = _resolve_project_path(os.getenv("TOKEN_FILE_PATH", "tokens.json"))
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("AZURE_REDIRECT_URI", "http://localhost:8000/callback")


def load_tokens() -> dict:
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    raise RuntimeError(f"No token file found at: {TOKEN_FILE}")


def save_tokens(tokens: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = TOKEN_FILE.with_suffix(TOKEN_FILE.suffix + ".tmp")
    tmp_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(TOKEN_FILE)
    os.chmod(TOKEN_FILE, 0o600)


async def get_authenticated_client(session: ClientSession) -> HaloInfiniteClient:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("AZURE_CLIENT_ID / AZURE_CLIENT_SECRET are not configured.")

    tokens = load_tokens()
    refresh_token = tokens["refresh_token"]

    app = AzureApp(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
    oauth_token = await oauth.refresh_oauth_token(session, refresh_token, app)
    user_token = await xbox.request_user_token(session, oauth_token.access_token)
    halo_xsts = await xbox.request_xsts_token(session, user_token.token, XSTS_V3_HALO_AUDIENCE)
    spartan_token = await halo.request_spartan_token(session, halo_xsts.token)
    clearance_token = await halo.request_clearance_token(session, spartan_token.token)

    expires_in = int(oauth_token.raw.get("expires_in") or 3600)
    tokens.update(
        {
            "access_token": oauth_token.access_token,
            "refresh_token": oauth_token.refresh_token,
            "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in)).replace(tzinfo=None).isoformat(),
            "spartan_token": spartan_token.token,
            "clearance_token": clearance_token.token,
        }
    )
    save_tokens(tokens)

    return HaloInfiniteClient(
        session=session,
        spartan_token=spartan_token.token,
        clearance_token=clearance_token.token,
        requests_per_second=5,
    )
