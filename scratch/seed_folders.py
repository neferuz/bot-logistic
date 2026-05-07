import asyncio
import aiosqlite
import os

async def seed_folders():
    db_path = "data/logistic_bot.db"
    
    # Ensure data directory exists
    if not os.path.exists("data"):
        os.makedirs("data")

    folders = {
        '1': '8udTD2dLVkg0MjYy',
        '2': 'cI22UnY_7a84ODI6',
        '3': '_i7riJupfo8wNmUy',
        '4': 'ILdoxmzs1WE2NWIy'
    }

    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS folder_links (
                folder_id TEXT PRIMARY KEY,
                slug TEXT
            )
        """)
        
        for folder_id, slug in folders.items():
            await db.execute("INSERT OR REPLACE INTO folder_links (folder_id, slug) VALUES (?, ?)", (folder_id, slug))
            print(f"Seeded folder {folder_id} with slug {slug}")
        
        await db.commit()
    print("Database seeding completed.")

if __name__ == "__main__":
    asyncio.run(seed_folders())
