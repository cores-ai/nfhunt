import os
import re
from dotenv import load_dotenv, find_dotenv

# Load environment variables from .env in project root if present (no override of existing env)
load_dotenv(find_dotenv(), override=False)

def _read_token_from_env() -> str | None:
    # Support common env var names
    for key in ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TOKEN"):
        val = os.getenv(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None

def _validate_token_or_raise(token: str) -> str:
    # Basic Telegram bot token validation: <digits>:<alnum/underscore/dash>
    if not isinstance(token, str) or not token:
        raise ValueError("BOT_TOKEN is empty. Set it in your environment or .env file.")
    pattern = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
    if not pattern.match(token):
        raise ValueError("BOT_TOKEN format looks invalid. Expected '<digits>:<token>'.")
    return token

_token = _read_token_from_env()
if not _token:
    raise RuntimeError(
        "BOT_TOKEN not found. Create a .env file with 'BOT_TOKEN=YOUR_TOKEN' or set it in environment."
    )

BOT_TOKEN: str = _validate_token_or_raise(_token)

import urllib.parse

def _get_mongo_uri() -> str:
    uri = os.getenv("MONGO_URI", "")
    if uri and "://" in uri:
        try:
            scheme, rest = uri.split("://", 1)
            if "@" in rest:
                credentials, cluster = rest.rsplit("@", 1)
                if ":" in credentials:
                    user, pwd = credentials.split(":", 1)
                    user = urllib.parse.quote_plus(urllib.parse.unquote_plus(user))
                    pwd = urllib.parse.quote_plus(urllib.parse.unquote_plus(pwd))
                    uri = f"{scheme}://{user}:{pwd}@{cluster}"
        except Exception:
            pass
    return uri

MONGO_URI: str = _get_mongo_uri()
OWNER_ID: int = int(os.getenv("OWNER_ID", "7562427494"))
