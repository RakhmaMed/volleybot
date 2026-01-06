"""Обработчики команд бота."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    Message,
    PollAnswer,
    Update,
)

from .config import POLLS_SCHEDULE
from .services import AdminService, BotStateService, PollService
from .utils import get_player_name, rate_limit_check


async def setup_bot_commands(bot: Bot) -> None:
    """
    Устанавливает список команд бота для отображения в меню Telegram.

    Args:
        bot: Экземпляр бота
    """
    # Команды для всех пользователей в группах
    user_commands = [
        BotCommand(command="help", description="Показать справку по командам"),
        BotCommand(command="schedule", description="Показать расписание опросов"),
    ]

    # Команды для администраторов (включая пользовательские)
    admin_commands = [
        BotCommand(command="help", description="Показать справку по командам"),
        BotCommand(command="schedule", description="Показать расписание опросов"),
        BotCommand(command="start", description="Включить бота"),
        BotCommand(command="stop", description="Выключить бота"),
    ]

    # Устанавливаем команды для обычных пользователей в группах
    await bot.set_my_commands(
        commands=user_commands, scope=BotCommandScopeAllGroupChats()
    )

    # Устанавливаем команды для администраторов всех групп
    await bot.set_my_commands(
        commands=admin_commands, scope=BotCommandScopeAllChatAdministrators()
    )

    logging.info("✅ Команды бота зарегистрированы в меню Telegram")


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
            logging.error("❌ Получена команда /start без информации о пользователе")
            return

        # Получаем сервисы из workflow_data
        admin_service: AdminService = dp.workflow_data["admin_service"]
        bot_state_service: BotStateService = dp.workflow_data["bot_state_service"]

        # Проверяем, является ли пользователь администратором группы
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)

        # Проверка rate limit (после проверки админа)
        rate_limit_error = rate_limit_check(user, is_admin)
        if rate_limit_error:
            await message.reply(rate_limit_error)
            return

        if not is_admin:
            await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
            logging.warning(
                f"⚠️ Попытка использования /start от неавторизованного пользователя: "
                f"@{user.username} (ID: {user.id})"
            )
            return

        if bot_state_service.is_enabled():
            await message.reply("✅ Бот уже включен и работает.")
            logging.info(
                f"ℹ️ Бот уже включен. Команда /start от администратора @{user.username} (ID: {user.id})"
            )
        else:
            bot_state_service.set_enabled(True)
            await message.reply(
                "✅ Бот включен. Опросы будут создаваться по расписанию."
            )
            logging.info(
                f"✅ Бот ВКЛЮЧЕН администратором @{user.username} (ID: {user.id})"
            )

    @router.message(Command("stop"))
    async def stop_bot_handler(message: Message) -> None:
        """Команда для выключения бота (только для администратора)."""
        user = message.from_user
        if user is None:
            logging.error("❌ Получена команда /stop без информации о пользователе")
            return

        # Получаем сервисы из workflow_data
        admin_service: AdminService = dp.workflow_data["admin_service"]
        bot_state_service: BotStateService = dp.workflow_data["bot_state_service"]

        # Проверяем, является ли пользователь администратором группы
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)

        # Проверка rate limit (после проверки админа)
        rate_limit_error = rate_limit_check(user, is_admin)
        if rate_limit_error:
            await message.reply(rate_limit_error)
            return

        if not is_admin:
            await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
            logging.warning(
                f"⚠️ Попытка использования /stop от неавторизованного пользователя: "
                f"@{user.username} (ID: {user.id})"
            )
            return

        if not bot_state_service.is_enabled():
            await message.reply("⚠️ Бот уже выключен.")
            logging.info(
                f"ℹ️ Бот уже выключен. Команда /stop от администратора @{user.username} (ID: {user.id})"
            )
        else:
            bot_state_service.set_enabled(False)
            await message.reply(
                "⏸️ Бот выключен. Опросы не будут создаваться до включения."
            )
            logging.info(
                f"⏸️ Бот ВЫКЛЮЧЕН администратором @{user.username} (ID: {user.id})"
            )

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        """Команда для отображения справки по командам бота."""
        user = message.from_user

        # Проверка rate limit
        rate_limit_error = rate_limit_check(user, is_admin=False)
        if rate_limit_error:
            await message.reply(rate_limit_error)
            return

        help_text = (
            "🏐 <b>Volleybot — Справка</b>\n\n"
            "<b>Доступные команды:</b>\n"
            "/help — показать эту справку\n"
            "/schedule — показать расписание опросов\n\n"
            "<b>Команды для администраторов:</b>\n"
            "/start — включить бота\n"
            "/stop — выключить бота\n\n"
            "<b>Как пользоваться:</b>\n"
            "Бот автоматически создаёт опросы по расписанию. "
            "Голосуйте «Да», если планируете участвовать в игре."
        )

        await message.reply(help_text)

        if user:
            logging.info(
                f"📖 Запрос справки от пользователя @{user.username} (ID: {user.id})"
            )

    @router.message(Command("schedule"))
    async def schedule_handler(message: Message) -> None:
        """Команда для отображения расписания игр."""
        user = message.from_user

        # Проверка rate limit
        rate_limit_error = rate_limit_check(user, is_admin=False)
        if rate_limit_error:
            await message.reply(rate_limit_error)
            return

        if not POLLS_SCHEDULE:
            await message.reply("🏐 Расписание игр пока не настроено.")
            return

        # Маппинг дней недели на русский (полные названия)
        days_ru = {
            "mon": "Понедельник",
            "tue": "Вторник",
            "wed": "Среда",
            "thu": "Четверг",
            "fri": "Пятница",
            "sat": "Суббота",
            "sun": "Воскресенье",
            "*": "Ежедневно",
        }

        # Порядок дней для вычисления следующего дня
        days_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

        def utc_to_msk(day: str, hour: int, minute: int) -> tuple[str, int, int]:
            """Конвертация времени из UTC в МСК (UTC+3)."""
            msk_hour = hour + 3
            msk_day = day

            if msk_hour >= 24:
                msk_hour -= 24
                # Переход на следующий день
                if day != "*":
                    day_idx = days_order.index(day)
                    msk_day = days_order[(day_idx + 1) % 7]

            return msk_day, msk_hour, minute

        schedule_text = "🏐 <b>Расписание игр</b> <i>(время МСК)</i>\n\n"

        for poll in POLLS_SCHEDULE:
            # Конвертируем время игры в МСК
            game_day_msk, game_hour_msk, game_minute_msk = utc_to_msk(
                poll.game_day, poll.game_hour_utc, poll.game_minute_utc
            )
            # Конвертируем время опроса в МСК
            open_day_msk, open_hour_msk, open_minute_msk = utc_to_msk(
                poll.open_day, poll.open_hour_utc, poll.open_minute_utc
            )

            game_day_text = days_ru.get(game_day_msk, game_day_msk)
            open_day_text = days_ru.get(open_day_msk, open_day_msk)

            schedule_text += f"<b>🏐 {poll.name}</b>\n"
            schedule_text += f"    ⏰ <b>{game_day_text}, {game_hour_msk:02d}:{game_minute_msk:02d}</b>\n"
            schedule_text += f"    📬 Опрос: {open_day_text}, {open_hour_msk:02d}:{open_minute_msk:02d}\n\n"

        schedule_text += "<i>Опрос закрывается за 30 минут до начала игры.</i>"

        await message.reply(schedule_text)

        if user:
            logging.info(
                f"📅 Запрос расписания от пользователя @{user.username} (ID: {user.id})"
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
                f"❌ Получен ответ на опрос {poll_id} без информации о пользователе"
            )
            return

        logging.info(
            f"🗳️ Получен ответ от пользователя @{user.username or 'unknown'} "
            f"(ID: {user.id}) на опрос {poll_id}: вариант {selected}, update_id: {update_id}"
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
        logging.debug(
            f"Обновленный список голосующих за опрос {poll_id}: {len(sorted_yes_voters)} чел."
        )

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
        logging.debug(
            "📨 Сообщение: id=%s, chat_id=%s, от=%s (ID: %s), тип=%s, текст=%r",
            message.message_id,
            message.chat.id,
            username,
            user_id,
            message.content_type,
            message.text or "",
        )

    # Регистрируем роутер в диспетчере
    dp.include_router(router)
