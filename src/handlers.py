"""Обработчики команд бота."""

# VERSION MARKER: 2026-01-28-v2 - Добавлено детальное логирование callback_query

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
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
    User,
)

from .db import (
    add_transaction,
    create_backup,
    ensure_player,
    find_player_by_name,
    get_all_players,
    get_fund_balance,
    get_open_monthly_game,
    get_player_balance,
    get_player_info,
    get_player_stats,
    get_players_with_balance,
    get_poll_stats,
    get_poll_templates,
    get_stats_summary,
    get_unpaid_halls,
    load_state,
    record_hall_payment,
    save_poll_template,
    save_monthly_vote,
    save_state,
    update_fund_balance,
    update_player_balance,
)
from .scheduler import get_monthly_subscription_poll_params
from .services import AdminService, BotStateService, PollService
from .types import PollTemplate
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
        BotCommand(command="stats", description="Показать статистику"),
    ]

    # Команды для администраторов (включая пользовательские)
    admin_commands = [
        BotCommand(command="help", description="Показать справку по командам"),
        BotCommand(command="schedule", description="Показать расписание опросов"),
        BotCommand(command="balance", description="Показать долги/балансы и кассу"),
        BotCommand(command="stats", description="Статистика по играм и игрокам"),
        BotCommand(command="subs", description="Абонементы по дням"),
        BotCommand(command="pay", description="Изменить баланс / оплата зала"),
        BotCommand(command="restore", description="Восстановить баланс (без кассы)"),
        BotCommand(
            command="open_monthly", description="Тест: открыть опрос абонемента"
        ),
        BotCommand(
            command="close_monthly", description="Тест: закрыть опрос абонемента"
        ),
        BotCommand(command="player", description="Подробная информация об игроках"),
        BotCommand(command="poll_off", description="Выключить опрос из расписания"),
        BotCommand(command="poll_on", description="Включить опрос в расписание"),
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
        f"ID: {p['id']}",
    ]
    if p.get("name") and str(p["name"]).strip():
        lines.append(f"🪪 @{escape_html(str(p['name']).strip())}")
    lines.append(f"💰 Баланс: {p.get('balance', 0)} ₽")
    ball = "да" if p.get("ball_donate") else "нет"
    lines.append(f"🏐 Донат: {ball}")
    return "\n".join(lines)


def _is_poll_enabled(template: PollTemplate) -> bool:
    """Возвращает признак активности шаблона опроса."""
    return int(template.get("enabled", 1) or 0) == 1


def _format_poll_status(template: PollTemplate) -> str:
    """Форматирует статус шаблона опроса для UI."""
    return "активен" if _is_poll_enabled(template) else "⏸️ выключен"


def _format_poll_reference_line(template: PollTemplate) -> str:
    """Форматирует строку шаблона для подсказок в командах управления."""
    template_id = template.get("id", "?")
    poll_name = escape_html(str(template.get("name") or "Без названия"))
    return f"{template_id} — {poll_name} — {_format_poll_status(template)}"


def _find_poll_template(
    identifier: str,
) -> tuple[PollTemplate | None, list[PollTemplate]]:
    """Ищет шаблон опроса по id, точному имени или уникальной подстроке."""
    poll_templates = get_poll_templates()

    if identifier.isdigit():
        poll_id = int(identifier)
        return (
            next((p for p in poll_templates if int(p.get("id", 0) or 0) == poll_id), None),
            [],
        )

    normalized_identifier = identifier.strip().casefold()
    exact_matches = [
        p
        for p in poll_templates
        if str(p.get("name", "")).strip().casefold() == normalized_identifier
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], []

    partial_matches = [
        p
        for p in poll_templates
        if normalized_identifier in str(p.get("name", "")).strip().casefold()
    ]
    if len(partial_matches) == 1:
        return partial_matches[0], []

    return None, partial_matches


def register_handlers(dp: Dispatcher, bot: Bot) -> None:
    """
    Регистрирует все обработчики команд.

    Args:
        dp: Диспетчер бота
        bot: Экземпляр бота
    """

    # Создаём роутер для обработчиков
    router: Router = Router()

    @router.message(Command("losiento"))
    async def losiento_handler(message: Message) -> None:
        """Отправляет видео 'lo siento' по очереди из списка."""
        try:
            video_list = load_state("video_losiento_list", [])
            index = load_state("video_losiento_index", 0)

            if video_list:
                if index >= len(video_list):
                    index = 0
                await message.answer_video(video_list[index])
                save_state("video_losiento_index", index + 1)
            else:
                await message.answer("😔 Видео losiento пока нет.")
        except Exception:
            logging.exception("❌ Ошибка при отправке видео losiento")

    @router.message(Command("gay"))
    async def gay_handler(message: Message) -> None:
        """Отправляет видео 'gay' по очереди из списка."""
        try:
            video_list = load_state("video_gay_list", [])
            index = load_state("video_gay_index", 0)

            if video_list:
                if index >= len(video_list):
                    index = 0
                await message.answer_video(video_list[index])
                save_state("video_gay_index", index + 1)
            else:
                await message.answer(
                    "😔 Видео gay пока нет. Пришли мне видео с подписью 'gay'!"
                )
        except Exception:
            logging.exception("❌ Ошибка при отправке видео gay")

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
                create_backup("bot_start_command")
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
                create_backup("bot_stop_command")
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
            "/balance — список всех долгов + касса\n"
            "/subs — абонементы по дням\n"
            "/pay [сумма] — изменить баланс (в ответ на сообщение)\n"
            "/pay [имя] [сумма] — найти игрока и изменить баланс\n"
            "/pay Оплата зала — оплатить аренду зала из кассы\n"
            "/restore [сумма] — восстановить баланс без изменения кассы\n"
            "/restore [имя] [сумма] — найти игрока и восстановить баланс\n"
            "/player — список всех игроков с подробной информацией\n"
            "/player [имя] — информация об одном игроке (по имени, @username или ID)\n"
            "/poll_off [id|name] — выключить опрос из расписания\n"
            "/poll_on [id|name] — включить опрос в расписание\n"
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

        poll_templates = get_poll_templates()
        if not poll_templates:
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

        for poll in poll_templates:
            game_day = days_ru.get(str(poll["game_day"]), str(poll["game_day"]))

            # Конвертация в МСК (UTC+3)
            msk_hour = (int(poll["game_hour_utc"]) + 3) % 24
            msk_minute = int(poll["game_minute_utc"])

            place = str(poll.get("place") or "")
            place_info = f" ({place})" if place else ""
            status_info = "" if _is_poll_enabled(poll) else " ⏸️ выключен"

            schedule_text += (
                f"{game_day} {msk_hour:02d}:{msk_minute:02d}{place_info}{status_info}\n"
            )

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
            # Администратор видит кассу и всех с ненулевым балансом
            fund = get_fund_balance()
            players = get_players_with_balance()
            text = f"🏦 <b>Касса:</b> {fund} ₽\n\n"
            if not players:
                text += "💰 Все балансы на нуле. Долгов нет!"
            else:
                text += "💰 <b>Список балансов:</b>\n\n"
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

        def pick_day(template: PollTemplate) -> str:
            game_day = str(template.get("game_day") or "*").lower()
            if game_day and game_day != "*":
                return game_day
            open_day = str(template.get("open_day") or "*").lower()
            return open_day or "*"

        day_to_polls: dict[str, list[PollTemplate]] = {}
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
                if not _is_poll_enabled(template):
                    label = f"{label} — ⏸️ выключен"

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

    @router.message(Command("stats"))
    async def stats_handler(message: Message) -> None:
        """Показывает статистику по играм, залам и игрокам."""
        user = message.from_user
        if user is None:
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)
        if not is_admin:
            return

        args = (message.text or "").split()
        month: str | None = None
        text: str

        if len(args) == 1:
            month = datetime.now().strftime("%Y-%m")
            stats = get_stats_summary(month)
            text = (
                f"📊 <b>Статистика за {month}</b>\n\n"
                f"Игры: <b>{stats['games_count']}</b>\n"
                f"Уникальных игроков: <b>{stats['unique_players']}</b>\n"
                f"Средний основной состав: <b>{stats['avg_main']:.2f}</b>\n"
                f"Средний интерес: <b>{stats['avg_interest']:.2f}</b>\n"
                f"Участий по абонементу: <b>{stats['subscription_uses']}</b>\n"
                f"Разовых списаний: <b>{stats['single_game_charges']}</b>\n"
                f"Сумма разовых списаний: <b>{stats['single_game_sum']} ₽</b>\n"
                f"Пополнения: <b>{stats['topups_sum']} ₽</b>\n"
                f"Оплаты залов: <b>{stats['hall_payments_sum']} ₽</b>\n"
                f"Касса: <b>{stats['fund_balance']} ₽</b>\n"
                f"Месячных голосований: <b>{stats['monthly_polls']}</b>"
            )
        elif len(args) >= 3 and args[1] == "month":
            month = args[2]
            stats = get_stats_summary(month)
            text = (
                f"📊 <b>Статистика за {escape_html(month)}</b>\n\n"
                f"Игры: <b>{stats['games_count']}</b>\n"
                f"Уникальных игроков: <b>{stats['unique_players']}</b>\n"
                f"Средний основной состав: <b>{stats['avg_main']:.2f}</b>\n"
                f"Средний интерес: <b>{stats['avg_interest']:.2f}</b>\n"
                f"Участий по абонементу: <b>{stats['subscription_uses']}</b>\n"
                f"Разовых списаний: <b>{stats['single_game_charges']}</b>\n"
                f"Сумма разовых списаний: <b>{stats['single_game_sum']} ₽</b>\n"
                f"Пополнения: <b>{stats['topups_sum']} ₽</b>\n"
                f"Оплаты залов: <b>{stats['hall_payments_sum']} ₽</b>\n"
                f"Касса: <b>{stats['fund_balance']} ₽</b>"
            )
        elif len(args) >= 3 and args[1] == "poll":
            identifier = args[2]
            if len(args) >= 4:
                month = args[3]
            template, partial_matches = _find_poll_template(identifier)
            if template is None:
                if partial_matches:
                    lines = [
                        f"❌ Найдено несколько опросов по запросу <code>{escape_html(identifier)}</code>.",
                        "Уточните запрос. Подходящие варианты:",
                    ]
                    lines.extend(
                        _format_poll_reference_line(candidate)
                        for candidate in partial_matches
                    )
                    await message.reply("\n".join(lines), parse_mode="HTML")
                    return
                await message.reply(
                    f"❌ Опрос не найден: <code>{escape_html(identifier)}</code>",
                    parse_mode="HTML",
                )
                return
            stats = get_poll_stats(int(template["id"]), month)
            period = month or "всё время"
            text = (
                f"🏐 <b>Статистика зала</b>\n\n"
                f"Зал: <b>{escape_html(str(template.get('name') or ''))}</b>\n"
                f"Период: <b>{escape_html(period)}</b>\n"
                f"Игры: <b>{stats['games_count']}</b>\n"
                f"Уникальных игроков: <b>{stats['unique_players']}</b>\n"
                f"Средний основной состав: <b>{stats['avg_main']:.2f}</b>\n"
                f"Средний интерес: <b>{stats['avg_interest']:.2f}</b>\n"
                f"Участий по абонементу: <b>{stats['subscription_uses']}</b>\n"
                f"Сумма разовых списаний: <b>{stats['single_game_sum']} ₽</b>\n"
                f"Последняя игра: <b>{escape_html(str(stats['last_game'] or '—'))}</b>"
            )
        elif len(args) >= 3 and args[1] == "player":
            if message.reply_to_message and message.reply_to_message.from_user:
                target_user_id = message.reply_to_message.from_user.id
                if len(args) >= 4:
                    month = args[3]
            else:
                identifier = args[2].lstrip("@")
                if len(args) >= 4:
                    month = args[3]
                if identifier.isdigit():
                    target_user_id = int(identifier)
                else:
                    players = find_player_by_name(identifier)
                    if not players:
                        await message.reply(
                            f"❌ Игрок не найден: <code>{escape_html(identifier)}</code>",
                            parse_mode="HTML",
                        )
                        return
                    if len(players) > 1:
                        matches = "\n".join(
                            f"• {format_player_link(player)}"
                            for player in players[:10]
                        )
                        await message.reply(
                            "❌ Найдено несколько игроков. Уточните запрос:\n"
                            f"{matches}",
                            parse_mode="HTML",
                        )
                        return
                    target_user_id = int(players[0]["id"])
            player = get_player_info(target_user_id)
            stats = get_player_stats(target_user_id, month)
            player_name = (
                format_player_link(player, target_user_id)
                if player
                else f"ID: {target_user_id}"
            )
            period = month or "всё время"
            text = (
                f"👤 <b>Статистика игрока</b>\n\n"
                f"{player_name}\n"
                f"Период: <b>{escape_html(period)}</b>\n"
                f"Игр всего: <b>{stats['games_total']}</b>\n"
                f"Основной состав: <b>{stats['main_count']}</b>\n"
                f"Запасной: <b>{stats['reserve_count']}</b>\n"
                f"Забронирован: <b>{stats['booked_count']}</b>\n"
                f"Игр по абонементу: <b>{stats['subscription_games']}</b>\n"
                f"Разовых игр: <b>{stats['single_game_count']}</b>\n"
                f"Списано разово: <b>{stats['single_game_sum']} ₽</b>\n"
                f"Текущий баланс: <b>{stats['balance']} ₽</b>"
            )
        else:
            text = (
                "❌ Неверный формат.\n\n"
                "<code>/stats</code>\n"
                "<code>/stats month YYYY-MM</code>\n"
                "<code>/stats poll ID|Название [YYYY-MM]</code>\n"
                "<code>/stats player ID|@username [YYYY-MM]</code>"
            )

        await message.reply(
            text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    async def _set_poll_enabled(message: Message, enabled: bool) -> None:
        """Переключает состояние шаблона опроса по id или имени."""
        user = message.from_user
        if user is None:
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)
        if not is_admin:
            return

        raw_text = (message.text or "").strip()
        identifier = raw_text.split(maxsplit=1)[1].strip() if " " in raw_text else ""
        poll_templates = get_poll_templates()

        if not identifier:
            lines = [
                "❌ Укажите ID или точное название опроса.",
                "Примеры: <code>/poll_off 3</code>, <code>/poll_on Пятница</code>",
                "",
                "<b>Доступные опросы:</b>",
            ]
            lines.extend(
                _format_poll_reference_line(template) for template in poll_templates
            )
            await message.reply("\n".join(lines), parse_mode="HTML")
            return

        template, partial_matches = _find_poll_template(identifier)
        if template is None:
            if partial_matches:
                lines = [
                    f"❌ Найдено несколько опросов по запросу <code>{escape_html(identifier)}</code>.",
                    "Уточните запрос. Подходящие варианты:",
                ]
                lines.extend(
                    _format_poll_reference_line(candidate)
                    for candidate in partial_matches
                )
                await message.reply("\n".join(lines), parse_mode="HTML")
                return
            await message.reply(
                f"❌ Опрос не найден: <code>{escape_html(identifier)}</code>",
                parse_mode="HTML",
            )
            return

        poll_name = str(template.get("name") or "Без названия")
        current_enabled = _is_poll_enabled(template)
        desired_status = "активен" if enabled else "⏸️ выключен"

        if current_enabled == enabled:
            await message.reply(
                f"ℹ️ Опрос <b>{escape_html(poll_name)}</b> (ID: {template.get('id')}) уже {desired_status}.",
                parse_mode="HTML",
            )
            return

        template["enabled"] = 1 if enabled else 0
        save_poll_template(template)

        result_lines = [
            f"✅ Опрос <b>{escape_html(poll_name)}</b> (ID: {template.get('id')}) теперь {desired_status}.",
        ]
        if not enabled:
            result_lines.append(
                "Изменение повлияет на следующие плановые открытия после перезапуска или пересоздания расписания."
            )

        await message.reply("\n".join(result_lines), parse_mode="HTML")

    @router.message(Command("poll_off"))
    async def poll_off_handler(message: Message) -> None:
        """Выключает шаблон опроса из расписания."""
        await _set_poll_enabled(message, enabled=False)

    @router.message(Command("poll_on"))
    async def poll_on_handler(message: Message) -> None:
        """Включает шаблон опроса в расписание."""
        await _set_poll_enabled(message, enabled=True)

    @router.message(Command("open_monthly"))
    async def open_monthly_handler(message: Message) -> None:
        """Тест: вручную открыть месячный опрос на абонемент (только для администратора)."""
        user = message.from_user
        if user is None:
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)
        if not is_admin:
            return

        params = get_monthly_subscription_poll_params()
        if params is None:
            await message.reply(
                "❌ Нет платных залов. Добавьте опросы с cost > 0 в БД."
            )
            return
        if get_open_monthly_game() is not None:
            await message.reply("ℹ️ Месячный опрос уже открыт.")
            return

        bot_state_service: BotStateService = dp.workflow_data["bot_state_service"]
        poll_service: PollService = dp.workflow_data["poll_service"]
        chat_id = bot_state_service.get_chat_id()
        question, options, option_poll_names = params

        new_chat_id = await poll_service.send_poll(
            bot,
            chat_id,
            question,
            "monthly_subscription",
            bot_state_service.is_enabled(),
            subs=[],
            options=options,
            allows_multiple_answers=True,
            poll_kind="monthly_subscription",
            option_poll_names=option_poll_names,
            poll_template_id=None,
        )
        if new_chat_id != chat_id:
            bot_state_service.set_chat_id(new_chat_id)

        await message.reply(
            "✅ Месячный опрос открыт. Проголосуйте, затем используйте /close_monthly для закрытия."
        )

    @router.message(Command("close_monthly"))
    async def close_monthly_handler(message: Message) -> None:
        """Тест: вручную закрыть активный месячный опрос (только для администратора)."""
        user = message.from_user
        if user is None:
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)
        if not is_admin:
            return

        poll_service: PollService = dp.workflow_data["poll_service"]
        monthly_game = get_open_monthly_game()
        if monthly_game is None:
            await message.reply(
                "❌ Нет активного опроса. Сначала откройте месячный опрос: /open_monthly"
            )
            return

        await poll_service.close_poll(bot, str(monthly_game["poll_id"]))
        await message.reply("✅ Месячный опрос закрыт. Расчёт абонемента выполнен.")

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

    async def _resolve_target(
        message: Message, args: list[str], callback_prefix: str
    ) -> tuple[int | None, int, str] | None:
        """
        Определяет целевого игрока и сумму из аргументов команды.

        Возвращает (target_user_id, amount, target_name) если определено,
        или None если показана клавиатура / сообщение об ошибке.
        """
        target_user_id: int | None = None
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
                    return None
            else:
                await message.reply(
                    "❌ Укажите сумму.\nПример: <code>/pay 500</code> (в ответ на сообщение)",
                    parse_mode="HTML",
                )
                return None
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
                        return None
                else:
                    # Поиск по имени или @username (убираем @ если есть)
                    clean_query = search_query.lstrip("@")
                    players = find_player_by_name(clean_query)
                    if not players:
                        await message.reply(f"❌ Игрок '{search_query}' не найден.")
                        return None
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
                            callback_data = f"{callback_prefix}:{p['id']}:{amount}"
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
                        return None

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
                return None
        else:
            return (None, 0, "")

        return (target_user_id, amount, target_name)

    @router.message(Command("pay"))
    async def pay_handler(message: Message) -> None:
        """Команда для изменения баланса игрока или оплаты зала (только для администратора)."""
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

        # Специальный случай: /pay Оплата зала
        rest_text = " ".join(args[1:]).strip().lower() if len(args) > 1 else ""
        if rest_text == "оплата зала":
            await _handle_hall_payment(message, user)
            return

        result = await _resolve_target(message, args, "pay_select")
        if result is None:
            return

        target_user_id, amount, target_name = result

        if target_user_id is None or amount == 0:
            await message.reply(
                "ℹ️ <b>Управление балансом:</b>\n\n"
                "1. Ответьте на сообщение игрока: <code>/pay 500</code>\n"
                "2. Поиск по имени: <code>/pay Иван 500</code>\n"
                "3. По @username: <code>/pay @username 500</code>\n"
                "4. По ID игрока: <code>/pay 12345678 500</code>\n"
                "5. Оплата зала: <code>/pay Оплата зала</code>\n\n"
                "<i>Сумма может быть отрицательной для списания.</i>",
                parse_mode="HTML",
            )
            return

        if update_player_balance(target_user_id, amount):
            update_fund_balance(amount)
            admin_name = f"@{user.username}" if user.username else f"ID:{user.id}"
            add_transaction(target_user_id, amount, f"Оплата (admin: {admin_name})")
            new_balance_data = get_player_balance(target_user_id)
            new_balance = (
                new_balance_data["balance"] if new_balance_data else "неизвестно"
            )
            fund = get_fund_balance()
            player_link = format_player_link(new_balance_data, target_user_id)
            try:
                await message.reply(
                    f"✅ Баланс игрока {player_link} изменен на {amount} ₽.\n"
                    f"💰 Текущий баланс: <b>{new_balance} ₽</b>\n"
                    f"🏦 Касса: <b>{fund} ₽</b>",
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

    async def _handle_hall_payment(message: Message, user: User) -> None:
        """Обработка команды /pay Оплата зала."""
        current_month = datetime.now().strftime("%Y-%m")
        unpaid = get_unpaid_halls(current_month)

        if not unpaid:
            await message.reply(
                "✅ Все залы за этот месяц оплачены.",
                parse_mode="HTML",
            )
            return

        keyboard = []
        lines = [f"🏦 <b>Неоплаченные залы за {current_month}:</b>\n"]
        for hall in unpaid:
            poll_template_id = int(hall["id"])
            hall_name = str(hall.get("name", ""))
            monthly_cost = int(hall.get("monthly_cost", 0) or 0)
            place = str(hall.get("place", ""))
            label = f"{hall_name}"
            if place:
                label += f" ({place})"
            label += f" — {monthly_cost} ₽"
            lines.append(f"• {escape_html(label)}")
            callback_data = f"hall_pay:{poll_template_id}:{current_month}"
            keyboard.append(
                [InlineKeyboardButton(text=label, callback_data=callback_data)]
            )

        fund = get_fund_balance()
        lines.append(f"\n🏦 Касса: <b>{fund} ₽</b>")
        lines.append("\nВыберите зал для оплаты:")

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.reply(
            "\n".join(lines),
            reply_markup=reply_markup,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.message(Command("restore"))
    async def restore_handler(message: Message) -> None:
        """Команда для восстановления баланса игрока без изменения кассы (только для администратора)."""
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
        result = await _resolve_target(message, args, "restore_select")
        if result is None:
            return

        target_user_id, amount, target_name = result

        if target_user_id is None or amount == 0:
            await message.reply(
                "ℹ️ <b>Восстановление баланса</b> (касса не меняется):\n\n"
                "1. Ответьте на сообщение игрока: <code>/restore 150</code>\n"
                "2. Поиск по имени: <code>/restore Иван 150</code>\n"
                "3. По @username: <code>/restore @username 150</code>\n"
                "4. По ID игрока: <code>/restore 12345678 150</code>\n\n"
                "<i>Используйте для игроков, которые проголосовали, но не пришли.</i>",
                parse_mode="HTML",
            )
            return

        if update_player_balance(target_user_id, amount):
            admin_name = f"@{user.username}" if user.username else f"ID:{user.id}"
            add_transaction(
                target_user_id, amount, f"Восстановление (admin: {admin_name})"
            )
            new_balance_data = get_player_balance(target_user_id)
            new_balance = (
                new_balance_data["balance"] if new_balance_data else "неизвестно"
            )
            player_link = format_player_link(new_balance_data, target_user_id)
            try:
                await message.reply(
                    f"✅ Баланс игрока {player_link} восстановлен на {amount} ₽.\n"
                    f"💰 Текущий баланс: <b>{new_balance} ₽</b>\n"
                    f"<i>Касса не изменена.</i>",
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                logging.info(
                    f"🔄 Админ @{user.username} (ID: {user.id}) восстановил баланс {target_name} (ID: {target_user_id}) на {amount}"
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
            text = (
                "\n".join(lines[:1] + lines[1:81]) + "\n\n… и ещё (показаны первые 80)."
            )
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
            await callback_query.answer(
                "❌ Ошибка: нет информации о пользователе", show_alert=True
            )
            return

        if callback_query.message is None:
            await callback_query.answer(
                "❌ Ошибка: сообщение не найдено", show_alert=True
            )
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )
        if not is_admin:
            await callback_query.answer(
                "❌ Нет прав для этого действия.", show_alert=True
            )
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

    async def _process_balance_select(
        callback_query: CallbackQuery, update_fund: bool
    ) -> None:
        """Общая обработка выбора игрока из списка для изменения баланса."""
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

        admin_service: AdminService = dp.workflow_data["admin_service"]
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
            admin_name = f"@{user.username}" if user.username else f"ID:{user.id}"
            if update_fund:
                update_fund_balance(amount)
                add_transaction(target_user_id, amount, f"Оплата (admin: {admin_name})")
            else:
                add_transaction(
                    target_user_id, amount, f"Восстановление (admin: {admin_name})"
                )

            new_balance_data = get_player_balance(target_user_id)
            new_balance = (
                new_balance_data["balance"] if new_balance_data else "неизвестно"
            )
            player_link = format_player_link(new_balance_data, target_user_id)

            if update_fund:
                fund = get_fund_balance()
                result_text = (
                    f"✅ Баланс игрока {player_link} изменен на {amount} ₽.\n"
                    f"💰 Текущий баланс: <b>{new_balance} ₽</b>\n"
                    f"🏦 Касса: <b>{fund} ₽</b>"
                )
            else:
                result_text = (
                    f"✅ Баланс игрока {player_link} восстановлен на {amount} ₽.\n"
                    f"💰 Текущий баланс: <b>{new_balance} ₽</b>\n"
                    f"<i>Касса не изменена.</i>"
                )

            if callback_query.message and not isinstance(
                callback_query.message, InaccessibleMessage
            ):
                await callback_query.message.edit_text(
                    result_text,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )

            await callback_query.answer()
            action = "изменил" if update_fund else "восстановил"
            logging.info(
                f"💰 Админ @{user.username} (ID: {user.id}) {action} баланс через меню: "
                f"ID={target_user_id}, сумма={amount}"
            )
        else:
            await callback_query.answer(
                "❌ Не удалось обновить баланс.", show_alert=True
            )

    @router.callback_query(lambda c: c.data and c.data.startswith("pay_select:"))
    async def process_pay_select(callback_query: CallbackQuery):
        """Обработка выбора игрока из списка для изменения баланса."""
        await _process_balance_select(callback_query, update_fund=True)

    @router.callback_query(lambda c: c.data and c.data.startswith("restore_select:"))
    async def process_restore_select(callback_query: CallbackQuery):
        """Обработка выбора игрока из списка для восстановления баланса."""
        await _process_balance_select(callback_query, update_fund=False)

    @router.callback_query(lambda c: c.data and c.data.startswith("hall_pay:"))
    async def process_hall_pay(callback_query: CallbackQuery):
        """Обработка выбора зала для оплаты."""
        user = callback_query.from_user
        if user is None:
            await callback_query.answer(
                "❌ Ошибка: нет информации о пользователе", show_alert=True
            )
            return

        if callback_query.message is None:
            await callback_query.answer(
                "❌ Ошибка: сообщение не найдено", show_alert=True
            )
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )

        if not is_admin:
            await callback_query.answer(
                "❌ У вас нет прав для этого действия.", show_alert=True
            )
            return

        if callback_query.data is None:
            await callback_query.answer("❌ Ошибка данных.", show_alert=True)
            return

        # Парсим callback_data: hall_pay:{poll_template_id}:{month}
        parts = callback_query.data.split(":", 2)
        if len(parts) != 3:
            await callback_query.answer("❌ Ошибка формата данных.", show_alert=True)
            return

        try:
            poll_template_id = int(parts[1])
        except ValueError:
            await callback_query.answer("❌ Ошибка идентификатора зала.", show_alert=True)
            return
        month = parts[2]

        # Получаем стоимость зала
        poll_templates = get_poll_templates()
        hall = next((p for p in poll_templates if p["id"] == poll_template_id), None)
        if not hall:
            await callback_query.answer(
                "❌ Зал не найден.", show_alert=True
            )
            return
        poll_name = str(hall["name"])

        monthly_cost = int(hall.get("monthly_cost", 0) or 0)
        if monthly_cost <= 0:
            await callback_query.answer(
                "❌ У этого зала нулевая стоимость.", show_alert=True
            )
            return

        # Записываем оплату
        if not record_hall_payment(poll_template_id, month, monthly_cost):
            await callback_query.answer(
                f"⚠️ Зал '{poll_name}' за {month} уже оплачен.", show_alert=True
            )
            return

        update_fund_balance(-monthly_cost)
        add_transaction(
            user.id,
            -monthly_cost,
            f"Оплата зала: {poll_name} ({month})",
            poll_template_id=poll_template_id,
            poll_name_snapshot=poll_name,
        )

        fund = get_fund_balance()
        place = str(hall.get("place", ""))
        hall_label = poll_name
        if place:
            hall_label += f" ({place})"

        result_text = (
            f"✅ <b>Зал оплачен</b>\n\n"
            f"🏟 {escape_html(hall_label)}\n"
            f"📅 Месяц: {month}\n"
            f"💸 Сумма: {monthly_cost} ₽\n"
            f"🏦 Касса: <b>{fund} ₽</b>"
        )

        if callback_query.message and not isinstance(
            callback_query.message, InaccessibleMessage
        ):
            await callback_query.message.edit_text(
                result_text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )

        await callback_query.answer()
        logging.info(
            f"🏟 Админ @{user.username} (ID: {user.id}) оплатил зал {poll_name} за {month}: {monthly_cost}₽"
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

        if data.kind == "monthly_subscription":
            data.monthly_votes[user.id] = selected
            save_monthly_vote(poll_id, user.id, selected)
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
