"""Обработчики команд бота."""

# VERSION MARKER: 2026-01-28-v2 - Добавлено детальное логирование callback_query

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
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    PollAnswer,
    Update,
)

from .config import POLLS_SCHEDULE
from .db import (
    ensure_player,
    find_player_by_name,
    get_all_players,
    get_player_balance,
    get_player_info,
    get_players_with_balance,
    get_poll_templates,
    update_player_balance,
)
from .services import AdminService, BotStateService, PollService
from .utils import (
    escape_html,
    format_player_link,
    get_player_name,
    rate_limit_check,
    retry_async,
)

# Логируем загрузку модуля для отладки
logging.info("🔄 Загружен модуль handlers.py - VERSION 2026-01-29-v2")


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
        BotCommand(command="subs", description="Абонементы по дням"),
        BotCommand(command="pay", description="Изменить баланс игрока"),
        BotCommand(command="player", description="Подробная информация об игроках"),
        BotCommand(command="start", description="Включить бота"),
        BotCommand(command="stop", description="Выключить бота"),
        BotCommand(command="webhookinfo", description="Статус webhook"),
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


def _format_player_detail(p: dict) -> str:
    """Форматирует подробную информацию об одном игроке (HTML)."""
    link = format_player_link(p)
    lines = [
        f"👤 {link}",
        f"🆔 ID: {p['id']}",
    ]
    if p.get("name") and str(p["name"]).strip():
        lines.append(f"📛 @{escape_html(str(p['name']).strip())}")
    lines.append(f"💰 Баланс: {p.get('balance', 0)} ₽")
    ball = "да" if p.get("ball_donate") else "нет"
    lines.append(f"🏐 Мяч на донат: {ball}")
    return "\n".join(lines)


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
            "/subs — абонементы по дням\n"
            "/pay [сумма] — изменить баланс (в ответ на сообщение)\n"
            "/pay [имя] [сумма] — найти игрока и изменить баланс\n"
            "/player — список всех игроков с подробной информацией\n"
            "/player [имя] — информация об одном игроке (по имени, @username или ID)\n"
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
                    player_link = format_player_link(p)
                    icon = "🔴" if balance < 0 else "🟢"
                    text += f"{icon} {player_link}: <b>{balance} ₽</b>\n"
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
            await message.reply(
                text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            logging.info(
                f"💰 Запрос баланса от {'админа' if is_admin else 'пользователя'} @{user.username} (ID: {user.id})"
            )
        except TelegramNetworkError:
            logging.warning(
                f"⚠️ Сетевая ошибка при ответе на /balance от @{user.username if user else 'unknown'}"
            )

    @router.message(Command("subs"))
    async def subscriptions_handler(message: Message) -> None:
        """Команда для отображения абонементов по дням (только для администратора)."""
        user = message.from_user
        if user is None:
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)

        if not is_admin:
            return

        rate_limit_error = rate_limit_check(user, is_admin=True)
        if rate_limit_error:
            try:
                await message.reply(rate_limit_error)
            except TelegramNetworkError:
                logging.warning("⚠️ Сетевая ошибка при отправке rate limit сообщения")
            return

        poll_templates = get_poll_templates()
        if not poll_templates:
            await message.reply("📅 Шаблоны опросов не найдены.")
            return

        players = get_all_players()
        players_by_id = {p["id"]: p for p in players if "id" in p}

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

        def pick_day(template: dict[str, object]) -> str:
            game_day = str(template.get("game_day") or "*").lower()
            if game_day and game_day != "*":
                return game_day
            open_day = str(template.get("open_day") or "*").lower()
            return open_day or "*"

        day_to_polls: dict[str, list[dict[str, object]]] = {}
        for template in poll_templates:
            day_key = pick_day(template)
            day_to_polls.setdefault(day_key, []).append(template)

        days_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun", "*"]
        ordered_days = [d for d in days_order if d in day_to_polls]
        ordered_days += sorted(d for d in day_to_polls.keys() if d not in days_order)

        lines = ["📅 <b>Абонементы по дням</b>"]

        for day_key in ordered_days:
            day_name = days_ru.get(day_key, day_key)
            lines.append(f"\n<b>{escape_html(day_name)}</b>")

            for template in day_to_polls.get(day_key, []):
                poll_name = str(template.get("name") or "Без названия")
                place = str(template.get("place") or "")
                hour = template.get("game_hour_utc")
                minute = template.get("game_minute_utc")

                time_text = ""
                if isinstance(hour, int) and isinstance(minute, int):
                    msk_hour = (hour + 3) % 24
                    time_text = f"{msk_hour:02d}:{minute:02d} МСК"

                label = escape_html(poll_name)
                if time_text:
                    label = f"{label} ({time_text})"
                if place:
                    label = f"{label} — {escape_html(place)}"

                subs = template.get("subs") or []
                subs_links: list[str] = []
                subs_entries = []
                for user_id in subs:
                    if not isinstance(user_id, int):
                        continue
                    player = players_by_id.get(user_id)
                    sort_key = (
                        (player or {}).get("fullname")
                        or (player or {}).get("name")
                        or str(user_id)
                    )
                    subs_entries.append((str(sort_key).lower(), user_id, player))

                for _, user_id, player in sorted(subs_entries, key=lambda x: x[0]):
                    subs_links.append(format_player_link(player, user_id))

                if subs_links:
                    subs_text = ", ".join(subs_links)
                else:
                    subs_text = "— нет подписчиков"

                lines.append(f"{label}: {subs_text}")

        try:
            await message.reply(
                "\n".join(lines),
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            logging.info(
                f"📋 Запрос абонементов по дням от админа @{user.username} (ID: {user.id})"
            )
        except TelegramNetworkError:
            logging.warning(
                f"⚠️ Сетевая ошибка при ответе на /subs от @{user.username if user else 'unknown'}"
            )

    @router.message(Command("webhookinfo"))
    async def webhookinfo_handler(message: Message) -> None:
        """Команда для проверки статуса webhook (только для администратора)."""
        user = message.from_user
        if user is None:
            return

        # Получаем сервисы из workflow_data
        admin_service: AdminService = dp.workflow_data["admin_service"]

        # Проверяем, является ли пользователь администратором
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)

        if not is_admin:
            return

        try:
            webhook_info = await bot.get_webhook_info()
            info_text = (
                f"🔍 <b>Webhook Info</b>\n\n"
                f"URL: <code>{webhook_info.url}</code>\n"
                f"Pending updates: {webhook_info.pending_update_count}\n"
                f"Max connections: {webhook_info.max_connections}\n"
            )

            if webhook_info.last_error_date:
                info_text += f"\n⚠️ Last error: {webhook_info.last_error_message}\n"
                info_text += f"Last error date: {webhook_info.last_error_date}\n"

            if webhook_info.allowed_updates:
                info_text += (
                    f"\nAllowed updates: {', '.join(webhook_info.allowed_updates)}"
                )
            else:
                info_text += "\n✅ All update types allowed"

            await message.reply(info_text)
            logging.info(
                f"🔍 Webhook info запрошен админом @{user.username} (ID: {user.id})"
            )
        except Exception as e:
            await message.reply(f"❌ Ошибка получения webhook info: {e}")
            logging.error(f"❌ Ошибка при получении webhook info: {e}")

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

        if message.text is None:
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
                        player_links = []
                        for p in players[:10]:  # Ограничим 10 игроками
                            # Для кнопок используем простое текстовое представление
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
                            # Для текста сообщения используем гиперссылки
                            player_links.append(format_player_link(p))

                        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
                        # Формируем список с гиперссылками
                        players_list = "\n".join([f"• {link}" for link in player_links])
                        await message.reply(
                            f"❓ Найдено несколько игроков ({len(players)}). Выберите нужного:\n\n{players_list}",
                            reply_markup=reply_markup,
                            parse_mode="HTML",
                            link_preview_options=LinkPreviewOptions(is_disabled=True),
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
                # Форматируем имя с гиперссылкой
                player_link = format_player_link(new_balance_data, target_user_id)
                try:
                    await message.reply(
                        f"✅ Баланс игрока {player_link} изменен на {amount} ₽.\n"
                        f"💰 Текущий баланс: <b>{new_balance} ₽</b>",
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
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

    @router.message(Command("player"))
    async def player_handler(message: Message) -> None:
        """Команда для подробного вывода информации об игроках (только для администратора)."""
        user = message.from_user
        if user is None:
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)

        if not is_admin:
            return

        if message.text is None:
            return

        args = message.text.split()

        # 1. Ответ на сообщение — показать одного игрока
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            ensure_player(
                user_id=target_user.id,
                name=target_user.username,
                fullname=target_user.full_name,
            )
            p = get_player_info(target_user.id)
            if p:
                text = _format_player_detail(p)
                await message.reply(
                    text,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            else:
                await message.reply("❌ Не удалось получить данные игрока.")
            return

        # 2. Есть аргумент — поиск одного игрока по имени, @username или ID
        if len(args) >= 2:
            search_query = " ".join(args[1:]).strip()
            if not search_query:
                pass
            elif search_query.isdigit():
                pid = int(search_query)
                p = get_player_info(pid)
                if p:
                    text = _format_player_detail(p)
                    await message.reply(
                        text,
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                else:
                    await message.reply(f"❌ Игрок с ID {pid} не найден.")
                return
            else:
                clean_query = search_query.lstrip("@")
                players = find_player_by_name(clean_query)
                if not players:
                    await message.reply(f"❌ Игрок '{search_query}' не найден.")
                    return
                if len(players) == 1:
                    p = get_player_info(players[0]["id"])
                    if p:
                        text = _format_player_detail(p)
                        await message.reply(
                            text,
                            parse_mode="HTML",
                            link_preview_options=LinkPreviewOptions(is_disabled=True),
                        )
                    else:
                        await message.reply("❌ Не удалось получить данные игрока.")
                    return
                # Несколько совпадений — клавиатура выбора
                keyboard = []
                player_links = []
                for p in players[:10]:
                    p_name = (
                        f"{p['fullname'] or p['name']} (ID: {p['id']})"
                        if (p.get("fullname") or p.get("name"))
                        else f"ID: {p['id']}"
                    )
                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                text=p_name,
                                callback_data=f"player_select:{p['id']}",
                            )
                        ]
                    )
                    player_links.append(format_player_link(p))
                reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
                players_list = "\n".join([f"• {link}" for link in player_links])
                await message.reply(
                    f"❓ Найдено несколько игроков ({len(players)}). Выберите:\n\n{players_list}",
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return

        # 3. Без аргументов — список всех игроков (кратко)
        all_players = get_all_players()
        if not all_players:
            await message.reply("📋 В базе пока нет игроков.")
            return

        lines = ["👥 <b>Игроки</b> ({}) — кратко:\n".format(len(all_players))]
        for p in all_players:
            link = format_player_link(p)
            balance = p.get("balance", 0)
            ball = "да" if p.get("ball_donate") else "нет"
            lines.append(f"• {link} — {balance} ₽, мяч: {ball}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = "\n".join(lines[:1] + lines[1:81]) + "\n\n… и ещё (показаны первые 80)."
        await message.reply(
            text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.callback_query(lambda c: c.data and c.data.startswith("player_select:"))
    async def process_player_select(callback_query: CallbackQuery):
        """Обработка выбора игрока из списка для просмотра информации."""
        user = callback_query.from_user
        if user is None:
            await callback_query.answer("❌ Ошибка: нет информации о пользователе", show_alert=True)
            return

        if callback_query.message is None:
            await callback_query.answer("❌ Ошибка: сообщение не найдено", show_alert=True)
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )
        if not is_admin:
            await callback_query.answer("❌ Нет прав для этого действия.", show_alert=True)
            return

        if callback_query.data is None:
            await callback_query.answer("❌ Ошибка данных.", show_alert=True)
            return

        parts = callback_query.data.split(":", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            await callback_query.answer("❌ Ошибка формата.", show_alert=True)
            return

        player_id = int(parts[1])
        p = get_player_info(player_id)
        if not p:
            await callback_query.answer("❌ Игрок не найден.", show_alert=True)
            return

        text = _format_player_detail(p)
        if not isinstance(callback_query.message, InaccessibleMessage):
            await callback_query.message.edit_text(
                text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        await callback_query.answer()

    @router.callback_query(lambda c: c.data and c.data.startswith("pay_select:"))
    async def process_pay_select(callback_query: CallbackQuery):
        """Обработка выбора игрока из списка для изменения баланса."""
        user = callback_query.from_user
        if user is None:
            logging.error("❌ callback_query.from_user is None")
            await callback_query.answer(
                "❌ Ошибка: нет информации о пользователе", show_alert=True
            )
            return

        if callback_query.message is None:
            logging.error("❌ callback_query.message is None")
            await callback_query.answer(
                "❌ Ошибка: сообщение не найдено", show_alert=True
            )
            return

        # Получаем сервисы из workflow_data
        admin_service: AdminService = dp.workflow_data["admin_service"]

        # Проверяем, является ли пользователь администратором
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )

        if not is_admin:
            await callback_query.answer(
                "❌ У вас нет прав для этого действия.", show_alert=True
            )
            return

        # Парсим callback_data: pay_select:player_id:amount
        if callback_query.data is None:
            await callback_query.answer("❌ Ошибка данных.", show_alert=True)
            return

        data_parts = callback_query.data.split(":")
        if len(data_parts) != 3:
            await callback_query.answer("❌ Ошибка данных.", show_alert=True)
            return

        try:
            target_user_id = int(data_parts[1])
            amount = int(data_parts[2])
        except ValueError:
            await callback_query.answer("❌ Ошибка формата данных.", show_alert=True)
            return

        if update_player_balance(target_user_id, amount):
            new_balance_data = get_player_balance(target_user_id)
            new_balance = (
                new_balance_data["balance"] if new_balance_data else "неизвестно"
            )

            # Форматируем имя с гиперссылкой
            player_link = format_player_link(new_balance_data, target_user_id)

            if callback_query.message and not isinstance(
                callback_query.message, InaccessibleMessage
            ):
                await callback_query.message.edit_text(
                    f"✅ Баланс игрока {player_link} изменен на {amount} ₽.\n"
                    f"💰 Текущий баланс: <b>{new_balance} ₽</b>",
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )

            await callback_query.answer()
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

        if data.poll_kind == "monthly_subscription":
            data.monthly_votes[user.id] = selected
            poll_service.persist_state()
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
    logging.info("✅ Все обработчики команд и событий зарегистрированы")
