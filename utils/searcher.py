import os
import asyncio
from telethon import events, TelegramClient
from .database import Database
import datetime
import re

class CargoSearcher:
    def __init__(self, clients: list, db: Database, admin_bot_instance):
        self.clients = clients
        self.db = db
        self.admin_bot = admin_bot_instance
        self.is_monitoring = False

    async def start_monitoring(self):
        self.is_monitoring = True
        for client in self.clients:
            client.add_event_handler(self.message_handler, events.NewMessage)
        print("Cargo search monitoring started.")

    async def message_handler(self, event):
        if not self.is_monitoring:
            return

        # Исключаем сообщения от самого себя и от сервисных сообщений
        if event.is_private or event.sender_id is None:
            return
            
        # Загружаем конфиг, чтобы знать ID бота/админа
        admin_ids = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
        if event.sender_id in admin_ids:
            return

        text = event.message.message
        if not text:
            return

        sender_id = event.sender_id
        chat_id = event.chat_id

        # 1. Лимит на длину сообщения (убираем "километровые" простыни логистов)
        if len(text) > 500:
            return

        # 3. Проверка на телефон (у грузовладельца ВСЕГДА есть номер телефона)
        # Ищем последовательность из 7+ цифр (с учетом пробелов и плюса)
        phone_pattern = r"(\+?\d[\s-]?){7,}"
        if not re.search(phone_pattern, text):
            return

        # Базовая фильтрация по направлению
        pattern = r"([А-Яа-яA-Za-z]{3,})\s+[-—→]\s+([А-Яа-яA-Za-z]{3,})"
        all_matches = re.findall(pattern, text)
        if len(all_matches) > 1:
            return 

        match = re.search(pattern, text)
        
        if match:
            route = match.group(0)
            
            # Blacklist for false positives (words that are not cities)
            blacklist = [
                "доска", "обязателен", "груз", "нужен", "бортовой", "реф", "тент", "холодильник", 
                "ищу", "стаж", "оплата", "карта", "нал", "зарплаты", "ком", "комис", "выплат", 
                "набор", "ваканси", "работа", "трактор", "заходите", "канал", "граждан", "день",
                "проект", "подпишись", "смена", "график", "безовта", "илтимос", 
                "usdt", "lorry", "bulk", "corporate", "preparation", "major", "доход", "можно",
                "более", "обучение", "заработок"
            ]
            if any(word in route.lower() for word in blacklist):
                return

            # Check for mass spam (last 24 hours) based on UNIQUE group count
            one_day_ago = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

            # 1. Лимит по тексту в РАЗНЫХ группах (максимум 20 разных групп для одного текста)
            msg_group_count = await self.db.get_message_group_count(text, one_day_ago)
            if msg_group_count >= 20: 
                return

            # 2. Лимит по пользователю в РАЗНЫХ группах (максимум 30 разных групп для одного юзера)
            sender_group_count = await self.db.get_recent_sender_count(sender_id, one_day_ago)
            if sender_group_count >= 30: 
                return

            # Мы убрали 'is_duplicate', чтобы разрешить повторные сообщения от одного человека 
            # в те же группы (владельцы часто повторяют сообщения).

            # Get chat link
            try:
                chat = await event.get_chat()
                chat_link = f"https://t.me/{chat.username}" if getattr(chat, 'username', None) else f"tg://resolve?id={abs(chat_id)}"
            except:
                chat_link = f"ID: {chat_id}"

            # Add to DB
            await self.db.add_cargo_entry(sender_id, text, chat_link, route, event.message.id, str(chat_id))
            print(f"✅ В базу добавлен груз: {route}")

    async def check_sender_validity(self, sender_id):
        # 15 groups in 5 hours
        five_hours_ago = (datetime.datetime.now() - datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        # Use our database method
        count = await self.db.get_recent_sender_count(sender_id, five_hours_ago)
        return count <= 15
