import asyncio
from datetime import datetime, timedelta
import motor.motor_asyncio
from config import MONGO_URI

class Database:
    client = None
    db = None
    users_collection = None

    @classmethod
    def get_collection(cls):
        if cls.users_collection is None:
            # Initialize inside the event loop lazily
            cls.client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
            cls.db = cls.client.netflix_bot
            cls.users_collection = cls.db.users
        return cls.users_collection

    @staticmethod
    async def get_user(user_id: int):
        return await Database.get_collection().find_one({"user_id": user_id})

    @staticmethod
    async def get_or_create_user(user_id: int, username: str, referrer_id: int = None):
        coll = Database.get_collection()
        user = await coll.find_one({"user_id": user_id})
        if not user:
            user = {
                "user_id": user_id,
                "username": username,
                "referral_count": 0,
                "referred_by": referrer_id,
                "plan_name": "Free",
                "unlimited_until": None,
                "created_at": datetime.utcnow(),
                "last_active": datetime.utcnow(),
                "daily_valid_finds": 0,
                "last_reset_time": datetime.utcnow()
            }
            await coll.insert_one(user)
            
            # Process referral bonus for referrer
            if referrer_id and referrer_id != user_id:
                await Database.process_referral(referrer_id)
        else:
            await Database.update_last_active(user_id)
            updates = {}
            if "daily_valid_finds" not in user:
                updates["daily_valid_finds"] = 0
                updates["last_reset_time"] = datetime.utcnow()
            if "plan_name" not in user:
                updates["plan_name"] = "Free"
            if updates:
                await coll.update_one({"user_id": user_id}, {"$set": updates})
                
        return await coll.find_one({"user_id": user_id})

    @staticmethod
    async def process_referral(referrer_id: int):
        coll = Database.get_collection()
        referrer = await coll.find_one({"user_id": referrer_id})
        if referrer:
            new_count = referrer.get("referral_count", 0) + 1
            await coll.update_one({"user_id": referrer_id}, {"$set": {"referral_count": new_count}})

    @staticmethod
    async def get_remaining_limit(user_id: int) -> int:
        """Returns the number of remaining limits, or -1 if unlimited."""
        coll = Database.get_collection()
        user = await coll.find_one({"user_id": user_id})
        if not user:
            return 0
            
        unlimited_until = user.get("unlimited_until")
        if unlimited_until:
            if unlimited_until > datetime.utcnow():
                return -1 # Represents unlimited
            else:
                # Plan expired! Revert to Free.
                await coll.update_one(
                    {"user_id": user_id}, 
                    {"$set": {"plan_name": "Free", "unlimited_until": None}}
                )
            
        last_reset = user.get("last_reset_time", datetime.min)
        if datetime.utcnow() - last_reset > timedelta(hours=24):
            await coll.update_one(
                {"user_id": user_id},
                {"$set": {"daily_valid_finds": 0, "last_reset_time": datetime.utcnow()}}
            )
            user["daily_valid_finds"] = 0
            
        max_limit = 10 + (user.get("referral_count", 0) * 10)
        daily_finds = user.get("daily_valid_finds", 0)
        return max(0, max_limit - daily_finds)

    @staticmethod
    async def get_time_until_reset(user_id: int) -> str:
        coll = Database.get_collection()
        user = await coll.find_one({"user_id": user_id})
        if not user: return "0h 0m"
        last_reset = user.get("last_reset_time", datetime.utcnow())
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
        coll = Database.get_collection()
        user = await coll.find_one({"user_id": user_id})
        if not user:
            return
            
        unlimited_until = user.get("unlimited_until")
        if unlimited_until and unlimited_until > datetime.utcnow():
            return # Unlimited
            
        await coll.update_one({"user_id": user_id}, {"$inc": {"daily_valid_finds": count}})

    @staticmethod
    async def check_limit(user_id: int) -> bool:
        """Returns True if the user has limits to process."""
        rem = await Database.get_remaining_limit(user_id)
        return rem != 0

    @staticmethod
    async def decrement_limit(user_id: int):
        """Increments daily_valid_finds by 1 if not unlimited."""
        coll = Database.get_collection()
        user = await coll.find_one({"user_id": user_id})
        if not user:
            return
            
        unlimited_until = user.get("unlimited_until")
        if unlimited_until and unlimited_until > datetime.utcnow():
            return # Unlimited
            
        await coll.update_one({"user_id": user_id}, {"$inc": {"daily_valid_finds": 1}})

    @staticmethod
    async def get_referral_info(user_id: int):
        coll = Database.get_collection()
        user = await coll.find_one({"user_id": user_id})
        if not user:
            return {"count": 0, "limit": 0, "unlimited_until": None}
            
        rem = await Database.get_remaining_limit(user_id)
        return {
            "count": user.get("referral_count", 0),
            "limit": rem if rem != -1 else "Unlimited",
            "unlimited_until": user.get("unlimited_until")
        }

    @staticmethod
    async def update_last_active(user_id: int):
        coll = Database.get_collection()
        await coll.update_one(
            {"user_id": user_id},
            {"$set": {"last_active": datetime.utcnow()}}
        )

    @staticmethod
    async def get_stats() -> dict:
        coll = Database.get_collection()
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)
        
        total_users = await coll.count_documents({})
        weekly_active = await coll.count_documents({"last_active": {"$gte": week_ago}})
        monthly_active = await coll.count_documents({"last_active": {"$gte": month_ago}})
        
        return {
            "total_users": total_users,
            "weekly_active": weekly_active,
            "monthly_active": monthly_active
        }

    @staticmethod
    async def give_access(user_id: int, days: int, plan_name: str = "Premium") -> bool:
        coll = Database.get_collection()
        user = await coll.find_one({"user_id": user_id})
        if not user:
            return False
            
        unlimited_until = datetime.utcnow() + timedelta(days=days)
        await coll.update_one(
            {"user_id": user_id},
            {"$set": {
                "unlimited_until": unlimited_until,
                "plan_name": plan_name
            }}
        )
        return True
