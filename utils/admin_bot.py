from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import TelegramClient, functions, types as telethon_types
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError
from .database import Database
from .userbot_manager import UserbotManager
import os
import asyncio
import re
import aiosqlite
import html

class AuthStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()
    waiting_folder_selection = State() 
    waiting_folder_link = State() 
    waiting_cargo_from = State() # ПОИСК: ОТКУДА
    waiting_cargo_to = State()   # ПОИСК: КУДА
    waiting_broadcast_text = State() # РАССЫЛКА: ТЕКСТ
    waiting_grant_input = State()    # ГРАНТ: ВВОД ID/USER

# Список Супер-админов
SUPER_ADMIN_IDS = [1033031442, 8588637245, 670031187]

class AdminBot:
    def __init__(self, bot_token: str, db: Database, userbot_mgr: UserbotManager):
        self.bot = Bot(token=bot_token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.db = db
        self.userbot_mgr = userbot_mgr
        self.scheduler = AsyncIOScheduler()
        self.temp_clients = {}
        self.register_handlers()
        self.register_callbacks()
        self.scheduler.start()

        # Middleware для кэширования юзернеймов всех, кто пишет боту
        @self.dp.message.outer_middleware()
        async def cache_users_middleware(handler, event, data):
            if event.from_user:
                await self.db.update_user_cache(event.from_user.id, event.from_user.username)
            return await handler(event, data)

    def register_handlers(self):
        @self.dp.message(Command("start"))
        async def cmd_start(message: types.Message, state: FSMContext):
            await state.clear() # СБРОСИТЬ ВСЁ ПРИ START
            u_id = message.from_user.id
            u_name = message.from_user.username
            await self.db.update_user_cache(u_id, u_name)
            
            if not await self.is_authorized(u_id):
                return await message.reply(f"❌ Доступ запрещен. Твой ID: {u_id}")

            await self.update_user_commands(u_id)
            is_sup = await self.is_super_admin(u_id)
            
            if is_sup:
                await message.reply("👋 Привет, <b>Супер Админ</b>! Ты авторизован.\n\n"
                                    "📜 <b>Все команды:</b>\n"
                                    "👀 /view_cargo — последние 10 грузов\n"
                                    "📢 /broadcast [текст] — запуск рассылки\n"
                                    "📱 /list_accounts — управление всеми аккаунтами\n"
                                    "➕ /add_account — войти в новый аккаунт\n"
                                    "🏗 /manage_folders — управление папками и группами\n"
                                    "🔄 /refresh_folders — обновить группы из папок\n"
                                    "📂 /join_folder — добавить папки каналов (Addlist)\n"
                                    "➕ /add_group [ссылка] — добавить одну группу\n"
                                    "👤 /grant_access [id] — дать доступ\n"
                                    "👥 /list_users — список всех доступов\n"
                                    "🛑 /stop_broadcast — остановить всё", parse_mode="HTML")
            else:
                await message.reply("👋 Привет, <b>Админ</b>! Твой доступ активен.\n\n"
                                    "📑 <b>Твои команды:</b>\n"
                                    "👀 /view_cargo — просмотр грузов\n"
                                    "📢 /broadcast [текст] — запуск рассылки\n"
                                    "📱 /list_accounts — менеджер твоих аккаунтов\n"
                                    "➕ /add_account — добавить свой аккаунт\n"
                                    "🏗 /manage_folders — твои папки и группы\n"
                                    "🛑 /stop_broadcast — остановить рассылки", parse_mode="HTML")

        @self.dp.message(Command("grant_access"))
        async def cmd_grant_access(message: types.Message, state: FSMContext):
            await state.clear()
            if not await self.is_super_admin(message.from_user.id):
                return await message.reply("🚫 Управление доступом доступно только <b>Супер-админам</b>.", parse_mode="HTML")
            
            parts = message.text.split()
            if len(parts) < 2:
                await message.reply("👤 <b>Введите ID пользователя или @username для выдачи доступа:</b>", parse_mode="HTML")
                await state.set_state(AuthStates.waiting_grant_input)
                return
            
            await self.process_grant_by_input(message, parts[1], state)

        @self.dp.message(AuthStates.waiting_grant_input)
        async def handle_grant_input(message: types.Message, state: FSMContext):
            if message.text and message.text.startswith("/"): return
            target_input = message.text.strip()
            await state.clear()
            await self.process_grant_by_input(message, target_input, state)

        self.dp.message(Command("list_users"))(self.cmd_list_users)
        self.dp.message(Command("view_cargo"))(self.cmd_view_cargo)

        @self.dp.message(AuthStates.waiting_cargo_from)
        async def handle_cargo_from(message: types.Message, state: FSMContext):
            if message.text and message.text.startswith("/"): return
            await state.update_data(cargo_from=message.text.strip())
            await message.reply("⏳ <b>Куда?</b> (Например: Москва)\n\n<i>* Напишите <code>.</code> или <code>-</code> если город не важен</i>", parse_mode="HTML")
            await state.set_state(AuthStates.waiting_cargo_to)

        @self.dp.message(AuthStates.waiting_cargo_to)
        async def handle_cargo_to(message: types.Message, state: FSMContext):
            if message.text and message.text.startswith("/"): return
            cargo_to = message.text.strip()
            data = await state.get_data()
            cargo_from = data.get('cargo_from')
            
            await state.update_data(cargo_to=cargo_to)
            await self.send_cargo_results(message, 0, cargo_from, cargo_to)

        @self.dp.message(Command("add_group"))
        async def cmd_add_group(message: types.Message):
            if not await self.is_authorized(message.from_user.id): return
            
            parts = message.text.split()
            if len(parts) < 2: return # УБРАЛИ ТЕКСТ С ПРИМЕРОМ
            link = parts[1]
            try:
                # Временно используем первый клиент
                client = self.userbot_mgr.clients[0]
                entity = await client.get_entity(link)
                await self.db.add_group(str(entity.id), username=getattr(entity,'username',''), title=entity.title)
                await message.answer(f"✅ Группа <b>{entity.title}</b> добавлена.", parse_mode="HTML")
            except Exception as e:
                await message.answer(f"❌ Ошибка: {e}")

        @self.dp.message(Command("manage_folders"))
        async def cmd_manage_folders(message: types.Message):
            if not await self.is_authorized(message.from_user.id): return
            resp = "🏗 <b>Управление категориями папок:</b>\n\n"
            builder = InlineKeyboardBuilder()
            for i in range(1, 5):
                count = await self.db.get_folder_count(str(i))
                resp += f"📁 <b>Папка {i}:</b> {count} групп\n"
                builder.row(
                    types.InlineKeyboardButton(text=f"👁 Списки {i}", callback_data=f"fld_list_{i}_0"),
                    types.InlineKeyboardButton(text=f"🗑 Очистить {i}", callback_data=f"fld_clear_{i}")
                )
            if await self.is_super_admin(message.from_user.id):
                builder.row(types.InlineKeyboardButton(text="➕ Добавить папку по ссылке (Addlist)", callback_data="fld_manual_link"))
            await message.reply(resp, reply_markup=builder.as_markup(), parse_mode="HTML")

        @self.dp.callback_query(F.data == "fld_manual_link")
        async def process_manual_link(callback_query: types.CallbackQuery, state: FSMContext):
            await callback_query.message.edit_text("🔗 Пришлите ссылку на папку (Addlist), например:\n<code>https://t.me/addlist/slug</code>", parse_mode="HTML")
            await state.set_state("waiting_link_for_management")
            await callback_query.answer()

        @self.dp.message(StateFilter("waiting_link_for_management"))
        async def handle_manual_link(message: types.Message, state: FSMContext):
            link = message.text.strip()
            slug = link.split("/")[-1]
            await state.update_data(target_slug=slug)
            
            builder = InlineKeyboardBuilder()
            for i in range(1, 5):
                builder.add(types.InlineKeyboardButton(text=f"📁 Папка {i}", callback_data=f"fld_set_link_{i}"))
            builder.adjust(2)
            await message.reply("🎯 В какую категорию добавить группы из этой ссылки?", reply_markup=builder.as_markup())

        @self.dp.callback_query(F.data.startswith('fld_set_link_'))
        async def process_fld_set_link(callback_query: types.CallbackQuery, state: FSMContext):
            folder_id = callback_query.data.split('_')[3]
            data = await state.get_data()
            slug = data.get('target_slug')
            
            if not slug or not self.userbot_mgr.clients:
                return await callback_query.answer("⚠️ Ошибка: Сессия утеряна или нет аккаунтов.")

            await callback_query.message.edit_text(f"⏳ Вступаю в чаты и привязываю к <b>Папке {folder_id}</b>...", parse_mode="HTML")
            
            client = self.userbot_mgr.clients[0]
            try:
                check = await client(functions.chatlists.CheckChatlistInviteRequest(slug=slug))
                group_ids = [str(chat.id) for chat in check.chats]
                
                # Подготовка пиров и вступление
                peers = []
                for chat in check.chats:
                    if isinstance(chat, telethon_types.Chat): peers.append(telethon_types.InputPeerChat(chat.id))
                    else: peers.append(telethon_types.InputPeerChannel(chat.id, chat.access_hash))
                
                await client(functions.chatlists.JoinChatlistInviteRequest(slug=slug, peers=peers))
                
                # Сохранение в базу
                for chat in check.chats:
                    await self.db.add_group(str(chat.id), username=getattr(chat,'username',f"id{chat.id}"), title=getattr(chat,'title','Группа'))
                
                await self.db.batch_update_folder(group_ids, folder_id)
                await self.db.update_folder_link(folder_id, slug) # СОХРАНЯЕМ ССЫЛКУ
                await callback_query.message.edit_text(f"✅ Готово! <b>{len(group_ids)}</b> групп добавлены в <b>Папке {folder_id}</b>.", parse_mode="HTML")
            except Exception as e:
                await callback_query.message.edit_text(f"❌ Ошибка: {e}")
            
            await state.clear()
            await callback_query.answer()

        @self.dp.message(Command("list_accounts"))
        async def cmd_list_accounts(message: types.Message):
            u_id = message.from_user.id
            if not await self.is_authorized(u_id): return
            
            is_sup = await self.is_super_admin(u_id)
            
            async with self.db._connect() as db:
                if is_sup:
                    query = "SELECT phone, username, owner_id FROM accounts"
                    params = ()
                else:
                    query = "SELECT phone, username, owner_id FROM accounts WHERE owner_id = ?"
                    params = (u_id,)
                
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    
            if not rows: return await message.reply("📱 Нет добавленных аккаунтов.")
            
            resp = "📱 <b>Ваши активные аккаунты:</b>\n" if not is_sup else "📱 <b>Все активные аккаунты системы:</b>\n"
            builder = InlineKeyboardBuilder()
            
            for i, (phone, username, owner) in enumerate(rows, 1):
                name = username or "Без имени"
                resp += f"{i}. 📞 +{phone} | 👤 <b>{name}</b>\n"
                if is_sup: resp += f"   └ 🔑 <i>ID владельца: {owner}</i>\n"
                
                # Добавляем кнопку удаления только для своих аккаунтов или если супер-админ
                builder.row(types.InlineKeyboardButton(text=f"🗑 Удалить +{phone}", callback_data=f"confirm_del_{phone}"))
            
            await message.reply(resp, reply_markup=builder.as_markup(), parse_mode="HTML")

        @self.dp.message(Command("join_folder"))
        async def cmd_join_folder(message: types.Message, state: FSMContext):
            if not await self.is_authorized(message.from_user.id): return
            
            if not self.userbot_mgr.clients:
                return await message.reply("❌ Нет активных аккаунтов. Сначала добавьте их!")
            
            builder = InlineKeyboardBuilder()
            for idx, client in enumerate(self.userbot_mgr.clients):
                try:
                    me = await client.get_me()
                    builder.button(text=f"📞 +{me.phone}", callback_data=f"sel_acc_{idx}")
                except: pass
            builder.adjust(1)
            await message.reply("📲 Выберите аккаунт, которому нужно добавить папку (Addlist):", reply_markup=builder.as_markup())
            await state.set_state(AuthStates.waiting_folder_link)

        @self.dp.callback_query(F.data.startswith("sel_acc_"))
        async def callback_sel_acc(callback_query: types.CallbackQuery, state: FSMContext):
            idx = int(callback_query.data.split("_")[2])
            await state.update_data(target_acc_idx=idx)
            await callback_query.message.edit_text("🔗 Отправьте ссылку на папку (Addlist), например:\n<code>https://t.me/addlist/slug</code>", parse_mode="HTML")
            await callback_query.answer()

        @self.dp.message(AuthStates.waiting_folder_link)
        async def handle_folder_link(message: types.Message, state: FSMContext):
            if message.text and message.text.startswith("/"): return
            link = message.text.strip()
            data = await state.get_data()
            idx = data.get('target_acc_idx')
            if idx is None: return await message.reply("⚠️ Ошибка выбора аккаунта. Попробуйте /join_folder заново.")
            
            slug = link.split("/")[-1]
            await state.update_data(current_slug=slug) # Сохраняем слаг
            client = self.userbot_mgr.clients[idx]
            await message.reply(f"⏳ Начинаю вступление... Это может занять время.")
            
            try:
                check = await client(functions.chatlists.CheckChatlistInviteRequest(slug=slug))
                peers = []
                for chat in check.chats:
                    if isinstance(chat, telethon_types.Chat): peers.append(telethon_types.InputPeerChat(chat.id))
                    else: peers.append(telethon_types.InputPeerChannel(chat.id, chat.access_hash))
                
                await client(functions.chatlists.JoinChatlistInviteRequest(slug=slug, peers=peers))
                
                # Сохраняем и привязываем
                group_ids = []
                for chat in check.chats:
                    await self.db.add_group(str(chat.id), username=getattr(chat,'username',f"id{chat.id}"), title=getattr(chat,'title','Группа'))
                    group_ids.append(str(chat.id))
                
                await state.update_data(current_group_ids=group_ids)
                
                builder = InlineKeyboardBuilder()
                for i in range(1, 5):
                    builder.add(types.InlineKeyboardButton(text=f"📁 Папка {i}", callback_data=f"grp_fld_{i}"))
                builder.adjust(2)
                await message.reply(f"✅ Успешно! Добавлено <b>{len(group_ids)}</b> чатов.\nУкажите номер внутренней папки:", reply_markup=builder.as_markup(), parse_mode="HTML")
            except Exception as e:
                await message.reply(f"❌ Ошибка: {e}")
                await state.clear()

        @self.dp.message(Command("add_account"))
        async def cmd_add_account(message: types.Message, state: FSMContext):
            if not await self.is_authorized(message.from_user.id): return
            await message.reply("📲 Введите номер телефона аккаунта:")
            await state.set_state(AuthStates.waiting_phone)

        @self.dp.message(AuthStates.waiting_phone)
        async def handle_phone(message: types.Message, state: FSMContext):
            if message.text and message.text.startswith("/"): return
            phone = message.text.strip().replace(" ", "").replace("-", "")
            # Нормализуем номер для имени файла (только цифры)
            clean_phone = "".join(filter(str.isdigit, phone))
            await state.update_data(phone=phone)
            client = TelegramClient(f"sessions/session_{clean_phone}", self.userbot_mgr.api_id, self.userbot_mgr.api_hash)
            await client.connect()
            try:
                sent = await client.send_code_request(phone)
                self.temp_clients[phone] = client
                await state.update_data(phone_code_hash=sent.phone_code_hash)
                await message.reply("📩 Код отправлен. Введите его:")
                await state.set_state(AuthStates.waiting_code)
            except Exception as e:
                await message.reply(f"❌ Ошибка: {e}")
                await state.clear()

        @self.dp.message(AuthStates.waiting_code)
        async def handle_code(message: types.Message, state: FSMContext):
            if message.text and message.text.startswith("/"): return
            data = await state.get_data()
            phone = data['phone']
            code = message.text.strip()
            client = self.temp_clients.get(phone)
            if not client: return await message.reply("Ошибка сессии. Введите /add_account заново.")

            try:
                await client.sign_in(phone, code, phone_code_hash=data['phone_code_hash'])
                # Сохраняем в БД с привязкой к владельцу
                async with aiosqlite.connect(self.db.db_path) as db:
                    await db.execute("INSERT OR REPLACE INTO accounts (phone, session_name, owner_id) VALUES (?, ?, ?)",
                                     (phone, f"session_{phone}", message.from_user.id))
                    await db.commit()
                
                self.userbot_mgr.clients.append(client)
                await message.reply(f"✅ Аккаунт +{phone} успешно добавлен и привязан к вам!")
                await state.clear()
            except SessionPasswordNeededError:
                await message.reply("🔐 Введите облачный пароль (2FA):")
                await state.set_state(AuthStates.waiting_password)
            except Exception as e:
                await message.reply(f"❌ Ошибка: {e}")
                await state.clear()

        @self.dp.message(AuthStates.waiting_password)
        async def handle_password(message: types.Message, state: FSMContext):
            if message.text and message.text.startswith("/"): return
            data = await state.get_data()
            phone = data['phone']
            password = message.text.strip()
            client = self.temp_clients.get(phone)
            try:
                await client.sign_in(password=password)
                
                # Получаем имя аккаунта
                me = await client.get_me()
                username = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "Unknown"
                
                async with self.db._connect() as db:
                    await db.execute("INSERT OR REPLACE INTO accounts (phone, session_name, username, owner_id) VALUES (?, ?, ?, ?)",
                                     (phone, f"session_{phone}", username, message.from_user.id))
                    await db.commit()
                
                if client not in self.userbot_mgr.clients:
                    self.userbot_mgr.clients.append(client)
                
                await message.reply(f"✅ Аккаунт <b>{username}</b> (+{phone}) успешно добавлен!")
                await state.clear()
            except Exception as e:
                await message.reply(f"❌ Ошибка 2FA: {e}")
                await state.clear()

        @self.dp.message(Command("refresh_folders"))
        async def cmd_refresh_folders(message: types.Message):
            if not await self.is_authorized(message.from_user.id): return
            if not self.userbot_mgr.clients:
                return await message.reply("⚠️ Нет активных аккаунтов!")
            
            await message.reply("🔄 Обновляю папки (1-4)...")
            client = self.userbot_mgr.clients[0]
            report = []
            for fid in ["1", "2", "3", "4"]:
                slug = await self.db.get_folder_link(fid)
                if not slug:
                    report.append(f"📁 Папка {fid}: ссылка не задана (используй /manage_folders)")
                    continue
                try:
                    check = await client(functions.chatlists.CheckChatlistInviteRequest(slug=slug))
                    group_ids = [str(chat.id) for chat in check.chats]
                    for chat in check.chats:
                        await self.db.add_group(str(chat.id), username=getattr(chat,'username',f"id{chat.id}"), title=getattr(chat,'title','Группа'))
                    await self.db.batch_update_folder(group_ids, fid)
                    report.append(f"📁 Папка {fid}: {len(group_ids)} групп")
                except Exception as e: report.append(f"📁 Папка {fid}: ошибка ({e})")
            await message.reply("✅ <b>Обновление завершено:</b>\n\n" + "\n".join(report), parse_mode="HTML")

        @self.dp.message(Command("broadcast"))
        async def cmd_broadcast(message: types.Message, state: FSMContext):
            if not await self.is_authorized(message.from_user.id): return
            text = message.text.replace("/broadcast", "").strip()
            
            if not text:
                await message.reply("📝 <b>Введите текст для рассылки:</b>\n\n<i>Это сообщение увидят все участники групп.</i>", parse_mode="HTML")
                await state.set_state(AuthStates.waiting_broadcast_text)
                return

            await self.show_broadcast_options(message, text)

        @self.dp.message(AuthStates.waiting_broadcast_text)
        async def handle_broadcast_text(message: types.Message, state: FSMContext):
            if message.text and message.text.startswith("/"): return
            text = message.text.strip()
            await state.clear()
            await self.show_broadcast_options(message, text)

        @self.dp.message(Command("stop_broadcast"))
        async def cmd_stop_broadcast(message: types.Message):
            u_id = message.from_user.id
            if not await self.is_authorized(u_id): return
            
            jobs = self.scheduler.get_jobs()
            # Показываем только его задачи (или все, если супер)
            is_sup = await self.is_super_admin(u_id)
            user_jobs = [j for j in jobs if j.id.startswith("br_")] if is_sup else [j for j in jobs if j.id.startswith(f"br_{u_id}_")]
            
            if not user_jobs:
                return await message.reply("📢 <b>Активных рассылок не найдено.</b>", parse_mode="HTML")
            
            resp = "📢 <b>УПРАВЛЕНИЕ РАССЫЛКАМИ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
            builder = InlineKeyboardBuilder()
            
            for j in user_jobs:
                parts = j.id.split('_')
                # Формат ID: br_{u_id}_{h}_{ts}
                h = parts[2] if len(parts) > 2 else "?"
                owner = parts[1] if len(parts) > 1 else "Syst"
                
                info = f"👤 [{owner}] " if is_sup else ""
                resp += f"{info}🕒 Интервал: <b>раз в {h} ч.</b>\n"
                builder.row(types.InlineKeyboardButton(text=f"🛑 Стоп {h}ч ({owner if is_sup else ''})", callback_data=f"stop_job_{j.id}"))
            
            builder.row(types.InlineKeyboardButton(text="🚫 ОСТАНОВИТЬ ВСЕ МОИ", callback_data="stop_all_my_jobs"))
            await message.reply(resp, reply_markup=builder.as_markup(), parse_mode="HTML")

        @self.dp.callback_query(F.data.startswith('stop_job_'))
        async def cb_stop_specific_job(cb: types.CallbackQuery):
            j_id = cb.data.replace('stop_job_', '')
            try:
                self.scheduler.remove_job(j_id)
                await cb.message.edit_text(f"✅ Задача <code>{j_id}</code> успешно остановлена.", parse_mode="HTML")
            except:
                await cb.answer("⚠️ Задача уже была остановлена или не найдена.", show_alert=True)
            await cb.answer()

        @self.dp.callback_query(F.data == 'stop_all_my_jobs')
        async def cb_stop_all_my_jobs(cb: types.CallbackQuery):
            u_id = cb.from_user.id
            jobs = self.scheduler.get_jobs()
            is_sup = await self.is_super_admin(u_id)
            
            count = 0
            for j in jobs:
                if is_sup and j.id.startswith("br_"):
                    self.scheduler.remove_job(j.id)
                    count += 1
                elif j.id.startswith(f"br_{u_id}_"):
                    self.scheduler.remove_job(j.id)
                    count += 1
            
            await cb.message.edit_text(f"🛑 Остановлено активных рассылок: <b>{count}</b>", parse_mode="HTML")
            await cb.answer()



        @self.dp.callback_query(F.data.startswith('usr_del_conf_'))
        async def process_usr_del_conf(cb: types.CallbackQuery):
            target_id = cb.data.split('_')[3]
            builder = InlineKeyboardBuilder()
            builder.row(
                types.InlineKeyboardButton(text="✅ ДА, УБРАТЬ", callback_data=f"usr_del_final_{target_id}"),
                types.InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="back_to_users")
            )
            await cb.message.edit_text(f"❓ <b>Вы точно хотите отозвать доступ у {target_id}?</b>", 
                                      reply_markup=builder.as_markup(), parse_mode="HTML")
            await cb.answer()

        @self.dp.callback_query(F.data == "back_to_users")
        async def handle_back_users_list(cb: types.CallbackQuery):
            # Перенаправляем на команду списка без создания нового сообщения
            await cb.message.delete()
            await cmd_list_users(cb.message)
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('usr_del_final_'))
        async def process_usr_del_final(cb: types.CallbackQuery):
            t_id = int(cb.data.split('_')[3])
            # Единственный аккаунт, который НЕЛЬЗЯ удалить ни при каких условиях
            if t_id == 670031187:
                return await cb.answer("🚫 Это основной владелец. Его нельзя удалить!", show_alert=True)
            
            await self.db.remove_user(t_id)
            await cb.message.edit_text(f"🗑 Доступ для ID <code>{t_id}</code> полностью аннулирован.", parse_mode="HTML")
            await cb.answer()


        @self.dp.callback_query(F.data.startswith("role_set_"))
        async def process_role_set(cb: types.CallbackQuery, state: FSMContext):
            role_map = {"admin": "admin", "super": "super_admin"}
            role_key = cb.data.split("_")[2]
            role = role_map.get(role_key)
            
            data = await state.get_data()
            target_id = data.get('grant_target_id')
            
            if not target_id: return await cb.answer("Ошибка: ID не найден.")
            
            await self.db.add_user(int(target_id), role=role)
            await self.update_user_commands(int(target_id))
            role_name = "Обычный Админ" if role == 'admin' else "Супер Админ"
            await cb.message.edit_text(f"✅ Доступ выдан!\n👤 ID: <code>{target_id}</code>\n🎭 Роль: <b>{role_name}</b>", parse_mode="HTML")
            await state.clear()
            await cb.answer()

        @self.dp.message(F.text.startswith('/view_'))
        async def cmd_view_detail(message: types.Message):
            if not await self.is_authorized(message.from_user.id): return
            try:
                c_id = int(message.text.replace('/view_', ''))
                c = await self.db.get_cargo_by_id(c_id)
                if not c: return
                route, s_id, c_link, txt, ts, m_id = c
                msg_link = f"{c_link.strip('/')}/{m_id}" if ("t.me/" in c_link and m_id) else None
                builder = InlineKeyboardBuilder()
                if msg_link: builder.row(types.InlineKeyboardButton(text="🔗 Пост", url=msg_link))
                builder.row(types.InlineKeyboardButton(text="👤 Юзер", url=f"tg://user?id={s_id}"))
                await message.answer(f"📍 <b>{route}</b>\n📅 {ts}\n\n{html.escape(txt)}", reply_markup=builder.as_markup(), parse_mode="HTML")
            except: pass

    def register_callbacks(self):
        @self.dp.callback_query(F.data.startswith('fld_list_'))
        async def process_fld_list(callback_query: types.CallbackQuery):
            parts = callback_query.data.split('_')
            folder_id = parts[2]
            page = int(parts[3]) if len(parts) > 3 else 0
            groups = await self.db.get_groups_in_folder(folder_id)
            if not groups: return await callback_query.answer("📁 Пусто", show_alert=True)
            
            per_page = 10
            total_pages = (len(groups) + per_page - 1) // per_page
            p_groups = groups[page*per_page : (page+1)*per_page]
            
            resp = f"✨ <b>Папка {folder_id}</b> (Стр. {page+1}/{total_pages})\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
            for i, (title, username) in enumerate(p_groups, page*per_page + 1):
                safe_t = html.escape(str(title))
                if username and username.startswith('@'):
                    resp += f"{i}. 🔗 <a href='https://t.me/{username[1:]}'>{safe_t}</a>\n"
                else: resp += f"{i}. 🔘 {safe_t} (<code>{username}</code>)\n"
            
            builder = InlineKeyboardBuilder()
            row = []
            if page > 0: row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"fld_list_{folder_id}_{page-1}"))
            if (page+1)*per_page < len(groups): row.append(types.InlineKeyboardButton(text="Вперед ➡️", callback_data=f"fld_list_{folder_id}_{page+1}"))
            if row: builder.row(*row)
            builder.row(types.InlineKeyboardButton(text="🔙 К папкам", callback_data="back_to_folders"))
            
            try: await callback_query.message.edit_text(resp, reply_markup=builder.as_markup(), parse_mode="HTML", disable_web_page_preview=True)
            except: pass
            await callback_query.answer()

        @self.dp.callback_query(F.data == "back_to_folders")
        async def handle_back(cb: types.CallbackQuery):
            resp = "🏗 <b>Управление категориями папок:</b>\n\n"
            builder = InlineKeyboardBuilder()
            for i in range(1, 5):
                count = await self.db.get_folder_count(str(i))
                resp += f"📁 <b>Папка {i}:</b> {count} групп\n"
                builder.row(types.InlineKeyboardButton(text=f"👁 Списки {i}", callback_data=f"fld_list_{i}_0"),
                            types.InlineKeyboardButton(text=f"🗑 Очистить {i}", callback_data=f"fld_clear_{i}"))
            if await self.is_super_admin(cb.from_user.id):
                builder.row(types.InlineKeyboardButton(text="➕ Добавить папку по ссылке (Addlist)", callback_data="fld_manual_link"))
            await cb.message.edit_text(resp, reply_markup=builder.as_markup(), parse_mode="HTML")
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('confirm_del_'))
        async def process_confirm_del(cb: types.CallbackQuery):
            phone = cb.data.split('_')[2]
            builder = InlineKeyboardBuilder()
            builder.row(
                types.InlineKeyboardButton(text="✅ ДА, УДАЛИТЬ", callback_data=f"del_acc_final_{phone}"),
                types.InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="back_to_accounts")
            )
            await cb.message.edit_text(f"❓ <b>Вы точно хотите полностью удалить аккаунт +{phone}?</b>\n\nЭту сессию нельзя будет восстановить без повторного входа.", 
                                      reply_markup=builder.as_markup(), parse_mode="HTML")
            await cb.answer()

        @self.dp.callback_query(F.data == "back_to_accounts")
        async def handle_back_acc(cb: types.CallbackQuery):
            await cmd_list_accounts(cb.message)
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('del_acc_final_'))
        async def process_final_del(cb: types.CallbackQuery):
            phone = cb.data.split('_')[3]
            
            # 1. Поиск и отключение в памяти
            target_client = None
            for client in self.userbot_mgr.clients:
                try:
                    if not client.is_connected(): continue
                    me = await client.get_me()
                    if me and getattr(me, 'phone', None) == phone:
                        target_client = client
                        break
                except: pass
            
            if target_client:
                try:
                    await target_client.disconnect()
                    if target_client in self.userbot_mgr.clients:
                        self.userbot_mgr.clients.remove(target_client)
                except: pass
            
            # 2. Удаление из базы данных
            async with self.db._connect() as db:
                await db.execute("DELETE FROM accounts WHERE phone = ?", (phone,))
                await db.commit()
            
            # 3. Удаление файла сессии
            session_path = f"sessions/session_{phone}.session"
            if os.path.exists(session_path): 
                try: os.remove(session_path)
                except: pass
            
            await cb.message.edit_text(f"🗑 Аккаунт <b>+{phone}</b> полностью удален из системы и базы данных.", parse_mode="HTML")
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('cargo_search_'))
        async def cb_cargo_search(cb: types.CallbackQuery, state: FSMContext):
            page = int(cb.data.split('_')[2])
            data = await state.get_data()
            cargo_from = data.get('cargo_from')
            cargo_to = data.get('cargo_to')
            await self.send_cargo_results(cb, page, cargo_from, cargo_to)
            await cb.answer()

        @self.dp.callback_query(lambda c: c.data.startswith('cargo_page_'))
        async def process_cb_cargo(cb: types.CallbackQuery):
            await self.send_cargo_page(cb, int(cb.data.split('_')[2]))
            await cb.answer()

        @self.dp.callback_query(F.data == 'br_once')
        async def cb_br_once(cb: types.CallbackQuery, state: FSMContext):
            data = await state.get_data()
            mode = data.get('broadcast_mode', 'mine')
            await cb.message.edit_text(f"🚀 Запуск рассылки (режим: {mode})...")
            asyncio.create_task(self.userbot_mgr.broadcast(self.last_broadcast_text, owner_id=cb.from_user.id, mode=mode))
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('grp_fld_'))
        async def cb_grp_fld(cb: types.CallbackQuery, state: FSMContext):
            f_id = cb.data.split('_')[2]
            data = await state.get_data()
            g_ids = data.get('current_group_ids')
            slug = data.get('current_slug')
            
            if not g_ids: return await cb.answer("Ошибка данных.")
            
            await self.db.batch_update_folder(g_ids, f_id)
            if slug: await self.db.update_folder_link(f_id, slug) # Сохраняем ссылку для refresh
            
            await cb.message.edit_text(f"✅ Готово! {len(g_ids)} групп привязаны к <b>Папке {f_id}</b>.", parse_mode="HTML")
            await state.clear()
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('br_sch_'))
        async def cb_br_sch(cb: types.CallbackQuery, state: FSMContext):
            h = int(cb.data.split('_')[2])
            u_id = cb.from_user.id
            data = await state.get_data()
            mode = data.get('broadcast_mode', 'mine')
            
            j_id = f"br_{u_id}_{h}_{int(asyncio.get_event_loop().time())}"
            self.scheduler.add_job(
                self.userbot_mgr.broadcast, 
                'interval', 
                hours=h, 
                args=[self.last_broadcast_text, u_id, mode], 
                id=j_id
            )
            await cb.message.edit_text(f"🕒 Рассылка (режим: {mode}) запланирована раз в {h} ч.")
            asyncio.create_task(self.userbot_mgr.broadcast(self.last_broadcast_text, owner_id=u_id, mode=mode))
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('br_mode_'))
        async def cb_br_mode(cb: types.CallbackQuery, state: FSMContext):
            mode = cb.data.split('_')[2]
            await state.update_data(broadcast_mode=mode)
            
            builder = InlineKeyboardBuilder()
            builder.row(types.InlineKeyboardButton(text="🚀 Запустить Разово", callback_data="br_once"))
            for h in range(1, 7):
                builder.add(types.InlineKeyboardButton(text=f"⏳ {h}ч", callback_data=f"br_sch_{h}"))
            builder.adjust(1, 3, 3)
            
            mode_text = {
                'main': "📲 ГЛАВНЫЕ (Системные)",
                'mine': "📱 МОИ (Вторичные)",
                'all': "🔄 ОБА (Все сразу)"
            }.get(mode, "Выбранные")
            
            await cb.message.edit_text(f"📈 <b>Настройка интервала</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
                                f"🤖 <b>Режим:</b> {mode_text}\n"
                                f"📝 <b>Текст:</b> <i>{self.last_broadcast_text}</i>\n\n"
                                f"Выберите период для рассылки:", 
                                reply_markup=builder.as_markup(), parse_mode="HTML")
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('view_det_'))
        async def cb_view_detail(cb: types.CallbackQuery):
            await cb.answer() # Отвечаем сразу, чтобы убрать "часики"
            try:
                c_id = int(cb.data.replace('view_det_', ''))
                c = await self.db.get_cargo_by_id(c_id)
                if not c: return
                
                route, s_id, c_link, txt, ts, m_id = c
                msg_link = f"{c_link.strip('/')}/{m_id}" if ("t.me/" in c_link and m_id) else None
                
                builder = InlineKeyboardBuilder()
                if msg_link: 
                    builder.row(types.InlineKeyboardButton(text="🔗 Открыть оригинал", url=msg_link))
                
                # Текст объявления (с обрезкой для безопасности)
                safe_txt = html.escape(txt)
                if len(safe_txt) > 3000: safe_txt = safe_txt[:3000] + "..."
                
                resp = f"📋 <b>ДЕТАЛИ ОБЪЯВЛЕНИЯ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
                resp += f"📍 <b>Маршрут:</b> {route}\n"
                resp += f"📅 <b>Дата:</b> {ts}\n"
                resp += f"👤 <b>Владелец:</b> <a href=\"tg://user?id={s_id}\">Написать (ID: {s_id})</a>\n\n"
                resp += f"📝 <b>Текст:</b>\n<i>{safe_txt}</i>"
                
                await cb.message.answer(resp, reply_markup=builder.as_markup(), parse_mode="HTML")
            except Exception as e:
                print(f"Callback Error: {e}")

        @self.dp.callback_query(F.data.startswith('fld_clear_'))
        async def cb_clear(cb: types.CallbackQuery):
            fid = cb.data.split('_')[2]
            await self.db.clear_folder(fid)
            await cb.message.edit_text(f"🗑 Папка {fid} очищена.")
            await cb.answer()

        @self.dp.callback_query(F.data == "back_to_users")
        async def cb_back_to_users(cb: types.CallbackQuery, state: FSMContext):
            await cb.message.delete()
            await self.cmd_list_users(cb.message, state) # ИСПРАВЛЕНО
            await cb.answer()

        @self.dp.callback_query(F.data.startswith('usr_fld_'))
        async def cb_usr_fld(cb: types.CallbackQuery):
            p = cb.data.split('_')
            target_id = int(p[2])
            fid = p[3]
            
            # Проверяем, есть ли уже эта папка
            u_f = await self.db.get_user_folders(target_id)
            if fid in u_f:
                # Временно: удаление не реализовано в Database, но мы можем просто переназначить или добавить метод
                # Но для MVP просто добавим. Имена папок "1", "2" и т.д.
                await cb.answer(f"Папка {fid} уже добавлена.")
            else:
                await self.db.add_user_folder(target_id, fid)
                await cb.answer(f"➕ Добавлена Папка {fid}")
            
            # Обновляем список пользователей
            await self.cmd_list_users(cb.message, state) # ИСПРАВЛЕНО

    async def cmd_list_users(self, message: types.Message, state: FSMContext = None):
        if not await self.is_super_admin(message.from_user.id):
            return await message.reply("🚫 Список админов виден только <b>Супер-админам</b>.", parse_mode="HTML")
        
        users = await self.db.get_all_users()
        if not users: return await message.answer("👥 <b>Доступов нет.</b>", parse_mode="HTML")
        
        resp = "👥 <b>СПИСОК ДОСТУПОВ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        builder = InlineKeyboardBuilder()
        for u_id, u_name, c_at, v_ut, role in users:
            try:
                chat = await self.bot.get_chat(u_id)
                f_name = chat.full_name
                u_link = f" (@{chat.username})" if chat.username else ""
            except:
                f_name = u_name or "ID"
                u_link = ""
            
            is_sup = (int(u_id) in SUPER_ADMIN_IDS) or (role == 'super_admin')
            label = "👑 Супер Админ" if is_sup else "👤 Обычный Админ"
            resp += f"🆔 <code>{u_id}</code> | <b>{f_name}</b>{u_link}\n🎭 Статус: {label}\n\n"
            
            builder.row(types.InlineKeyboardButton(text=f"🗑 Убрать доступ {u_id}", callback_data=f"usr_del_conf_{u_id}"))
        
        await message.answer(resp, reply_markup=builder.as_markup(), parse_mode="HTML")

    async def cmd_view_cargo(self, message: types.Message, state: FSMContext):
        await state.clear()
        if not await self.is_authorized(message.from_user.id): return
        
        await message.answer("⏳ <b>Откуда?</b> (Например: Ташкент)\n\n<i>* Напишите <code>.</code> или <code>-</code> если город не важен</i>", parse_mode="HTML")
        await state.set_state(AuthStates.waiting_cargo_from)

    async def is_authorized(self, user_id: int):
        # Только главный владелец имеет доступ в обход базы данных
        if user_id == 670031187: return True
        return await self.db.is_authorized(user_id)

    async def is_super_admin(self, user_id: int):
        # Только главный владелец всегда супер-админ
        if user_id == 670031187: return True
        role = await self.db.get_user_role(user_id)
        return role == 'super_admin'

    async def process_grant_by_input(self, message: types.Message, target_input: str, state: FSMContext):
        target_id = None
        
        # Если ввели @username, резолвим его в ID
        if target_input.startswith("@"):
            msg_wait = await message.reply(f"🔄 Поиск пользователя <code>{target_input}</code>...", parse_mode="HTML")
            
            # Способ 0: Проверка в нашей базе (если этот юзер уже заходил в бота)
            cached_id = await self.db.get_user_id_by_username(target_input)
            if cached_id:
                target_id = str(cached_id)
                await msg_wait.delete()
            else:
                # Способ 1: Через сам Bot API
                try:
                    chat = await self.bot.get_chat(target_input)
                    target_id = str(chat.id)
                    await msg_wait.delete()
                except Exception as bot_err:
                    # Способ 2: Через юзербота
                    clients_count = len(self.userbot_mgr.clients)
                    if clients_count > 0:
                        try:
                            client = self.userbot_mgr.clients[0]
                            if not client.is_connected(): await client.connect()
                            entity = await client.get_entity(target_input)
                            target_id = str(entity.id)
                            await msg_wait.delete()
                        except Exception as ub_err:
                            return await msg_wait.edit_text(f"❌ Не удалось найти <b>{target_input}</b>.\n\n"
                                                          f"Бот не «знает» этого пользователя. Попросите его написать <b>/start</b> боту или введите его цифровой ID.", parse_mode="HTML")
                    else:
                        return await msg_wait.edit_text(f"❌ Бот не смог найти <b>{target_input}</b>.\n\n"
                                                      f"Попросите пользователя написать <b>/start</b> нашему боту, чтобы его ID сохранился в базе, либо введите его цифровой ID.", parse_mode="HTML")
        else:
            target_id = target_input

        await state.update_data(grant_target_id=target_id)
        
        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(text="👤 Обычный Админ", callback_data="role_set_admin"),
            types.InlineKeyboardButton(text="👑 Супер Админ", callback_data="role_set_super")
        )
        await message.reply(f"📈 <b>Выдача прав</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
                            f"👤 <b>Пользователь:</b> <code>{target_id}</code> ({target_input})\n\n"
                            f"Выберите роль для пользователя:", 
                            reply_markup=builder.as_markup(), parse_mode="HTML")

    async def show_broadcast_options(self, message: types.Message, text: str):
        self.last_broadcast_text = text
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="📲 Только Системные", callback_data="br_mode_main"))
        builder.row(types.InlineKeyboardButton(text="📱 Только Мои Аккаунты", callback_data="br_mode_mine"))
        builder.row(types.InlineKeyboardButton(text="🔄 Все Вместе (Оба)", callback_data="br_mode_all"))
        
        await message.reply(f"📈 <b>Выбор аккаунтов для рассылки</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
                            f"📝 <b>Текст:</b>\n<i>{text}</i>\n\n"
                            f"С каких аккаунтов запустить рассылку?", 
                            reply_markup=builder.as_markup(), parse_mode="HTML")

    async def send_cargo_results(self, message: types.Message | types.CallbackQuery, page: int, cargo_from: str, cargo_to: str):
        limit = 10
        offset = page * limit
        u_id = message.from_user.id
        
        # Получаем папки пользователя. Если их нет - считаем, что доступ ко всем
        allowed_folders = await self.db.get_user_folders(u_id)
        if not allowed_folders: allowed_folders = None
        
        cargo_list = await self.db.get_paginated_cargo(
            limit=limit, 
            offset=offset, 
            cargo_from=cargo_from, 
            cargo_to=cargo_to, 
            allowed_folders=allowed_folders
        )
        total_count = await self.db.get_total_cargo_count(
            cargo_from=cargo_from, 
            cargo_to=cargo_to, 
            allowed_folders=allowed_folders
        )
        
        if not cargo_list:
            msg = "📭 <b>По вашему запросу ничего не найдено.</b>"
            if isinstance(message, types.CallbackQuery): await message.message.edit_text(msg, parse_mode="HTML")
            else: await message.answer(msg, parse_mode="HTML")
            return

        resp = f"🚛 <b>НАЙДЕНО ГРУЗОВ: {total_count}</b>\n"
        resp += f"🗺 <i>{cargo_from} ➔ {cargo_to}</i>\n"
        resp += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        emoji_nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        builder = InlineKeyboardBuilder()
        
        rows_data = [] # Для клавиатуры (цифры)

        for i, (route, s_id, c_link, txt, ts, m_id, r_id) in enumerate(cargo_list):
            num = emoji_nums[i] if i < 10 else f"{i+1}."
            
            # Очистка и сокращение текста
            clean_text = txt.replace("\n", " ").strip()
            if len(clean_text) > 80: clean_text = clean_text[:77] + "..."
            
            resp += f"{num} <b>{route}</b>\n"
            resp += f"└ 📄 <i>{html.escape(clean_text)}</i>\n\n"
            
            # Добавляем кнопку с номером
            rows_data.append(types.InlineKeyboardButton(text=str(i+1), callback_data=f"view_det_{r_id}"))
        
        # Группируем кнопки с номерами в ряды по 5
        for i in range(0, len(rows_data), 5):
            builder.row(*rows_data[i:i+5])

        # Кнопки навигации
        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"cargo_search_{page-1}"))
        if offset + limit < total_count:
            nav_row.append(types.InlineKeyboardButton(text="Вперед ➡️", callback_data=f"cargo_search_{page+1}"))
        if nav_row:
            builder.row(*nav_row)
        
        if isinstance(message, types.CallbackQuery):
            try: await message.message.edit_text(resp, reply_markup=builder.as_markup(), parse_mode="HTML")
            except: pass # Если текст не изменился (например при кликах на те же кнопки)
        else:
            await message.answer(resp, reply_markup=builder.as_markup(), parse_mode="HTML")

    async def send_cargo_page(self, message, page: int):
        # Эта функция теперь не используется для поиска, но оставим как заглушку или удалим позже
        pass

    async def send_alert(self, text: str, group_id: str = None):
        admins = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
        folder_id = await self.db.get_folder_by_group(group_id) if group_id else None
        db_users = await self.db.get_all_users()
        recipients = set(admins)
        for uid, _, _, _ in db_users:
            u_f = await self.db.get_user_folders(int(uid))
            if not folder_id or str(folder_id) in u_f or not u_f: recipients.add(int(uid))
        for u_id in recipients:
            try: await self.bot.send_message(u_id, f"📦 <b>НАЙДЕН ГРУЗ:</b>\n{html.escape(text)}", parse_mode="HTML")
            except: pass

    async def update_user_commands(self, user_id: int):
        """Обновляет меню доступных команд (/ в строке ввода и кнопка Меню) для конкретного пользователя"""
        from aiogram.types import BotCommand, BotCommandScopeChat
        is_sup = await self.is_super_admin(user_id)
        
        if is_sup:
            commands = [
                BotCommand(command="view_cargo", description="👀 Найти груз"),
                BotCommand(command="broadcast", description="📢 Запустить рассылку"),
                BotCommand(command="stop_broadcast", description="🛑 Остановить всё"),
                BotCommand(command="list_accounts", description="📱 Все аккаунты"),
                BotCommand(command="add_account", description="➕ Добавить аккаунт (Userbot)"),
                BotCommand(command="manage_folders", description="🏗 Управление папками"),
                BotCommand(command="refresh_folders", description="🔄 Обновить группы из папок"),
                BotCommand(command="join_folder", description="📂 Вступить в Addlist"),
                BotCommand(command="add_group", description="➕ Добавить одну группу"),
                BotCommand(command="grant_access", description="👤 Дать доступ (grant)"),
                BotCommand(command="list_users", description="👥 Список всех админов"),
                BotCommand(command="start", description="🏠 Главное меню")
            ]
        else:
            commands = [
                BotCommand(command="view_cargo", description="👀 Найти груз"),
                BotCommand(command="broadcast", description="📢 Запустить рассылку"),
                BotCommand(command="stop_broadcast", description="🛑 Остановить всё"),
                BotCommand(command="list_accounts", description="📱 Мои аккаунты"),
                BotCommand(command="add_account", description="➕ Добавить свой аккаунт"),
                BotCommand(command="manage_folders", description="🏗 Мои папки"),
                BotCommand(command="join_folder", description="📂 Вступить в Addlist (Папки)"),
                BotCommand(command="start", description="🏠 Главное меню")
            ]
        
        try:
            await self.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))
        except: pass

    async def start(self):
        # Глобальные команды (видимы всем, кто не в списке)
        from aiogram.types import BotCommand
        await self.bot.set_my_commands([
            BotCommand(command="start", description="🚀 Запустить")
        ])
        await self.dp.start_polling(self.bot)
