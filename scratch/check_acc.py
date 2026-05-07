import asyncio
import os
from telethon import TelegramClient, functions, types

async def check_account(phone, api_id, api_hash):
    client = TelegramClient(f"sessions/session_{phone}", api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print(f"Account {phone} is not authorized.")
        return

    me = await client.get_me()
    print(f"Checking groups for {phone} ({me.first_name})")
    
    dialogs = await client.get_dialogs()
    groups = 0
    channels = 0
    restricted = 0
    
    for d in dialogs:
        if d.is_group:
            groups += 1
            # Try to send a message to a test entity or just check permissions
            # We won't actually send to avoid spamming
        elif d.is_channel:
            channels += 1
            
    print(f"Total Dialogs: {len(dialogs)}")
    print(f"Groups: {groups}")
    print(f"Channels: {channels}")
    
    # Check folders
    try:
        filters = await client(functions.messages.GetDialogFiltersRequest())
        print(f"Folders found: {len(filters)}")
    except Exception as e:
        print(f"Error getting filters: {e}")

if __name__ == "__main__":
    import sys
    phone = "998779714415"
    api_id = 20641234
    api_hash = "d1195ec464b8c886fb006c3a34a3a279"
    asyncio.run(check_account(phone, api_id, api_hash))
