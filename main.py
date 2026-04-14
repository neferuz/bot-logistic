import asyncio
import os
from dotenv import load_dotenv
from utils.database import Database
from utils.userbot_manager import UserbotManager
from utils.admin_bot import AdminBot
from utils.searcher import CargoSearcher

import logging

async def main():
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    
    # Config
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")
    db_path = os.getenv("DB_PATH", "data/logistic_bot.db")
    
    # Ensure data directory exists
    if not os.path.exists("data"):
        os.makedirs("data")

    # Initialize DB
    db = Database(db_path)
    await db.init_db()
    
    # Initialize Userbot Manager
    userbot_mgr = UserbotManager(api_id, api_hash, db)
    await userbot_mgr.init_accounts(session_dir="sessions")
    
    if not userbot_mgr.clients:
        print("WARNING: No userbot sessions found in 'sessions/' folder. Please run session_creator.py first.")

    # Initialize Admin Bot
    admin_bot = AdminBot(bot_token, db, userbot_mgr)
    
    # Initialize Searcher
    searcher = CargoSearcher(userbot_mgr.clients, db, admin_bot)
    
    # Run everything
    print("Bot is starting...")
    
    # Start searching in background if needed
    if userbot_mgr.clients:
        asyncio.create_task(searcher.start_monitoring())
    
    # Start Admin Bot polling
    await admin_bot.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
