import os

import requests
from dotenv import load_dotenv

load_dotenv()


def resolve_xuid(gamertag: str) -> dict:
    api_key = os.getenv("XBOX_API_KEY")
    base_url = "https://xbl.io/api/v2"

    cleaned_gamertag = (gamertag or "").strip()
    if not cleaned_gamertag:
        return {"xuid": None, "error_type": "invalid_input", "error": "Gamertag is empty."}

    if not api_key:
        return {
            "xuid": None,
            "error_type": "missing_api_key",
            "error": "XBOX_API_KEY is not configured.",
        }

    url = f"{base_url}/search/{cleaned_gamertag}"
    headers = {"X-Authorization": api_key, "Accept": "*/*"}

    try:
        response = requests.get(url, headers=headers, timeout=12)
    except requests.RequestException as exc:
        return {
            "xuid": None,
            "error_type": "network_error",
            "error": f"Network error while looking up gamertag: {exc}",
        }

    try:
        data = response.json()
    except ValueError:
        data = {}

    provider_code = data.get("code")
    if provider_code and provider_code != 200:
        if provider_code == 401:
            return {
                "xuid": None,
                "error_type": "provider_auth",
                "error": "Gamertag provider returned code 401 (unauthorized API key).",
            }
        if provider_code == 429:
            return {
                "xuid": None,
                "error_type": "provider_rate_limited",
                "error": "Gamertag provider returned code 429 (rate limited).",
            }
        return {
            "xuid": None,
            "error_type": "provider_error",
            "error": f"Gamertag provider returned code {provider_code}.",
        }

    if not response.ok:
        return {
            "xuid": None,
            "error_type": "http_error",
            "error": f"HTTP {response.status_code} from gamertag provider.",
        }

    people = data.get("people")
    if not isinstance(people, list):
        content = data.get("content")
        if isinstance(content, dict):
            people = content.get("people")

    if isinstance(people, list) and people:
        xuid = people[0].get("xuid")
        if xuid:
            return {"xuid": xuid, "error_type": None, "error": None}
        return {
            "xuid": None,
            "error_type": "provider_error",
            "error": "Provider response did not include an XUID for the matched profile.",
        }

    return {
        "xuid": None,
        "error_type": "not_found",
        "error": "Gamertag not found.",
    }
