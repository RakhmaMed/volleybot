"""Обработчики команд бота."""

import asyncio
import logging
from collections.abc import Callable

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message, PollAnswer
from aiogram.filters import Command

from .poll import poll_data, update_players_list
from .utils import is_admin


def register_handlers(
    dp: Dispatcher,
    bot: Bot,
    get_bot_enabled: Callable[[], bool],
    set_bot_enabled: Callable[[bool], None]
) -> None:
    """
    Регистрирует все обработчики команд.
    
    Args:
        dp: Диспетчер бота
        bot: Экземпляр бота
        get_bot_enabled: Функция получения состояния бота
        set_bot_enabled: Функция установки состояния бота
    """
    
    # Создаём роутер для обработчиков
    router: Router = Router()
    
    @router.message(Command("start"))
    async def start_bot_handler(message: Message) -> None:
        """Команда для включения бота (только для администратора)."""
        user = message.from_user
        if user is None:
            logging.error("Получена команда /start без информации о пользователе")
            return
        
        if not is_admin(user):
            await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
            logging.warning(f"Попытка использования /start от неавторизованного пользователя: @{user.username} (ID: {user.id})")
            return
        
        if get_bot_enabled():
            await message.reply("✅ Бот уже включен и работает.")
            logging.info(f"Бот уже включен. Команда от администратора @{user.username}")
        else:
            set_bot_enabled(True)
            await message.reply("✅ Бот включен. Опросы будут создаваться по расписанию.")
            logging.info(f"Бот включен администратором @{user.username} (ID: {user.id})")

    @router.message(Command("stop"))
    async def stop_bot_handler(message: Message) -> None:
        """Команда для выключения бота (только для администратора)."""
        user = message.from_user
        if user is None:
            logging.error("Получена команда /stop без информации о пользователе")
            return
        
        if not is_admin(user):
            await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
            logging.warning(f"Попытка использования /stop от неавторизованного пользователя: @{user.username} (ID: {user.id})")
            return
        
        if not get_bot_enabled():
            await message.reply("⚠️ Бот уже выключен.")
            logging.info(f"Бот уже выключен. Команда от администратора @{user.username}")
        else:
            set_bot_enabled(False)
            await message.reply("⏸️ Бот выключен. Опросы не будут создаваться до включения.")
            logging.info(f"Бот выключен администратором @{user.username} (ID: {user.id})")

    @router.message(Command("chatid"))
    async def chatid_handler(message: Message) -> None:
        """Команда для получения ID чата."""
        chat = message.chat
        chat_info: str = f"📋 *Информация о чате:*\n\n"
        chat_info += f"ID чата: `{chat.id}`\n"
        chat_info += f"Тип: {chat.type}\n"
        
        if chat.title:
            chat_info += f"Название: {chat.title}\n"
        if chat.username:
            chat_info += f"Username: @{chat.username}\n"
        
        await message.reply(chat_info, parse_mode='Markdown')
        
        from_user = message.from_user
        if from_user is None:
            logging.error(f"Получена команда /chatid без информации о пользователе. Chat ID: {chat.id}")
        else:
            logging.info(f"Запрос ID чата от пользователя @{from_user.username} (ID: {from_user.id}). Chat ID: {chat.id}")

    @router.poll_answer()
    async def handle_poll_answer(poll_answer: PollAnswer) -> None:
        """Обработчик ответов на опросы."""
        poll_id: str = poll_answer.poll_id
        user = poll_answer.user
        selected: list[int] = poll_answer.option_ids
        
        if user is None:
            logging.error(f"Получен ответ на опрос {poll_id} без информации о пользователе")
            return
        
        logging.info(f"Получен новый ответ от пользователя {user.username} (ID: {user.id}), голос: {selected}")
        
        if poll_id not in poll_data:
            return

        data = poll_data[poll_id]
        yes_voters: list[dict[str, int | str]] = data['yes_voters']

        # Удаляем пользователя, если был
        yes_voters = [v for v in yes_voters if v['id'] != user.id]

        if 0 in selected:  # Да
            name: str = f"@{user.username}" if user.username else user.full_name
            yes_voters.append({'id': user.id, 'name': name})

        data['yes_voters'] = yes_voters
        logging.info(f"Обновленный список голосующих: {yes_voters}")
        
        # Отменяем предыдущую задачу обновления
        if 'update_task' in data and data['update_task'] is not None:
            data['update_task'].cancel()
            logging.debug("Предыдущая задача обновления отменена")
        
        # Создаём новую задачу обновления с задержкой
        data['update_task'] = asyncio.create_task(update_players_list(bot, poll_id))
        logging.debug("Создана новая задача отложенного обновления (10 сек)")

    # Регистрируем роутер в диспетчере
    dp.include_router(router)
