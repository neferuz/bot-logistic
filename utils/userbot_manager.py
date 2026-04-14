from telethon import TelegramClient, events
import asyncio
import os
from .database import Database
import random

class UserbotManager:
    def __init__(self, api_id, api_hash, db: Database):
        self.api_id = api_id
        self.api_hash = api_hash
        self.db = db
        self.clients = []
        self.active_sessions = []

    async def init_accounts(self, session_dir="sessions"):
        if not os.path.exists(session_dir):
            os.makedirs(session_dir)

        for session_file in os.listdir(session_dir):
            if session_file.endswith(".session"):
                session_name = session_file.replace(".session", "")
                phone = session_name.replace("session_", "")
                
                client = TelegramClient(os.path.join(session_dir, session_name), self.api_id, self.api_hash)
                await client.connect()
                if await client.is_user_authorized():
                    self.clients.append(client)
                    self.active_sessions.append(session_name)
                    
                    # Синхронизация с БД
                    me = await client.get_me()
                    username = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "Unknown"
                    
                    async with self.db._connect() as db:
                        # Проверяем, есть ли уже такой аккаунт
                        async with db.execute("SELECT id FROM accounts WHERE phone = ?", (phone,)) as cursor:
                            if not await cursor.fetchone():
                                # Если нет, добавляем его владельцу-админу по умолчанию (первый в списке ADMIN_IDS)
                                admins = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
                                owner_id = admins[0] if admins else 0
                                await db.execute(
                                    "INSERT INTO accounts (phone, session_name, username, owner_id) VALUES (?, ?, ?, ?)",
                                    (phone, session_name, username, owner_id)
                                )
                                await db.commit()
                else:
                    print(f"Skipping unauthorized session: {session_file}")
        
        print(f"Initialized {len(self.clients)} userbot sessions and synced with DB.")

    async def broadcast(self, message: str, owner_id: int = None, mode: str = 'mine', delay_range=(30, 60)):
        target_clients = []
        
        async with self.db._connect() as db:
            query = "SELECT phone FROM accounts"
            params = []
            
            if mode == 'main':
                # Только счета главного владельца (Системные)
                query += " WHERE owner_id = 670031187"
            elif mode == 'mine' and owner_id:
                # Только свои счета
                query += " WHERE owner_id = ?"
                params.append(owner_id)
            elif mode == 'all' and owner_id:
                # И свои, и системные (если админ)
                query += " WHERE owner_id IN (670031187, ?)"
                params.append(owner_id)
            elif owner_id:
                # По умолчанию - свои
                query += " WHERE owner_id = ?"
                params.append(owner_id)

            async with db.execute(query, params) as cursor:
                target_phones = [row[0] for row in await cursor.fetchall()]
        
        # Нормализуем все телефоны из базы (только цифры)
        target_phones_clean = ["".join(filter(str.isdigit, p)) for p in target_phones]
        
        # Фильтруем активных клиентов по списку телефонов
        for client in self.clients:
            try:
                me = await client.get_me()
                if not me or not me.phone: continue
                
                clean_me_phone = "".join(filter(str.isdigit, me.phone))
                if clean_me_phone in target_phones_clean:
                    target_clients.append(client)
            except: pass

        if not target_clients:
            print(f"Нет активных аккаунтов для вещания (mode={mode}, owner={owner_id})")
            return

        # Для каждого аккаунта запускаем рассылку
        tasks = [self._broadcast_for_client(c, message, delay_range) for c in target_clients]
        await asyncio.gather(*tasks)

    async def _broadcast_for_client(self, client, message, delay_range):
        from telethon import functions, types
        try:
            me = await client.get_me()
            phone = me.phone if me else "Unknown"
            print(f"--- [BROADCAST] Start for {phone} ---")
            target_peers = []
            
            # Способ 1: Ищем группы в папках (Folders/Filters)
            try:
                filters = await client(functions.messages.GetDialogFiltersRequest())
                for f in filters:
                    if isinstance(f, types.DialogFilter):
                        for peer in f.include_peers:
                            target_peers.append(peer)
            
                if target_peers:
                    print(f"[{phone}] Найдено {len(target_peers)} пиров в официальных папках Telegram.")
            except Exception as e:
                print(f"[{phone}] Ошибка при получении папок: {e}")

            # Способ 2: Если папки пусты, берем ВСЕ группы и каналы, где состоит аккаунт
            if not target_peers:
                print(f"[{phone}] Папки пусты. Сканирую все диалоги (fallback)...")
                dialogs = await client.get_dialogs()
                for d in dialogs:
                    if d.is_group or d.is_channel:
                        target_peers.append(d.input_entity)
                
                if target_peers:
                    print(f"[{phone}] Найдено {len(target_peers)} групп/каналов через список диалогов.")

            if not target_peers:
                print(f"[{phone}] К сожалению, не найдено ни одной группы для рассылки.")
                return

            # Выполняем рассылку
            sent_count = 0
            for peer in target_peers:
                try:
                    await client.send_message(peer, message)
                    sent_count += 1
                    print(f"[{phone}] ✅ Отправлено в {getattr(peer, 'channel_id', getattr(peer, 'chat_id', 'unknown'))} ({sent_count}/{len(target_peers)})")
                    await asyncio.sleep(random.randint(*delay_range))
                except Exception as e:
                    print(f"[{phone}] ❌ Ошибка отправки: {e}")
                    if "flood" in str(e).lower():
                        print(f"[{phone}] ⚠️ Flood Wait! Пропускаю аккаунт.")
                        break
            
            print(f"--- [BROADCAST] Finished for {phone}. Успешно: {sent_count} ---")
            
        except Exception as e:
            print(f"--- [BROADCAST] Global error for client: {e} ---")

    async def _send_to_chunk(self, client, message, chunk, delay_range):
        for group in chunk:
            try:
                # Пытаемся сконвертировать ID в число для Telethon
                target = group
                if (group.startswith('-') and group[1:].isdigit()) or group.isdigit():
                    target = int(group)
                
                await client.send_message(target, message)
                print(f"✅ Рассылка: отправлено в {group}")
                # Anti-ban sleep
                await asyncio.sleep(random.randint(*delay_range))
            except Exception as e:
                print(f"Error sending message to {group}: {str(e)}")
                # If flood wait, handle it
                if "flood" in str(e).lower():
                    await asyncio.sleep(600)  # Sleep for 10 mins if flood detected

    async def monitor_cargo_search(self, group_ids, search_query_func):
        # Loop over clients to add handlers for incoming messages
        # Logic: If a message is from one of the groups, search for cargo by direction/keywords.
        # This will be refined in searcher.py
        pass
