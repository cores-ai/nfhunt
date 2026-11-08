import asyncio
import logging
from datetime import datetime, timedelta
import aiohttp
from config import CF_ACCOUNT_ID, CF_D1_DATABASE_ID, CF_API_TOKEN

logger = logging.getLogger(__name__)

def _parse_dt(val) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(val, fmt)
        except Exception:
            pass
    return None

def _deserialize_user(row: dict | None) -> dict | None:
    if not row:
        return None
    data = dict(row)
    data["created_at"] = _parse_dt(data.get("created_at"))
    data["last_active"] = _parse_dt(data.get("last_active"))
    data["unlimited_until"] = _parse_dt(data.get("unlimited_until"))
    data["last_reset_time"] = _parse_dt(data.get("last_reset_time"))
    return data

class Database:
    _session: aiohttp.ClientSession | None = None
    _initialized: bool = False
    _lock = asyncio.Lock()

    @classmethod
    async def get_session(cls) -> aiohttp.ClientSession:
        if cls._session is None or cls._session.closed:
            cls._session = aiohttp.ClientSession()
        return cls._session

    @classmethod
    async def close(cls):
        if cls._session and not cls._session.closed:
            await cls._session.close()

    @classmethod
    async def ensure_initialized(cls):
        if cls._initialized:
            return
        async with cls._lock:
            if cls._initialized:
                return
            await cls._init_table()
            cls._initialized = True

    @classmethod
    async def _init_table(cls):
        create_sql = """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            referral_count INTEGER DEFAULT 0,
            referred_by INTEGER,
            plan_name TEXT DEFAULT 'Free',
            unlimited_until TEXT,
            created_at TEXT,
            last_active TEXT,
            daily_valid_finds INTEGER DEFAULT 0,
            last_reset_time TEXT,
            chat_type TEXT DEFAULT 'private',
            total_checks INTEGER DEFAULT 0
        );
        """
        await cls.raw_execute(create_sql)
        try:
            cols_info = await cls.raw_execute("PRAGMA table_info(users)")
            existing_cols = {col.get("name") for col in cols_info} if cols_info else set()
            if "chat_type" not in existing_cols:
                await cls.raw_execute("ALTER TABLE users ADD COLUMN chat_type TEXT DEFAULT 'private'")
            if "total_checks" not in existing_cols:
                await cls.raw_execute("ALTER TABLE users ADD COLUMN total_checks INTEGER DEFAULT 0")
        except Exception:
            pass

    @classmethod
    async def raw_execute(cls, sql: str, params: list | None = None) -> list[dict]:
        if not CF_ACCOUNT_ID or not CF_D1_DATABASE_ID or not CF_API_TOKEN or "PASTE_YOUR" in CF_API_TOKEN:
            logger.error("Cloudflare D1 credentials missing in .env")
            return []

        session = await cls.get_session()
        url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_D1_DATABASE_ID}/query"
        headers = {
            "Authorization": f"Bearer {CF_API_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "sql": sql,
            "params": params or []
        }

        try:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if not data.get("success"):
                    logger.error("Cloudflare D1 Error: %s", data.get("errors"))
                    return []
                result = data.get("result", [])
                if result and isinstance(result, list):
                    return result[0].get("results", [])
                return []
        except Exception as e:
            logger.error("Cloudflare D1 request exception: %s", e)
            return []

    @classmethod
    async def execute(cls, sql: str, params: list | None = None) -> list[dict]:
        await cls.ensure_initialized()
        return await cls.raw_execute(sql, params)

    @staticmethod
    async def get_user(user_id: int):
        rows = await Database.execute("SELECT * FROM users WHERE user_id = ?", [user_id])
        if rows:
            return _deserialize_user(rows[0])
        return None

    @staticmethod
    async def get_or_create_user(user_id: int, username: str, referrer_id: int = None, chat_type: str = "private"):
        user = await Database.get_user(user_id)
        now_iso = datetime.utcnow().isoformat()
        if not user:
            await Database.execute(
                """
                INSERT OR IGNORE INTO users (
                    user_id, username, referral_count, referred_by, plan_name,
                    unlimited_until, created_at, last_active, daily_valid_finds, last_reset_time,
                    chat_type, total_checks
                ) VALUES (?, ?, 0, ?, 'Free', NULL, ?, ?, 0, ?, ?, 0)
                """,
                [user_id, username, referrer_id, now_iso, now_iso, now_iso, chat_type]
            )

            # Process referral bonus for referrer
            if referrer_id and referrer_id != user_id:
                await Database.process_referral(referrer_id)
        else:
            await Database.execute(
                "UPDATE users SET last_active = ?, username = ?, chat_type = ? WHERE user_id = ?",
                [now_iso, username, chat_type, user_id]
            )

        return await Database.get_user(user_id)

    @staticmethod
    async def process_referral(referrer_id: int):
        await Database.execute(
            "UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?",
            [referrer_id]
        )

    @staticmethod
    async def get_remaining_limit(user_id: int) -> int:
        """Returns the number of remaining limits, or -1 if unlimited."""
        user = await Database.get_user(user_id)
        if not user:
            return 0

        unlimited_until = user.get("unlimited_until")
        if unlimited_until:
            if unlimited_until > datetime.utcnow():
                return -1  # Represents unlimited
            else:
                # Plan expired! Revert to Free.
                await Database.execute(
                    "UPDATE users SET plan_name = 'Free', unlimited_until = NULL WHERE user_id = ?",
                    [user_id]
                )

        last_reset = user.get("last_reset_time") or datetime.min
        if datetime.utcnow() - last_reset > timedelta(hours=24):
            now_iso = datetime.utcnow().isoformat()
            await Database.execute(
                "UPDATE users SET daily_valid_finds = 0, last_reset_time = ? WHERE user_id = ?",
                [now_iso, user_id]
            )
            user["daily_valid_finds"] = 0

        max_limit = 10 + (user.get("referral_count", 0) * 10)
        daily_finds = user.get("daily_valid_finds", 0)
        return max(0, max_limit - daily_finds)

    @staticmethod
    async def get_time_until_reset(user_id: int) -> str:
        user = await Database.get_user(user_id)
        if not user:
            return "0h 0m"
        last_reset = user.get("last_reset_time") or datetime.utcnow()
        reset_time = last_reset + timedelta(hours=24)
        time_left = reset_time - datetime.utcnow()
        if time_left.total_seconds() <= 0:
            return "Ready now"
        hours, remainder = divmod(int(time_left.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours}h {minutes}m"

    @staticmethod
    async def decrement_batch_limit(user_id: int, count: int):
        """Increments daily_valid_finds by count if not unlimited."""
        user = await Database.get_user(user_id)
        if not user:
            return

        unlimited_until = user.get("unlimited_until")
        if unlimited_until and unlimited_until > datetime.utcnow():
            return  # Unlimited

        await Database.execute(
            "UPDATE users SET daily_valid_finds = daily_valid_finds + ? WHERE user_id = ?",
            [count, user_id]
        )

    @staticmethod
    async def check_limit(user_id: int) -> bool:
        """Returns True if the user has limits to process."""
        rem = await Database.get_remaining_limit(user_id)
        return rem != 0

    @staticmethod
    async def decrement_limit(user_id: int):
        """Increments daily_valid_finds by 1 if not unlimited."""
        await Database.decrement_batch_limit(user_id, 1)

    @staticmethod
    async def get_referral_info(user_id: int):
        user = await Database.get_user(user_id)
        if not user:
            return {"count": 0, "limit": 0, "unlimited_until": None}

        rem = await Database.get_remaining_limit(user_id)
        return {
            "count": user.get("referral_count", 0),
            "limit": rem if rem != -1 else "Unlimited",
            "unlimited_until": user.get("unlimited_until")
        }

    @staticmethod
    async def update_last_active(user_id: int, chat_type: str = "private"):
        now_iso = datetime.utcnow().isoformat()
        await Database.execute(
            "UPDATE users SET last_active = ?, chat_type = ? WHERE user_id = ?",
            [now_iso, chat_type, user_id]
        )

    @staticmethod
    async def record_activity(user_id: int, chat_type: str = "private", increment_checks: int = 0):
        """Update last active, chat_type, and increment total checks processed."""
        now_iso = datetime.utcnow().isoformat()
        if increment_checks > 0:
            await Database.execute(
                "UPDATE users SET last_active = ?, chat_type = ?, total_checks = total_checks + ? WHERE user_id = ?",
                [now_iso, chat_type, increment_checks, user_id]
            )
        else:
            await Database.execute(
                "UPDATE users SET last_active = ?, chat_type = ? WHERE user_id = ?",
                [now_iso, chat_type, user_id]
            )

    @staticmethod
    async def get_stats() -> dict:
        now = datetime.utcnow()
        m15_iso = (now - timedelta(minutes=15)).isoformat()
        h1_iso = (now - timedelta(hours=1)).isoformat()
        d1_iso = (now - timedelta(hours=24)).isoformat()
        w1_iso = (now - timedelta(days=7)).isoformat()
        m1_iso = (now - timedelta(days=30)).isoformat()
        now_iso = now.isoformat()

        total_res = await Database.execute("SELECT COUNT(*) as total FROM users")
        live_res = await Database.execute("SELECT COUNT(*) as total FROM users WHERE last_active >= ?", [m15_iso])
        h1_res = await Database.execute("SELECT COUNT(*) as total FROM users WHERE last_active >= ?", [h1_iso])
        d1_res = await Database.execute("SELECT COUNT(*) as total FROM users WHERE last_active >= ?", [d1_iso])
        w1_res = await Database.execute("SELECT COUNT(*) as total FROM users WHERE last_active >= ?", [w1_iso])
        m1_res = await Database.execute("SELECT COUNT(*) as total FROM users WHERE last_active >= ?", [m1_iso])

        dm_res = await Database.execute("SELECT COUNT(*) as total FROM users WHERE chat_type = 'private' OR chat_type IS NULL")
        grp_res = await Database.execute("SELECT COUNT(*) as total FROM users WHERE chat_type != 'private' AND chat_type IS NOT NULL")

        prem_res = await Database.execute(
            "SELECT COUNT(*) as total FROM users WHERE plan_name != 'Free' OR (unlimited_until IS NOT NULL AND unlimited_until > ?)",
            [now_iso]
        )
        finds_res = await Database.execute("SELECT SUM(daily_valid_finds) as finds, SUM(total_checks) as checks FROM users")

        total_users = total_res[0]["total"] if total_res else 0
        live_15m = live_res[0]["total"] if live_res else 0
        live_1h = h1_res[0]["total"] if h1_res else 0
        daily_active = d1_res[0]["total"] if d1_res else 0
        weekly_active = w1_res[0]["total"] if w1_res else 0
        monthly_active = m1_res[0]["total"] if m1_res else 0

        dm_users = dm_res[0]["total"] if dm_res else 0
        group_users = grp_res[0]["total"] if grp_res else 0
        premium_users = prem_res[0]["total"] if prem_res else 0
        free_users = max(0, total_users - premium_users)

        total_finds = (finds_res[0]["finds"] if finds_res and finds_res[0].get("finds") else 0) or 0
        total_checks = (finds_res[0]["checks"] if finds_res and finds_res[0].get("checks") else 0) or 0

        return {
            "total_users": total_users,
            "live_15m": live_15m,
            "live_1h": live_1h,
            "daily_active": daily_active,
            "weekly_active": weekly_active,
            "monthly_active": monthly_active,
            "dm_users": dm_users,
            "group_users": group_users,
            "premium_users": premium_users,
            "free_users": free_users,
            "total_finds_today": total_finds,
            "total_checks_all_time": total_checks
        }

    @staticmethod
    async def get_recent_users(limit: int = 10) -> list[dict]:
        rows = await Database.execute(
            "SELECT user_id, username, plan_name, last_active, chat_type, daily_valid_finds, total_checks FROM users ORDER BY last_active DESC LIMIT ?",
            [limit]
        )
        return [_deserialize_user(r) for r in rows] if rows else []

    @staticmethod
    async def get_all_user_ids() -> list[int]:
        rows = await Database.execute("SELECT user_id FROM users")
        if rows:
            return [int(r["user_id"]) for r in rows if r.get("user_id")]
        return []

    @staticmethod
    async def give_access(user_id: int, days: int, plan_name: str = "Premium") -> bool:
        user = await Database.get_user(user_id)
        if not user:
            return False

        unlimited_until = datetime.utcnow() + timedelta(days=days)
        await Database.execute(
            "UPDATE users SET unlimited_until = ?, plan_name = ? WHERE user_id = ?",
            [unlimited_until.isoformat(), plan_name, user_id]
        )
        return True
