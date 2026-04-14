import asyncio
import os
from telethon import TelegramClient, functions, types
from dotenv import load_dotenv

async def check_folders():
    load_dotenv()
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    
    session_dir = "sessions"
    for session_file in os.listdir(session_dir):
        if session_file.endswith(".session"):
            print(f"\n--- Checking {session_file} ---")
            client = TelegramClient(os.path.join(session_dir, session_file), api_id, api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                print("Not authorized.")
                continue
            
            try:
                filters = await client(functions.messages.GetDialogFiltersRequest())
                print(f"Total filters (folders): {len(filters)}")
                for f in filters:
                    if isinstance(f, types.DialogFilter):
                        print(f"Folder: {f.title} (ID: {f.id}), Peers: {len(f.include_peers)}")
            except Exception as e:
                print(f"Error: {e}")
            await client.disconnect()

if __name__ == "__main__":
    asyncio.run(check_folders())
