from pyrogram import Client
from config import API_ID, API_HASH

async def setup_scraper():
    print("🔧 Setting up Pyrogram scraper...")
    
    client = Client(
        "scraper_session",
        api_id=API_ID,
        api_hash=API_HASH,
        workers=1000
    )
    
    await client.start()
    print("✅ Scraper setup completed successfully!")
    print("📱 You are now logged in.")
    
    # Get session info
    me = await client.get_me()
    print(f"👤 Logged in as: {me.first_name} (@{me.username})")
    
    await client.stop()
    print("🔚 Session saved successfully!")

if __name__ == "__main__":
    import asyncio
    asyncio.run(setup_scraper())