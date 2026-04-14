import aiosqlite
import datetime
import os
import difflib
from contextlib import asynccontextmanager


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path, timeout=20) as db:
            await db.create_function("LOWER", 1, lambda x: x.lower() if x else x)
            # Коэффициент подобия (0.0 - 1.0)
            await db.create_function("SIMILARITY", 2, lambda q, t: difflib.SequenceMatcher(None, q.lower() if q else "", t.lower() if t else "").ratio())
            yield db

    async def init_db(self):
        async with self._connect() as db:
            # 1. Создаем таблицы, если их нет (базовый набор)
            await db.execute('''CREATE TABLE IF NOT EXISTS groups 
                                (group_id TEXT PRIMARY KEY, username TEXT, title TEXT, folder_id TEXT)''')
            
            await db.execute('''CREATE TABLE IF NOT EXISTS users 
                                (user_id INTEGER PRIMARY KEY, username TEXT, 
                                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                                 valid_until TIMESTAMP,
                                 role TEXT DEFAULT 'user')''')

            await db.execute('''CREATE TABLE IF NOT EXISTS user_folders
                                (user_id INTEGER, folder_id TEXT, 
                                 PRIMARY KEY (user_id, folder_id))''')
            await db.execute('''CREATE TABLE IF NOT EXISTS cargo_cache
                                (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                                 route TEXT, sender_id INTEGER, chat_link TEXT, 
                                 text TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, msg_id INTEGER,
                                 group_id TEXT)''')

            await db.execute('''CREATE TABLE IF NOT EXISTS target_users
                                (user_id INTEGER PRIMARY KEY, username TEXT)''')

            await db.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT UNIQUE,
                    session_name TEXT,
                    username TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    owner_id INTEGER
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS folder_links (
                    folder_id TEXT PRIMARY KEY,
                    slug TEXT
                )
            """)

            # 2. МИГРАЦИИ (Агрессивное добавление всех возможных недостающих колонок)
            tables_cols = {
                "groups": ["username", "title", "folder_id"],
                "cargo_cache": ["route", "text", "chat_link", "msg_id", "group_id"],
                "users": ["role"],
                "accounts": ["owner_id", "username"]
            }
            
            for table, cols in tables_cols.items():
                for col in cols:
                    try:
                        await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                    except:
                        pass # Колонка уже существует
            
            await db.commit()

    async def add_user(self, user_id: int, username: str = None, days: int = 36500, role: str = 'admin'):
        expiry = datetime.datetime.now() + datetime.timedelta(days=days)
        async with self._connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, username, valid_until, role) VALUES (?, ?, ?, ?)",
                (user_id, username, expiry, role)
            )
            await db.commit()

    async def get_user_role(self, user_id: int):
        async with self._connect() as db:
            async with db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def is_authorized(self, user_id: int):
        async with self._connect() as db:
            async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone() is not None

    async def add_group(self, group_id: str, username: str = None, title: str = None):
        async with self._connect() as db:
            # Используем COALESCE или INSERT OR REPLACE для обновления полей
            await db.execute(
                "INSERT INTO groups (group_id, username, title) VALUES (?, ?, ?) "
                "ON CONFLICT(group_id) DO UPDATE SET username=excluded.username, title=excluded.title",
                (str(group_id), username, title)
            )
            await db.commit()

    async def get_all_groups(self):
        async with self._connect() as db:
            async with db.execute("SELECT group_id FROM groups") as cursor:
                return [row[0] for row in await cursor.fetchall()]

    async def add_cargo_entry(self, sender_id: int, text: str, chat_link: str, route: str, msg_id: int, group_id: str = None):
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO cargo_cache (sender_id, text, chat_link, route, msg_id, group_id) VALUES (?, ?, ?, ?, ?, ?)",
                (sender_id, text, chat_link, route, msg_id, group_id)
            )
            await db.commit()

    async def add_user_folder(self, user_id: int, folder_id: str):
        async with self._connect() as db:
            await db.execute("INSERT OR IGNORE INTO user_folders (user_id, folder_id) VALUES (?, ?)", (user_id, folder_id))
            await db.commit()

    async def get_user_folders(self, user_id: int):
        async with self._connect() as db:
            async with db.execute("SELECT folder_id FROM user_folders WHERE user_id = ?", (user_id,)) as cursor:
                return [r[0] for r in await cursor.fetchall()]

    async def batch_update_folder(self, group_ids: list, folder_id: str):
        async with self._connect() as db:
            for gid in group_ids:
                await db.execute("UPDATE groups SET folder_id = ? WHERE group_id = ?", (folder_id, str(gid)))
            await db.commit()

    async def get_folder_by_group(self, group_id: str):
        async with self._connect() as db:
            async with db.execute("SELECT folder_id FROM groups WHERE group_id = ?", (str(group_id),)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def get_folder_count(self, folder_id: str):
        async with self._connect() as db:
            async with db.execute("SELECT COUNT(*) FROM groups WHERE folder_id = ?", (folder_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_groups_in_folder(self, folder_id: str):
        async with self._connect() as db:
            # Пробуем title, если упадет - значит база совсем старая (хотя миграция выше должна помочь)
            async with db.execute("SELECT title, username FROM groups WHERE folder_id = ?", (folder_id,)) as cursor:
                return await cursor.fetchall()

    async def clear_folder(self, folder_id: str):
        async with self._connect() as db:
            await db.execute("UPDATE groups SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
            await db.commit()

    async def get_recent_sender_count(self, sender_id: int, since: str):
        async with self._connect() as db:
            async with db.execute(
                "SELECT COUNT(DISTINCT group_id) FROM cargo_cache WHERE sender_id = ? AND timestamp > ?",
                (sender_id, since)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_message_group_count(self, text: str, since: str):
        async with self._connect() as db:
            limit_text = text[:100]
            async with db.execute(
                "SELECT COUNT(DISTINCT group_id) FROM cargo_cache WHERE text LIKE ? AND timestamp > ?",
                (f"{limit_text}%", since)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_paginated_cargo(self, limit: int = 10, offset: int = 0, cargo_from: str = None, cargo_to: str = None, allowed_folders: list = None):
        async with self._connect() as db:
            # Исправленный SELECT для AdminBot: route, s_id, c_link, txt, ts, m_id, r_id
            query = """SELECT 
                        COALESCE(route, direction, ''), 
                        sender_id, 
                        chat_link, 
                        COALESCE(text, message_text, ''), 
                        timestamp, 
                        COALESCE(msg_id, message_id, 0), 
                        id 
                       FROM cargo_cache"""
            params = []
            conditions = []

            # Поиск по всем возможным полям текста и маршрута
            # Используем ИЛИ точное вхождение LIKE (по всем полям), ИЛИ коэффициент подобия > 0.8 (только по маршруту)
            search_pattern = """(
                LOWER(route) LIKE LOWER(?) OR LOWER(direction) LIKE LOWER(?) OR LOWER(text) LIKE LOWER(?) OR LOWER(message_text) LIKE LOWER(?) 
                OR SIMILARITY(?, route) > 0.8 OR SIMILARITY(?, direction) > 0.8
            )"""

            # Функция для проверки, является ли поисковый запрос "любым" (пропуск фильтра)
            def is_wildcard(s):
                if not s: return True
                return s.strip().lower() in ["любой", "все", "везде", ".", "-", "any", "all", "*"]

            if not is_wildcard(cargo_from):
                conditions.append(search_pattern)
                p = f"%{cargo_from}%"
                params.extend([p, p, p, p, cargo_from, cargo_from])
            if not is_wildcard(cargo_to):
                conditions.append(search_pattern)
                p = f"%{cargo_to}%"
                params.extend([p, p, p, p, cargo_to, cargo_to])

            if allowed_folders:
                folder_placeholders = ",".join(["?"] * len(allowed_folders))
                async with db.execute(f"SELECT group_id FROM groups WHERE folder_id IN ({folder_placeholders})", allowed_folders) as cursor:
                    group_names = [row[0] for row in await cursor.fetchall()]
                    if group_names:
                        # Формируем условие для разных форматов @link и https://t.me/link
                        group_conditions = []
                        for g in group_names:
                            clean_g = g.replace('@', '').replace('https://t.me/', '')
                            group_conditions.append("group_id LIKE ?")
                            params.append(f"%{clean_g}%")
                        conditions.append(f"({' OR '.join(group_conditions)})")
                    else:
                        conditions.append("1=0")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
                # ANTI-SPAM: Только те, кто НЕ в черном списке спамеров (массовая рассылка 30+ групп)
                query += " AND sender_id NOT IN (SELECT sender_id FROM cargo_cache WHERE timestamp > datetime('now', '-1 day') GROUP BY sender_id HAVING COUNT(DISTINCT group_id) > 30)"
            else:
                # Если условий нет, всё равно применяем анти-спам
                query += " WHERE sender_id NOT IN (SELECT sender_id FROM cargo_cache WHERE timestamp > datetime('now', '-1 day') GROUP BY sender_id HAVING COUNT(DISTINCT group_id) > 30)"

            # Группировка по тексту для удаления дублей
            query += " GROUP BY COALESCE(text, message_text, '')"
            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            async with db.execute(query, params) as cursor:
                return await cursor.fetchall()

    async def get_total_cargo_count(self, cargo_from: str = None, cargo_to: str = None, allowed_folders: list = None):
        async with self._connect() as db:
            # Считаем уникальные тексты
            query = "SELECT COUNT(DISTINCT COALESCE(text, message_text, '')) FROM cargo_cache"
            params = []
            conditions = []

            search_pattern = """(
                LOWER(route) LIKE LOWER(?) OR LOWER(direction) LIKE LOWER(?) OR LOWER(text) LIKE LOWER(?) OR LOWER(message_text) LIKE LOWER(?) 
                OR SIMILARITY(?, route) > 0.8 OR SIMILARITY(?, direction) > 0.8
            )"""

            # Функция для проверки, является ли поисковый запрос "любым"
            def is_wildcard(s):
                if not s: return True
                return s.strip().lower() in ["любой", "все", "везде", ".", "-", "any", "all", "*"]

            if not is_wildcard(cargo_from):
                conditions.append(search_pattern)
                p = f"%{cargo_from}%"
                params.extend([p, p, p, p, cargo_from, cargo_from])
            if not is_wildcard(cargo_to):
                conditions.append(search_pattern)
                p = f"%{cargo_to}%"
                params.extend([p, p, p, p, cargo_to, cargo_to])

            if allowed_folders:
                folder_placeholders = ",".join(["?"] * len(allowed_folders))
                async with db.execute(f"SELECT group_id FROM groups WHERE folder_id IN ({folder_placeholders})", allowed_folders) as cursor:
                    group_names = [row[0] for row in await cursor.fetchall()]
                    if group_names:
                        group_conditions = []
                        for g in group_names:
                            clean_g = g.replace('@', '').replace('https://t.me/', '')
                            group_conditions.append("group_id LIKE ?")
                            params.append(f"%{clean_g}%")
                        conditions.append(f"({' OR '.join(group_conditions)})")
                    else: conditions.append("1=0")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
                query += " AND sender_id NOT IN (SELECT sender_id FROM cargo_cache WHERE timestamp > datetime('now', '-1 day') GROUP BY sender_id HAVING COUNT(DISTINCT group_id) > 30)"
            else:
                query += " WHERE sender_id NOT IN (SELECT sender_id FROM cargo_cache WHERE timestamp > datetime('now', '-1 day') GROUP BY sender_id HAVING COUNT(DISTINCT group_id) > 30)"

            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_cargo_by_id(self, cargo_id: int):
        async with self._connect() as db:
            async with db.execute(
                "SELECT route, sender_id, chat_link, text, timestamp, msg_id FROM cargo_cache WHERE id = ?",
                (cargo_id,)
            ) as cursor:
                return await cursor.fetchone()

    async def get_all_users(self):
        async with self._connect() as db:
            async with db.execute("SELECT user_id, username, created_at, valid_until, role FROM users") as cursor:
                return await cursor.fetchall()

    async def remove_user(self, user_id: int):
        async with self._connect() as db:
            await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM user_folders WHERE user_id = ?", (user_id,))
            await db.commit()

    async def is_duplicate(self, text: str, route: str, since: str):
        async with self._connect() as db:
            limit_text = text[:100]
            async with db.execute(
                "SELECT id FROM cargo_cache WHERE (text LIKE ? OR route = ?) AND timestamp > ? LIMIT 1",
                (f"{limit_text}%", route, since)
            ) as cursor:
                return await cursor.fetchone() is not None

    async def update_folder_link(self, folder_id: str, slug: str):
        async with self._connect() as db:
            await db.execute("INSERT OR REPLACE INTO folder_links (folder_id, slug) VALUES (?, ?)", (folder_id, slug))
            await db.commit()

    async def get_folder_link(self, folder_id: str):
        async with self._connect() as db:
            async with db.execute("SELECT slug FROM folder_links WHERE folder_id = ?", (folder_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
    async def update_user_cache(self, user_id: int, username: str):
        if not username: return
        # Очищаем юзернейм от @ если есть
        username = username.lstrip('@').lower()
        async with self._connect() as db:
            await db.execute("INSERT OR REPLACE INTO target_users (user_id, username) VALUES (?, ?)", (user_id, username))
            await db.commit()

    async def get_user_id_by_username(self, username: str):
        username = username.lstrip('@').lower()
        async with self._connect() as db:
            async with db.execute("SELECT user_id FROM target_users WHERE username = ?", (username,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def search_user_by_username(self, username: str):
        # Поиск по частичному совпадению или по точному
        username = username.lstrip('@').lower()
        async with self._connect() as db:
            # Сначала точное совпадение
            async with db.execute("SELECT user_id FROM target_users WHERE username = ?", (username,)) as cursor:
                row = await cursor.fetchone()
                if row: return row[0]
            # Теперь неточное
            async with db.execute("SELECT user_id FROM target_users WHERE username LIKE ?", (f"%{username}%",)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
