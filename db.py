import motor.motor_asyncio
import os
from datetime import datetime
from bson import ObjectId

# === MongoDB Connection ===
MONGODB_URL = "mongodb+srv://Blinkprohjt.mongodb.net/?appName=Cler0"
client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URL)
db = client.telegram_bot  # Database name
users_collection = db.users  # Collection name

# === Default constants ===
DEFAULT_FREE_CREDITS = 200
DEFAULT_PLAN = "Free"
DEFAULT_STATUS = "Free"
DEFAULT_PLAN_EXPIRY = "N/A"
DEFAULT_KEYS_REDEEMED = 0

# === Initialize DB ===
async def init_db():
    print("✅ MongoDB connected successfully!")
    return True

# === Get or create user ===
async def get_user(user_id):
    user = await users_collection.find_one({"id": user_id})
    
    if user:
        # Ensure all fields are present
        user.setdefault("credits", DEFAULT_FREE_CREDITS)
        user.setdefault("plan", DEFAULT_PLAN)
        user.setdefault("status", DEFAULT_STATUS)
        user.setdefault("plan_expiry", DEFAULT_PLAN_EXPIRY)
        user.setdefault("keys_redeemed", DEFAULT_KEYS_REDEEMED)
        user.setdefault("custom_urls", [])
        user.setdefault("serp_key", None)
        return user
    else:
        # Create new user
        now = datetime.now().strftime('%d-%m-%Y')
        new_user = {
            "id": user_id,
            "credits": DEFAULT_FREE_CREDITS,
            "plan": DEFAULT_PLAN,
            "status": DEFAULT_STATUS,
            "plan_expiry": DEFAULT_PLAN_EXPIRY,
            "keys_redeemed": DEFAULT_KEYS_REDEEMED,
            "registered_at": now,
            "custom_urls": [],
            "serp_key": None
        }
        await users_collection.insert_one(new_user)
        return new_user

# === Update user fields ===
async def update_user(user_id: int, **kwargs):
    """Update user data in MongoDB"""
    try:
        # Ensure all fields are properly handled
        update_data = {}
        
        for key, value in kwargs.items():
            if value is not None:
                update_data[key] = value
            else:
                # Set default values for None
                if key == 'custom_urls':
                    update_data[key] = []
                elif key == 'credits':
                    update_data[key] = 0
                elif key == 'plan':
                    update_data[key] = 'Free'
                elif key == 'status':
                    update_data[key] = 'Free'
        
        # FIXED: Changed 'user_id' to 'id' to match get_user
        result = await users_collection.update_one(
            {'id': user_id},  # ✅ FIXED: 'id' not 'user_id'
            {'$set': update_data},
            upsert=True
        )
        print(f"DEBUG: Database update - User: {user_id}, Data: {update_data}, Modified: {result.modified_count}")
        return True
    except Exception as e:
        print(f"ERROR in update_user: {e}")
        return False

# === Get all users ===
async def get_all_users():
    users = []
    async for user in users_collection.find({}):
        users.append({
            "id": user.get("id"),
            "plan": user.get("plan", DEFAULT_PLAN),
            "custom_urls": user.get("custom_urls", []),
            "serp_key": user.get("serp_key")
        })
    return users

# === Get total user count ===
async def get_user_count():
    return await users_collection.count_documents({})

# === SERP key functions ===
async def set_serp_key(user_id: int, serp_key: str) -> bool:
    try:
        # Check if key already exists for another user
        existing_user = await users_collection.find_one({
            "serp_key": serp_key,
            "id": {"$ne": user_id}
        })
        
        if existing_user:
            return False
        
        # Update user's serp_key
        await users_collection.update_one(
            {"id": user_id},
            {"$set": {"serp_key": serp_key}}
        )
        return True
    except Exception as e:
        print(f"Error setting SERP key: {e}")
        return False

async def get_serp_key(user_id: int):
    user = await users_collection.find_one({"id": user_id})
    return user.get("serp_key") if user else None

async def delete_serp_key(user_id: int) -> bool:
    result = await users_collection.update_one(
        {"id": user_id},
        {"$set": {"serp_key": None}}
    )
    return result.modified_count > 0

async def serp_key_exists(serp_key: str, exclude_user: int = None) -> bool:
    query = {"serp_key": serp_key}
    if exclude_user:
        query["id"] = {"$ne": exclude_user}
    
    user = await users_collection.find_one(query)
    return bool(user)

# Alias for compatibility
clear_serp_key = delete_serp_key