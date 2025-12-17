import asyncio
import asyncpg
import os

async def test():
    DB_URL = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(DB_URL)
    count = await conn.fetchval("SELECT COUNT(*) FROM users")
    await conn.close()
    print(f"User count in DB: {count}")

asyncio.run(test())
