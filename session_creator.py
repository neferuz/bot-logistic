import os
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

async def create_session():
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    
    if not api_id or not api_hash:
        print("Error: Please set API_ID and API_HASH in your .env file.")
        return

    session_dir = "sessions"
    if not os.path.exists(session_dir):
        os.makedirs(session_dir)

    phone = input("Enter phone number for the new account: ")
    session_path = os.path.join(session_dir, f"session_{phone.replace('+', '')}")

    client = TelegramClient(session_path, api_id, api_hash)
    await client.start(phone=phone)
    
    print(f"Successfully logged in! Session saved to: {session_path}.session")
    await client.disconnect()

if __name__ == "__main__":
    import asyncio
    asyncio.run(create_session())
