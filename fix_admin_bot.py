import sys
with open("utils/admin_bot.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if "await message.reply(\"✅ **Успешный вход!** Аккаунт активен.\", parse_mode=\"Markdown\")" in line:
        start_idx = i
        break

for i in range(start_idx, len(lines)):
    if "await callback_query.answer()" in line and "await state.clear()" in lines[i-1]:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    new_code = """                # Если вошли успешно
                builder = InlineKeyboardBuilder()
                for i in range(1, 5):
                    builder.row(types.InlineKeyboardButton(text=f"📁 Подключить Папку {i}", callback_data=f"sel_fld_{i}"))
                builder.row(types.InlineKeyboardButton(text="❌ Не добавлять папку", callback_data="skip_fld"))
                
                await message.reply("✅ Успешный вход! Аккаунт активен.\\n\\n**К какой папке привязать этот аккаунт?**", 
                                   reply_markup=builder.as_markup(), parse_mode="Markdown")
                if client not in self.userbot_mgr.clients:
                    self.userbot_mgr.clients.append(client)
                await state.update_data(target_client_for_join=client)
                await state.set_state(AuthStates.waiting_folder_selection)
            except SessionPasswordNeededError:
                await message.reply("🔐 На аккаунте стоит пароль (2FA). Введите его:")
                await state.set_state(AuthStates.waiting_password)
            except PhoneCodeInvalidError:
                await message.reply("❌ Неверный код. Попробуй еще раз:")
            except Exception as e:
                await message.reply(f"❌ Ошибка: {e}")
                await state.clear()

        @self.dp.message(AuthStates.waiting_password, ~F.text.startswith('/'))
        async def auth_password(message: types.Message, state: FSMContext):
            password = message.text.strip()
            data = self.temp_clients.get(message.from_user.id)
            if not data: return
            
            client = data['client']
            try:
                await client.sign_in(password=password)
                builder = InlineKeyboardBuilder()
                for i in range(1, 5):
                    builder.row(types.InlineKeyboardButton(text=f"📁 Подключить Папку {i}", callback_data=f"sel_fld_{i}"))
                builder.row(types.InlineKeyboardButton(text="❌ Не добавлять папку", callback_data="skip_fld"))
                
                await message.reply("✅ Успешный вход (2FA)! Аккаунт активен.\\n\\n**К какой папке привязать этот аккаунт?**", 
                                   reply_markup=builder.as_markup(), parse_mode="Markdown")
                if client not in self.userbot_mgr.clients:
                    self.userbot_mgr.clients.append(client)
                await state.update_data(target_client_for_join=client)
                await state.set_state(AuthStates.waiting_folder_selection)
            except PasswordHashInvalidError:
                await message.reply("❌ Неверный пароль. Попробуй еще раз:")
            except Exception as e:
                await message.reply(f"❌ Ошибка: {e}")
                await state.clear()

        @self.dp.callback_query(F.data == "skip_fld")
        async def process_skip_folder(callback_query: types.CallbackQuery, state: FSMContext):
            await callback_query.message.edit_text("✅ Аккаунт добавлен без привязки к папке.", parse_mode="Markdown")
            await state.clear()
            await callback_query.answer()

        @self.dp.callback_query(F.data.startswith('sel_fld_'))
        async def process_folder_selection(callback_query: types.CallbackQuery, state: FSMContext):
            folder_id = callback_query.data.split('_')[2]
            
            # Твои 4 ссылки
            links_map = {
                '1': '8udTD2dLVkg0MjYy',
                '2': 'cI22UnY_7a84ODI6',
                '3': '_i7riJupfo8wNmUy',
                '4': 'ILdoxmzs1WE2NWIy'
            }
            slug = links_map.get(folder_id)
            if not slug: return await callback_query.answer("⚠️ Ошибка: Ссылка не найдена.")
            
            data = await state.get_data()
            client = data.get('target_client_for_join')
            if not client: 
                # Если в стейте нет, пробуем взять последний добавленный
                if self.userbot_mgr.clients:
                    client = self.userbot_mgr.clients[-1]
                else:
                    return await callback_query.answer("⚠️ Ошибка: Сессия утеряна. Попробуй заново.")

            await callback_query.message.edit_text(f"⏳ Начинаю вступление в **Папку {folder_id}**... Подождите.", parse_mode="Markdown")
            
            try:
                # 1. Проверяем папку
                check = await client(functions.chatlists.CheckChatlistInviteRequest(slug=slug))
                group_ids = [str(chat.id) for chat in check.chats]
                
                # 2. Подготовка пиров
                peers = []
                for chat in check.chats:
                    if isinstance(chat, (telethon_types.Chat, telethon_types.ChatEmpty)):
                        peers.append(telethon_types.InputPeerChat(chat.id))
                    else:
                        peers.append(telethon_types.InputPeerChannel(chat.id, chat.access_hash))
                
                # 3. Вступление
                await client(functions.chatlists.JoinChatlistInviteRequest(slug=slug, peers=peers))
                
                # 4. Сохранение в базу
                for chat in check.chats:
                    title = getattr(chat, "title", "Группа")
                    username = getattr(chat, "username", f"id{chat.id}")
                    await self.db.add_group(str(chat.id))
                    await self.db.batch_update_folder([str(chat.id)], folder_id) # Метод требует список

                await callback_query.message.edit_text(f"✅ **Успех!**\\n\\n⚓ Аккаунт привязан к **Папке {folder_id}**.\\n🔹 Вступил в **{len(check.chats)}** чатов.", parse_mode="Markdown")
            except Exception as e:
                await callback_query.message.edit_text(f"❌ Ошибка вступления: {e}")
            
            await state.clear()
            await callback_query.answer()\n"""
    lines[start_idx:end_idx+1] = [new_code]
    with open("utils/admin_bot.py", "w") as f:
        f.writelines(lines)
    print("Code successfully updated!")
else:
    print("Could not find markers.", start_idx, end_idx)
