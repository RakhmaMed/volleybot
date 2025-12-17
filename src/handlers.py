"""Обработчики команд бота."""

import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, PollAnswer, Update

from .services import BotStateService, PollService
from .utils import get_player_name, is_admin


def register_handlers(dp: Dispatcher, bot: Bot) -> None:
    """
    Регистрирует все обработчики команд.

    Args:
        dp: Диспетчер бота
        bot: Экземпляр бота
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
            logging.warning(
                f"Попытка использования /start от неавторизованного пользователя: @{user.username} (ID: {user.id})"
            )
            return

        # Получаем сервис из workflow_data
        bot_state_service: BotStateService = dp.workflow_data["bot_state_service"]

        if bot_state_service.is_enabled():
            await message.reply("✅ Бот уже включен и работает.")
            logging.info(f"Бот уже включен. Команда от администратора @{user.username}")
        else:
            bot_state_service.set_enabled(True)
            await message.reply(
                "✅ Бот включен. Опросы будут создаваться по расписанию."
            )
            logging.info(
                f"Бот включен администратором @{user.username} (ID: {user.id})"
            )

    @router.message(Command("stop"))
    async def stop_bot_handler(message: Message) -> None:
        """Команда для выключения бота (только для администратора)."""
        user = message.from_user
        if user is None:
            logging.error("Получена команда /stop без информации о пользователе")
            return

        if not is_admin(user):
            await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
            logging.warning(
                f"Попытка использования /stop от неавторизованного пользователя: @{user.username} (ID: {user.id})"
            )
            return

        # Получаем сервис из workflow_data
        bot_state_service: BotStateService = dp.workflow_data["bot_state_service"]

        if not bot_state_service.is_enabled():
            await message.reply("⚠️ Бот уже выключен.")
            logging.info(
                f"Бот уже выключен. Команда от администратора @{user.username}"
            )
        else:
            bot_state_service.set_enabled(False)
            await message.reply(
                "⏸️ Бот выключен. Опросы не будут создаваться до включения."
            )
            logging.info(
                f"Бот выключен администратором @{user.username} (ID: {user.id})"
            )

    @router.message(Command("chatid"))
    async def chatid_handler(message: Message) -> None:
        """Команда для получения ID чата."""
        chat = message.chat
        chat_info: str = "📋 *Информация о чате:*\n\n"
        chat_info += f"ID чата: `{chat.id}`\n"
        chat_info += f"Тип: {chat.type}\n"

        if chat.title:
            chat_info += f"Название: {chat.title}\n"
        if chat.username:
            chat_info += f"Username: @{chat.username}\n"

        await message.reply(chat_info, parse_mode="Markdown")

        from_user = message.from_user
        if from_user is None:
            logging.error(
                f"Получена команда /chatid без информации о пользователе. Chat ID: {chat.id}"
            )
        else:
            logging.info(
                f"Запрос ID чата от пользователя @{from_user.username} (ID: {from_user.id}). Chat ID: {chat.id}"
            )

    @router.poll_answer()
    async def handle_poll_answer(
        poll_answer: PollAnswer, event_update: Update | None = None
    ) -> None:
        """Обработчик ответов на опросы."""
        poll_id: str = poll_answer.poll_id
        user = poll_answer.user
        selected: list[int] = poll_answer.option_ids
        update_id: int = event_update.update_id if event_update else 0

        if user is None:
            logging.error(
                f"Получен ответ на опрос {poll_id} без информации о пользователе"
            )
            return

        logging.info(
            f"Получен новый ответ от пользователя {user.username} "
            f"(ID: {user.id}), голос: {selected}, update_id: {update_id}"
        )

        # Получаем сервис из workflow_data
        poll_service: PollService = dp.workflow_data["poll_service"]

        if not poll_service.has_poll(poll_id):
            return

        data = poll_service.get_poll_data(poll_id)
        if data is None:
            return

        voted_yes = 0 in selected  # Да
        subs: list[int] = data.subs
        name: str = get_player_name(user, subs)

        # Обновляем список голосующих
        sorted_yes_voters = poll_service.update_voters(
            poll_id=poll_id,
            user_id=user.id,
            user_name=name,
            update_id=update_id,
            voted_yes=voted_yes,
        )
        logging.info(f"Обновленный список голосующих: {sorted_yes_voters}")

        # Отменяем предыдущую задачу обновления
        poll_service.cancel_update_task(poll_id)

        # Создаём новую задачу обновления с задержкой
        poll_service.create_update_task(poll_id, bot)

        # Сохраняем текущее состояние опросов для восстановления после перезапуска
        poll_service.persist_state()

    @router.message()
    async def log_any_message(message: Message) -> None:
        """Логирует все входящие сообщения и их message_id."""
        user = message.from_user
        username = f"@{user.username}" if user and user.username else "unknown"
        user_id = user.id if user else "unknown"
        logging.info(
            "Получено сообщение id=%s chat_id=%s от %s (ID: %s), тип=%s, текст=%r",
            message.message_id,
            message.chat.id,
            username,
            user_id,
            message.content_type,
            message.text,
        )

    # Регистрируем роутер в диспетчере
    dp.include_router(router)
