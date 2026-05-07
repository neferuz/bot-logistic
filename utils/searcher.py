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
            
        admin_ids = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
        if event.sender_id in admin_ids:
            return

        text = event.message.message
        if not text:
            return

        sender_id = event.sender_id
        chat_id = event.chat_id

        if len(text) > 1000: # Увеличили лимит, так как списки бывают длинными
            return

        # Проверка на телефон
        phone_pattern = r"(\+?\d[\s-]?){7,}"
        if not re.search(phone_pattern, text):
            return

        # Улучшенный паттерн для поиска маршрутов
        # Разрешаем любые символы (эмодзи и т.д.) между городами и разделителем
        # Разделители: -, —, →, >, =>, стрелочки и т.д.
        # Города: минимум 3 буквы
        city_p = r"[А-Яа-яA-Za-z]{3,}"
        sep_p = r"[-—→\>\=\|]+"
        
        # Паттерн для поиска всех вхождений
        # Мы ищем Город1 (возможно с мусором после него) РАЗДЕЛИТЕЛЬ Город2 (возможно с мусором после него)
        pattern = rf"({city_p})(?:[^\w\n]*?){sep_p}(?:[^\w\n]*?)({city_p})"
        
        matches = re.findall(pattern, text)
        if not matches:
            # Попробуем еще один вариант: "ГОРОД ГОРОД" (без разделителя, часто пишут капсом)
            # Но только если это похоже на маршрут (начало строки или после новой строки)
            fallback_pattern = rf"(?:^|\n)({city_p})\s+({city_p})(?:\s|\n|$)"
            matches = re.findall(fallback_pattern, text)

        if not matches:
            return

        # Get chat link once
        try:
            chat = await event.get_chat()
            chat_link = f"https://t.me/{chat.username}" if getattr(chat, 'username', None) else f"tg://resolve?id={abs(chat_id)}"
        except:
            chat_link = f"ID: {chat_id}"

        one_day_ago = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        # Анти-спам проверка один раз на сообщение
        msg_group_count = await self.db.get_message_group_count(text, one_day_ago)
        if msg_group_count >= 25: 
            return

        sender_group_count = await self.db.get_recent_sender_count(sender_id, one_day_ago)
        if sender_group_count >= 40: 
            return

        # Добавляем КАЖДЫЙ найденный маршрут как отдельную запись для поиска
        added_any = False
        for m_from, m_to in matches:
            # Очистка от мусора
            m_from = m_from.strip()
            m_to = m_to.strip()
            
            route = f"{m_from} - {m_to}"
            
            # Проверка на блеклист
            blacklist = [
                "доска", "обязателен", "груз", "нужен", "бортовой", "реф", "тент", "холодильник", 
                "ищу", "стаж", "оплата", "карта", "нал", "зарплаты", "ком", "комис", "выплат", 
                "набор", "ваканси", "работа", "трактор", "заходите", "канал", "граждан", "день",
                "проект", "подпишись", "смена", "график", "безовта", "илтимос"
            ]
            if any(word in route.lower() for word in blacklist):
                continue

            # Добавляем в базу
            await self.db.add_cargo_entry(sender_id, text, chat_link, route, event.message.id, str(chat_id))
            added_any = True
            
        if added_any:
            print(f"✅ Обработано сообщение от {sender_id}, найдено маршрутов: {len(matches)}")


    async def check_sender_validity(self, sender_id):
        # 15 groups in 5 hours
        five_hours_ago = (datetime.datetime.now() - datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        # Use our database method
        count = await self.db.get_recent_sender_count(sender_id, five_hours_ago)
        return count <= 15
