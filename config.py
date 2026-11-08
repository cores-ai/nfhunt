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
OWNER_ID: int = int(os.getenv("OWNER_ID", "7562427494"))

# Cloudflare D1 Database Configuration
CF_ACCOUNT_ID: str = os.getenv("CF_ACCOUNT_ID", "").strip()
CF_D1_DATABASE_ID: str = os.getenv("CF_D1_DATABASE_ID", "").strip()
CF_API_TOKEN: str = os.getenv("CF_API_TOKEN", "").strip()
