import asyncio
import aiosqlite
from cyberjournal.db import DB_PATH

async def migrate():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("ALTER TABLE entries ADD COLUMN map_nonce BLOB;")
        await db.execute("ALTER TABLE entries ADD COLUMN map_ct BLOB;")
        await db.execute("ALTER TABLE entries ADD COLUMN map_format TEXT DEFAULT 'ascii';")
        await db.commit()

if __name__ == "__main__":
    asyncio.run(migrate())
