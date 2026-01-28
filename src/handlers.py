"""Обработчики команд бота."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PollAnswer,
    Update,
)

from .config import POLLS_SCHEDULE
from .db import (
    ensure_player,
    find_player_by_name,
    get_player_balance,
    get_players_with_balance,
    update_player_balance,
)
from .services import AdminService, BotStateService, PollService
from .utils import get_player_name, rate_limit_check, retry_async


@retry_async(
    (TelegramNetworkError, asyncio.TimeoutError, OSError),
    tries=None,
    delay=2,
    backoff=2.0,
    max_delay=60.0,
)
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
        BotCommand(command="balance", description="Показать мой баланс"),
    ]

    # Команды для администраторов (включая пользовательские)
    admin_commands = [
        BotCommand(command="help", description="Показать справку по командам"),
        BotCommand(command="schedule", description="Показать расписание опросов"),
        BotCommand(command="balance", description="Показать долги/балансы"),
        BotCommand(command="pay", description="Изменить баланс игрока"),
        BotCommand(command="start", description="Включить бота"),
        BotCommand(command="stop", description="Выключить бота"),
    ]

    # Устанавливаем команды для приватных чатов (по умолчанию, без scope)
    await bot.set_my_commands(commands=user_commands)

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
            try:
                await message.reply(rate_limit_error)
            except TelegramNetworkError:
                logging.warning("⚠️ Сетевая ошибка при отправке rate limit сообщения")
            return

        if not is_admin:
            try:
                await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
            except TelegramNetworkError:
                logging.warning("⚠️ Сетевая ошибка при отправке сообщения")
            logging.warning(
                f"⚠️ Попытка использования /start от неавторизованного пользователя: "
                f"@{user.username} (ID: {user.id})"
            )
            return

        try:
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
        except TelegramNetworkError:
            logging.warning(
                f"⚠️ Сетевая ошибка при ответе на /start от @{user.username} (ID: {user.id})"
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
            try:
                await message.reply(rate_limit_error)
            except TelegramNetworkError:
                logging.warning("⚠️ Сетевая ошибка при отправке rate limit сообщения")
            return

        if not is_admin:
            try:
                await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
            except TelegramNetworkError:
                logging.warning("⚠️ Сетевая ошибка при отправке сообщения")
            logging.warning(
                f"⚠️ Попытка использования /stop от неавторизованного пользователя: "
                f"@{user.username} (ID: {user.id})"
            )
            return

        try:
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
        except TelegramNetworkError:
            logging.warning(
                f"⚠️ Сетевая ошибка при ответе на /stop от @{user.username} (ID: {user.id})"
            )

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        """Команда для отображения справки по командам бота."""
        user = message.from_user
        if user is None:
            return

        # Получаем сервисы из workflow_data
        admin_service: AdminService = dp.workflow_data["admin_service"]

        # Проверяем, является ли пользователь администратором
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)

        # Проверка rate limit
        rate_limit_error = rate_limit_check(user, is_admin)
        if rate_limit_error:
            try:
                await message.reply(rate_limit_error)
            except TelegramNetworkError:
                logging.warning("⚠️ Сетевая ошибка при отправке rate limit сообщения")
            return

        help_text = (
            "🏐 <b>Volleybot — Справка</b>\n\n"
            "<b>Доступные команды:</b>\n"
            "/help — показать эту справку\n"
            "/schedule — показать расписание опросов\n"
            "/balance — показать мой баланс\n\n"
            "<b>Команды для администраторов:</b>\n"
            "/balance — список всех долгов\n"
            "/pay [сумма] — изменить баланс (в ответ на сообщение)\n"
            "/pay [имя] [сумма] — найти игрока и изменить баланс\n"
            "/start — включить бота\n"
            "/stop — выключить бота\n\n"
            "<b>Как пользоваться:</b>\n"
            "Бот автоматически создаёт опросы по расписанию. "
            "Голосуйте «Да», если планируете участвовать в игре."
        )

        try:
            await message.reply(help_text)
            if user:
                logging.info(
                    f"📖 Запрос справки от пользователя @{user.username} (ID: {user.id})"
                )
        except TelegramNetworkError:
            logging.warning(
                f"⚠️ Сетевая ошибка при ответе на /help от @{user.username if user else 'unknown'}"
            )

    @router.message(Command("schedule"))
    async def schedule_handler(message: Message) -> None:
        """Команда для отображения расписания опросов."""
        user = message.from_user

        # Проверка rate limit
        rate_limit_error = rate_limit_check(user, is_admin=False)
        if rate_limit_error:
            try:
                await message.reply(rate_limit_error)
            except TelegramNetworkError:
                logging.warning("⚠️ Сетевая ошибка при отправке rate limit сообщения")
            return

        if not POLLS_SCHEDULE:
            try:
                await message.reply("📅 Расписание опросов пока не настроено.")
            except TelegramNetworkError:
                logging.warning("⚠️ Сетевая ошибка при отправке сообщения о расписании")
            return

        # Маппинг дней недели на русский
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

        schedule_text = "📅 <b>Расписание игр</b> (время МСК)\n\n"

        for poll in POLLS_SCHEDULE:
            game_day = days_ru.get(poll.game_day, poll.game_day)

            # Конвертация в МСК (UTC+3)
            msk_hour = (poll.game_hour_utc + 3) % 24
            msk_minute = poll.game_minute_utc

            place_info = f" ({poll.place})" if poll.place else ""

            schedule_text += f"{game_day} {msk_hour:02d}:{msk_minute:02d}{place_info}\n"

        schedule_text += (
            "\n<i>ℹ️ Опрос начинается за день до игры в 19:00 "
            "и заканчивается за полчаса до начала игры.</i>"
        )

        try:
            await message.reply(schedule_text)
            if user:
                logging.info(
                    f"📅 Запрос расписания от пользователя @{user.username} (ID: {user.id})"
                )
        except TelegramNetworkError:
            logging.warning(
                f"⚠️ Сетевая ошибка при ответе на /schedule от @{user.username if user else 'unknown'}"
            )

    @router.message(Command("balance"))
    async def balance_handler(message: Message) -> None:
        """Команда для отображения баланса/долгов."""
        user = message.from_user
        if user is None:
            return

        # Получаем сервисы из workflow_data
        admin_service: AdminService = dp.workflow_data["admin_service"]

        # Проверяем, является ли пользователь администратором
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)

        # Проверка rate limit
        rate_limit_error = rate_limit_check(user, is_admin)
        if rate_limit_error:
            try:
                await message.reply(rate_limit_error)
            except TelegramNetworkError:
                pass
            return

        if is_admin:
            # Администратор видит всех с ненулевым балансом
            players = get_players_with_balance()
            if not players:
                text = "💰 Все балансы на нуле. Долгов нет!"
            else:
                text = "💰 <b>Список балансов:</b>\n\n"
                for p in players:
                    balance = p["balance"]
                    name = p["fullname"] or p["name"] or f"ID: {p['id']}"
                    icon = "🔴" if balance < 0 else "🟢"
                    text += f"{icon} {name}: <b>{balance} ₽</b>\n"
        else:
            # Обычный пользователь видит только свой баланс
            player = get_player_balance(user.id)
            if player:
                balance = player["balance"]
                if balance == 0:
                    text = "💰 Ваш баланс: <b>0 ₽</b>. Всё в порядке!"
                elif balance < 0:
                    text = f"💰 Ваш баланс: <b>{balance} ₽</b>. Пожалуйста, пополните."
                else:
                    text = f"💰 Ваш баланс: <b>{balance} ₽</b>. Спасибо за предоплату!"
            else:
                text = "💰 Информация о вашем балансе не найдена. Обратитесь к администратору."

        try:
            await message.reply(text)
            logging.info(
                f"💰 Запрос баланса от {'админа' if is_admin else 'пользователя'} @{user.username} (ID: {user.id})"
            )
        except TelegramNetworkError:
            logging.warning(
                f"⚠️ Сетевая ошибка при ответе на /balance от @{user.username if user else 'unknown'}"
            )

    @router.message(Command("pay"))
    async def pay_handler(message: Message) -> None:
        """Команда для изменения баланса игрока (только для администратора)."""
        user = message.from_user
        if user is None:
            return

        # Получаем сервисы из workflow_data
        admin_service: AdminService = dp.workflow_data["admin_service"]

        # Проверяем, является ли пользователь администратором
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)

        if not is_admin:
            # Обычным игрокам команда недоступна и не показывается
            return

        args = message.text.split()
        target_user_id = None
        amount = 0
        target_name = ""

        # 1. Если это ответ на сообщение
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            target_user_id = target_user.id
            # Гарантируем наличие игрока в базе при ответе на сообщение
            ensure_player(
                user_id=target_user_id,
                name=target_user.username,
                fullname=target_user.full_name,
            )
            target_name = (
                target_user.full_name or target_user.username or f"ID: {target_user_id}"
            )
            if len(args) > 1:
                try:
                    amount = int(args[1])
                except ValueError:
                    await message.reply(
                        "❌ Ошибка: сумма должна быть числом.\nПример: <code>/pay 500</code>",
                        parse_mode="HTML",
                    )
                    return
            else:
                await message.reply(
                    "❌ Укажите сумму.\nПример: <code>/pay 500</code> (в ответ на сообщение)",
                    parse_mode="HTML",
                )
                return
        # 2. Если указаны аргументы (Имя/ID/@username Сумма)
        elif len(args) >= 3:
            try:
                amount = int(args[-1])
                search_query = " ".join(args[1:-1])

                # Проверяем, не является ли запрос ID игрока
                if search_query.isdigit():
                    target_user_id = int(search_query)
                    player = get_player_balance(target_user_id)
                    if player:
                        target_name = (
                            player["fullname"]
                            or player["name"]
                            or f"ID: {target_user_id}"
                        )
                    else:
                        await message.reply(
                            f"❌ Игрок с ID {target_user_id} не найден."
                        )
                        return
                else:
                    # Поиск по имени или @username (убираем @ если есть)
                    clean_query = search_query.lstrip("@")
                    players = find_player_by_name(clean_query)
                    if not players:
                        await message.reply(f"❌ Игрок '{search_query}' не найден.")
                        return
                    if len(players) > 1:
                        keyboard = []
                        for p in players[:10]:  # Ограничим 10 игроками
                            p_name = (
                                f"{p['fullname'] or p['name']} (ID: {p['id']})"
                                if (p["fullname"] or p["name"])
                                else f"ID: {p['id']}"
                            )
                            callback_data = f"pay_select:{p['id']}:{amount}"
                            keyboard.append(
                                [
                                    InlineKeyboardButton(
                                        text=p_name, callback_data=callback_data
                                    )
                                ]
                            )

                        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
                        await message.reply(
                            f"❓ Найдено несколько игроков ({len(players)}). Выберите нужного:",
                            reply_markup=reply_markup,
                        )
                        return

                    target_user_id = players[0]["id"]
                    target_name = (
                        players[0]["fullname"]
                        or players[0]["name"]
                        or f"ID: {target_user_id}"
                    )
            except ValueError:
                await message.reply(
                    "❌ Ошибка: сумма должна быть числом в конце команды.\nПример: <code>/pay Иван 500</code>",
                    parse_mode="HTML",
                )
                return
        else:
            await message.reply(
                "ℹ️ <b>Управление балансом:</b>\n\n"
                "1. Ответьте на сообщение игрока: <code>/pay 500</code>\n"
                "2. Поиск по имени: <code>/pay Иван 500</code>\n"
                "3. По @username: <code>/pay @username 500</code>\n"
                "4. По ID игрока: <code>/pay 12345678 500</code>\n\n"
                "<i>Сумма может быть отрицательной для списания.</i>",
                parse_mode="HTML",
            )
            return

        if target_user_id and amount != 0:
            if update_player_balance(target_user_id, amount):
                new_balance_data = get_player_balance(target_user_id)
                new_balance = (
                    new_balance_data["balance"] if new_balance_data else "неизвестно"
                )
                try:
                    await message.reply(
                        f"✅ Баланс игрока <b>{target_name}</b> изменен на {amount} ₽.\n"
                        f"💰 Текущий баланс: <b>{new_balance} ₽</b>",
                        parse_mode="HTML",
                    )
                    logging.info(
                        f"💰 Админ @{user.username} (ID: {user.id}) изменил баланс {target_name} (ID: {target_user_id}) на {amount}"
                    )
                except TelegramNetworkError:
                    pass
            else:
                await message.reply(
                    "❌ Не удалось обновить баланс. Убедитесь, что игрок взаимодействовал с ботом ранее."
                )

    @router.callback_query(lambda c: c.data and c.data.startswith("pay_select:"))
    async def process_pay_select(callback_query: CallbackQuery, bot: Bot):
        """Обработка выбора игрока из списка для изменения баланса."""
        user = callback_query.from_user
        if user is None:
            logging.error("❌ Callback query without user")
            return

        logging.info(f"Обработка pay_select от {user.id}: {callback_query.data}")

        # Получаем сервисы из workflow_data
        admin_service: AdminService = dp.workflow_data["admin_service"]

        # Проверяем, является ли пользователь администратором
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )

        if not is_admin:
            logging.warning(
                f"🚫 Попытка несанкционированного изменения баланса через callback: "
                f"@{user.username} (ID: {user.id})"
            )
            await callback_query.answer(
                "❌ У вас нет прав для этого действия.", show_alert=True
            )
            return

        # Парсим callback_data: pay_select:player_id:amount
        data_parts = callback_query.data.split(":")
        if len(data_parts) != 3:
            await callback_query.answer("❌ Ошибка данных.")
            return

        try:
            target_user_id = int(data_parts[1])
            amount = int(data_parts[2])
        except ValueError:
            await callback_query.answer("❌ Ошибка формата данных.")
            return

        if update_player_balance(target_user_id, amount):
            new_balance_data = get_player_balance(target_user_id)
            new_balance = (
                new_balance_data["balance"] if new_balance_data else "неизвестно"
            )

            p_name = "игрока"
            if new_balance_data:
                p_name = f"<b>{new_balance_data['fullname'] or new_balance_data['name'] or f'ID: {target_user_id}'}</b>"

            try:
                await callback_query.message.edit_text(
                    f"✅ Баланс {p_name} изменен на {amount} ₽.\n"
                    f"💰 Текущий баланс: <b>{new_balance} ₽</b>",
                    parse_mode="HTML",
                )
                await callback_query.answer("✅ Баланс обновлен")
            except Exception as e:
                logging.error(f"❌ Ошибка при редактировании сообщения: {e}")
                await callback_query.answer("✅ Баланс обновлен")

            logging.info(
                f"💰 Админ @{user.username} (ID: {user.id}) изменил баланс через меню: "
                f"ID={target_user_id}, сумма={amount}"
            )
        else:
            await callback_query.answer(
                "❌ Не удалось обновить баланс.", show_alert=True
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

        # Гарантируем наличие игрока в базе при голосовании
        ensure_player(user_id=user.id, name=user.username, fullname=user.full_name)

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
