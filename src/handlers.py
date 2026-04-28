"""Обработчики команд бота."""

# VERSION MARKER: 2026-01-28-v2 - Добавлено детальное логирование callback_query

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
    add_poll_subscription,
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
    record_hall_payment_atomic,
    save_monthly_vote,
    save_poll_template,
    save_state,
    toggle_player_ball_donate,
    update_player_and_fund_balance_atomic,
    update_player_and_transaction_atomic,
)
from .scheduler import refresh_scheduler
from .services import AdminService, BotStateService, PollService
from .types import PollTemplate
from .utils import (
    call_with_network_retry,
    count_games_in_month,
    escape_html,
    format_player_link,
    rate_limit_check,
    retry_async,
    validate_balance_callback_data,
    validate_hall_pay_callback_data,
    validate_player_select_callback_data,
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
        BotCommand(command="subs", description="Абонементы по дням / добавить"),
        BotCommand(command="pay", description="Изменить баланс / оплата зала"),
        BotCommand(command="restore", description="Восстановить баланс (без кассы)"),
        BotCommand(
            command="open_monthly", description="Тест: открыть опрос абонемента"
        ),
        BotCommand(
            command="close_monthly", description="Тест: закрыть опрос абонемента"
        ),
        BotCommand(command="player", description="Подробная информация об игроках"),
        BotCommand(
            command="ball_donate", description="Переключить донат мяча у игрока"
        ),
        BotCommand(command="hall", description="Управление залами"),
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


def _format_player_choice_label(p: dict) -> str:
    """Форматирует игрока для списков выбора с указанием баланса."""
    display_name = p.get("fullname") or p.get("name") or f"ID: {p['id']}"
    balance = int(p.get("balance", 0) or 0)
    return f"{display_name} (ID: {p['id']}, баланс: {balance} ₽)"


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
            next(
                (p for p in poll_templates if int(p.get("id", 0) or 0) == poll_id), None
            ),
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


class HallWizard(StatesGroup):
    """Состояния пошагового мастера управления залами."""

    name = State()
    place = State()
    game_day = State()
    game_time = State()
    open_day = State()
    open_time = State()
    cost = State()
    cost_per_game = State()
    message = State()
    enabled = State()
    confirm = State()


HALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
HALL_DAY_LABELS = {
    "mon": "Пн",
    "tue": "Вт",
    "wed": "Ср",
    "thu": "Чт",
    "fri": "Пт",
    "sat": "Сб",
    "sun": "Вс",
    "*": "Каждый день",
}
HALL_DAY_ALIASES = {
    "mon": "mon",
    "monday": "mon",
    "пн": "mon",
    "понедельник": "mon",
    "tue": "tue",
    "tuesday": "tue",
    "вт": "tue",
    "вторник": "tue",
    "wed": "wed",
    "wednesday": "wed",
    "ср": "wed",
    "среда": "wed",
    "thu": "thu",
    "thursday": "thu",
    "чт": "thu",
    "четверг": "thu",
    "fri": "fri",
    "friday": "fri",
    "пт": "fri",
    "пятница": "fri",
    "sat": "sat",
    "saturday": "sat",
    "сб": "sat",
    "суббота": "sat",
    "sun": "sun",
    "sunday": "sun",
    "вс": "sun",
    "воскресенье": "sun",
}
HALL_WIZARD_STEPS: list[tuple[str, State]] = [
    ("name", HallWizard.name),
    ("place", HallWizard.place),
    ("game_day_msk", HallWizard.game_day),
    ("game_time_msk", HallWizard.game_time),
    ("open_day_msk", HallWizard.open_day),
    ("open_time_msk", HallWizard.open_time),
    ("cost", HallWizard.cost),
    ("cost_per_game", HallWizard.cost_per_game),
    ("message", HallWizard.message),
    ("enabled", HallWizard.enabled),
]


def _previous_hall_day(day: str) -> str:
    """Возвращает предыдущий день недели."""
    return HALL_DAYS[(HALL_DAYS.index(day) - 1) % len(HALL_DAYS)]


def _parse_hall_day(value: str) -> str | None:
    """Парсит день недели из русского/английского алиаса."""
    normalized = value.strip().lower().replace(".", "")
    return HALL_DAY_ALIASES.get(normalized)


def _parse_hall_time(value: str) -> tuple[int, int] | None:
    """Парсит время HH:MM."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _parse_hall_enabled(value: str) -> int | None:
    """Парсит признак включения зала."""
    normalized = value.strip().lower()
    if normalized in {"1", "yes", "y", "true", "on", "да", "д", "вкл"}:
        return 1
    if normalized in {"0", "no", "n", "false", "off", "нет", "н", "выкл"}:
        return 0
    return None


def _utc_to_msk_day_time(day: str, hour: int, minute: int) -> tuple[str, str]:
    """Конвертирует день/время UTC в день/время МСК."""
    total_minutes = hour * 60 + minute + 3 * 60
    day_shift = total_minutes // (24 * 60)
    msk_minutes = total_minutes % (24 * 60)
    if day in HALL_DAYS:
        day = HALL_DAYS[(HALL_DAYS.index(day) + day_shift) % len(HALL_DAYS)]
    return day, f"{msk_minutes // 60:02d}:{msk_minutes % 60:02d}"


def _msk_to_utc_day_time(day: str, time_text: str) -> tuple[str, int, int]:
    """Конвертирует день/время МСК в день/время UTC."""
    parsed_time = _parse_hall_time(time_text)
    if parsed_time is None:
        raise ValueError("invalid time")
    hour, minute = parsed_time
    total_minutes = hour * 60 + minute - 3 * 60
    day_shift = total_minutes // (24 * 60)
    utc_minutes = total_minutes % (24 * 60)
    if day in HALL_DAYS:
        day = HALL_DAYS[(HALL_DAYS.index(day) + day_shift) % len(HALL_DAYS)]
    return day, utc_minutes // 60, utc_minutes % 60


def _prepare_hall_template_for_wizard(template: PollTemplate) -> dict[str, object]:
    """Добавляет в шаблон удобные для UI МСК-поля."""
    prepared = dict(template)
    game_day, game_time = _utc_to_msk_day_time(
        str(template.get("game_day", "mon") or "mon"),
        int(template.get("game_hour_utc", 0) or 0),
        int(template.get("game_minute_utc", 0) or 0),
    )
    open_day, open_time = _utc_to_msk_day_time(
        str(template.get("open_day", "mon") or "mon"),
        int(template.get("open_hour_utc", 0) or 0),
        int(template.get("open_minute_utc", 0) or 0),
    )
    prepared["game_day_msk"] = game_day
    prepared["game_time_msk"] = game_time
    prepared["open_day_msk"] = open_day
    prepared["open_time_msk"] = open_time
    return prepared


def _build_hall_template_for_save(template: dict[str, object]) -> dict[str, object]:
    """Готовит шаблон мастера к сохранению в БД."""
    game_day, game_hour, game_minute = _msk_to_utc_day_time(
        str(template["game_day_msk"]), str(template["game_time_msk"])
    )
    open_day, open_hour, open_minute = _msk_to_utc_day_time(
        str(template["open_day_msk"]), str(template["open_time_msk"])
    )
    result = {
        "name": str(template["name"]).strip(),
        "place": str(template.get("place") or "").strip(),
        "message": str(template["message"]).strip(),
        "game_day": game_day,
        "game_hour_utc": game_hour,
        "game_minute_utc": game_minute,
        "open_day": open_day,
        "open_hour_utc": open_hour,
        "open_minute_utc": open_minute,
        "cost": int(template.get("cost", 0) or 0),
        "cost_per_game": int(template.get("cost_per_game", 0) or 0),
        "enabled": int(template.get("enabled", 1) or 0),
    }
    if "id" in template:
        result["id"] = int(template["id"])
    if "subs" in template:
        result["subs"] = template["subs"]
    return result


def _format_hall_time_summary(template: PollTemplate | dict[str, object]) -> str:
    """Форматирует время игры для списков залов."""
    day, time_text = _utc_to_msk_day_time(
        str(template.get("game_day", "mon") or "mon"),
        int(template.get("game_hour_utc", 0) or 0),
        int(template.get("game_minute_utc", 0) or 0),
    )
    return f"{HALL_DAY_LABELS.get(day, day)} {time_text} МСК"


def _format_hall_wizard_summary(template: dict[str, object]) -> str:
    """Форматирует сводку перед сохранением зала."""
    enabled = "включён" if int(template.get("enabled", 1) or 0) == 1 else "выключен"
    place = str(template.get("place") or "не указано")
    return (
        "🏟 <b>Проверьте зал</b>\n\n"
        f"Название: <b>{escape_html(str(template.get('name') or ''))}</b>\n"
        f"Место: {escape_html(place)}\n"
        f"Игра: {HALL_DAY_LABELS.get(str(template.get('game_day_msk')), str(template.get('game_day_msk')))} "
        f"{escape_html(str(template.get('game_time_msk')))} МСК\n"
        f"Опрос: {HALL_DAY_LABELS.get(str(template.get('open_day_msk')), str(template.get('open_day_msk')))} "
        f"{escape_html(str(template.get('open_time_msk')))} МСК\n"
        f"Разовая игра: <b>{int(template.get('cost', 0) or 0)} ₽</b>\n"
        f"Аренда за игру: <b>{int(template.get('cost_per_game', 0) or 0)} ₽</b>\n"
        f"Текст опроса: {escape_html(str(template.get('message') or ''))}\n"
        f"Статус: {enabled}"
    )


def register_handlers(dp: Dispatcher, bot: Bot) -> None:
    """
    Регистрирует все обработчики команд.

    Args:
        dp: Диспетчер бота
        bot: Экземпляр бота
    """

    # Создаём роутер для обработчиков
    router: Router = Router()

    async def safe_reply(
        message: Message,
        text: str,
        *,
        action_name: str,
        **kwargs,
    ) -> bool:
        result = await call_with_network_retry(
            lambda: message.reply(text, **kwargs),
            action_name=action_name,
            exceptions=(TelegramNetworkError, asyncio.TimeoutError, OSError),
            logger=logging.getLogger(__name__),
        )
        return result is not None

    async def safe_answer_callback(
        callback_query: CallbackQuery,
        *,
        action_name: str,
        **kwargs,
    ) -> bool:
        result = await call_with_network_retry(
            lambda: callback_query.answer(**kwargs),
            action_name=action_name,
            exceptions=(TelegramNetworkError, asyncio.TimeoutError, OSError),
            logger=logging.getLogger(__name__),
        )
        return result is not None

    async def safe_edit_message_text(
        message: Message | InaccessibleMessage,
        text: str,
        *,
        action_name: str,
        **kwargs,
    ) -> bool:
        if isinstance(message, InaccessibleMessage):
            return False

        result = await call_with_network_retry(
            lambda: message.edit_text(text, **kwargs),
            action_name=action_name,
            exceptions=(TelegramNetworkError, asyncio.TimeoutError, OSError),
            logger=logging.getLogger(__name__),
        )
        return result is not None

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
            "/subs add HALL_ID игрок — добавить абонемент игроку\n"
            "/pay [сумма] — изменить баланс (в ответ на сообщение)\n"
            "/pay [имя] [сумма] — найти игрока и изменить баланс\n"
            "/pay Оплата зала — оплатить аренду зала из кассы\n"
            "/restore [сумма] — восстановить баланс без изменения кассы\n"
            "/restore [имя] [сумма] — найти игрока и восстановить баланс\n"
            "/player — список всех игроков с подробной информацией\n"
            "/player [имя] — информация об одном игроке (по имени, @username или ID)\n"
            "/ball_donate — переключить донат мяча у игрока (reply)\n"
            "/ball_donate [имя] — переключить донат мяча по имени, @username или ID\n"
            "/hall — управление залами и расписанием\n"
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

        transfer_details_note = "\n\nℹ️ Реквизиты для перевода — в описании группы."

        if is_admin:
            # Администратор видит кассу и всех с ненулевым балансом
            fund = get_fund_balance()
            players = get_players_with_balance()
            text = f"🏦 <b>Касса:</b> {fund} ₽\n\n"
            if not players:
                text += "💰 Все балансы на нуле. Долгов нет!"
            else:
                positive_players = [p for p in players if p.get("balance", 0) > 0]
                debtor_players = [p for p in players if p.get("balance", 0) < 0]

                if positive_players:
                    text += "🟢 <b>Положительный баланс:</b>\n\n"
                    for p in positive_players:
                        balance = p["balance"]
                        player_link = format_player_link(p)
                        text += f"🟢 {player_link}: <b>{balance} ₽</b>\n"
                    text += "\n"

                if debtor_players:
                    text += "🔴 <b>Отрицательный баланс:</b>\n\n"
                    for p in debtor_players:
                        balance = p["balance"]
                        username = str(p.get("name") or "").strip().lstrip("@")
                        if username:
                            fullname = str(p.get("fullname") or "").strip()
                            username_mention = f"@{escape_html(username)}"
                            if fullname:
                                debtor_ref = (
                                    f"{escape_html(fullname)} ({username_mention})"
                                )
                            else:
                                debtor_ref = username_mention
                        else:
                            debtor_ref = format_player_link(p)
                        text += f"🔴 {debtor_ref}: <b>{balance} ₽</b>\n"
            text += transfer_details_note
        else:
            # Обычный пользователь видит только свой баланс
            player = get_player_balance(user.id)
            if player:
                balance = player["balance"]
                if balance == 0:
                    text = "Чётко-чётко. Долгов нет 🤝"
                elif balance < 0:
                    text = f"💰 Ваш баланс: <b>{balance} ₽</b>. Пожалуйста, пополните."
                else:
                    text = f"💰 Ваш баланс: <b>{balance} ₽</b>. Спасибо за предоплату!"
                text += transfer_details_note
            else:
                text = (
                    "💰 Информация о вашем балансе не найдена. "
                    "Обратитесь к администратору."
                    f"{transfer_details_note}"
                )

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

    def _format_subs_add_usage() -> str:
        """Возвращает подсказку для добавления абонемента."""
        return (
            "ℹ️ <b>Добавление абонемента</b>\n\n"
            "Формат: <code>/subs add HALL_ID игрок</code>\n\n"
            "Примеры:\n"
            "<code>/subs add 3 @username</code>\n"
            "<code>/subs add 3 12345678</code>\n"
            "<code>/subs add 3 Иван Петров</code>\n\n"
            "ID зала можно посмотреть командой <code>/hall</code>."
        )

    def _validate_subs_add_select_callback_data(data: str) -> tuple[int, int] | None:
        """Парсит callback выбора игрока для /subs add."""
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "subs_add_select":
            return None
        _, raw_hall_id, raw_player_id = parts

        try:
            poll_template_id = int(raw_hall_id)
            player_id = int(raw_player_id)
        except ValueError:
            return None

        if poll_template_id <= 0 or player_id <= 0:
            return None

        return poll_template_id, player_id

    def _format_hall_label(template: PollTemplate) -> str:
        """Форматирует название зала с местом для ответа."""
        poll_name = str(template.get("name") or "Без названия")
        place = str(template.get("place") or "")
        label = poll_name
        if place:
            label += f" ({place})"
        return label

    def _add_subscription_result_text(
        poll_template_id: int, target_user_id: int
    ) -> str:
        """Добавляет подписку и возвращает HTML-ответ для пользователя."""
        hall = _get_hall_by_id(poll_template_id)
        if hall is None:
            return f"❌ Зал с ID {poll_template_id} не найден."

        player = get_player_info(target_user_id)
        if player is None:
            return f"❌ Игрок с ID {target_user_id} не найден."

        hall_label = escape_html(_format_hall_label(hall))
        player_link = format_player_link(player, target_user_id)
        existing_subs = hall.get("subs") or []
        if target_user_id in existing_subs:
            return (
                f"ℹ️ У игрока {player_link} уже есть абонемент на зал "
                f"<b>{hall_label}</b>."
            )

        create_backup("subs_command")
        status = add_poll_subscription(poll_template_id, target_user_id)
        if status == "success":
            return (
                f"✅ Абонемент добавлен.\n"
                f"Игрок: {player_link}\n"
                f"Зал: <b>{hall_label}</b>"
            )
        if status == "duplicate":
            return (
                f"ℹ️ У игрока {player_link} уже есть абонемент на зал "
                f"<b>{hall_label}</b>."
            )
        if status == "missing_hall":
            return f"❌ Зал с ID {poll_template_id} не найден."
        if status == "missing_player":
            return f"❌ Игрок с ID {target_user_id} не найден."
        return "❌ Не удалось добавить абонемент. Попробуйте ещё раз."

    async def _handle_subs_add(message: Message, args: list[str]) -> None:
        """Обрабатывает /subs add HALL_ID PLAYER."""
        if len(args) < 4:
            await safe_reply(
                message,
                _format_subs_add_usage(),
                parse_mode="HTML",
                action_name="reply to /subs add usage",
            )
            return

        raw_hall_id = args[2].strip()
        if not raw_hall_id.isdigit() or int(raw_hall_id) <= 0:
            await safe_reply(
                message,
                "❌ Укажите числовой ID зала.\n\n" + _format_subs_add_usage(),
                parse_mode="HTML",
                action_name="reply to /subs add invalid hall id",
            )
            return

        poll_template_id = int(raw_hall_id)
        hall = _get_hall_by_id(poll_template_id)
        if hall is None:
            await safe_reply(
                message,
                f"❌ Зал с ID {poll_template_id} не найден.",
                parse_mode="HTML",
                action_name="reply to /subs add missing hall",
            )
            return

        search_query = " ".join(args[3:]).strip()
        if not search_query:
            await safe_reply(
                message,
                _format_subs_add_usage(),
                parse_mode="HTML",
                action_name="reply to /subs add missing player query",
            )
            return

        target_user_id: int | None = None
        if search_query.isdigit():
            target_user_id = int(search_query)
            if get_player_info(target_user_id) is None:
                await safe_reply(
                    message,
                    f"❌ Игрок с ID {target_user_id} не найден.",
                    parse_mode="HTML",
                    action_name="reply to /subs add missing player by id",
                )
                return
        else:
            clean_query = search_query.lstrip("@")
            players = find_player_by_name(clean_query)
            if not players:
                await safe_reply(
                    message,
                    f"❌ Игрок '{escape_html(search_query)}' не найден.",
                    parse_mode="HTML",
                    action_name="reply to /subs add missing player by name",
                )
                return
            if len(players) > 1:
                keyboard = []
                player_lines = []
                for player in players[:10]:
                    p_name = _format_player_choice_label(player)
                    callback_data = (
                        f"subs_add_select:{poll_template_id}:{player['id']}"
                    )
                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                text=p_name,
                                callback_data=callback_data,
                            )
                        ]
                    )
                    player_lines.append(f"• {format_player_link(player)}")

                hall_label = escape_html(_format_hall_label(hall))
                await safe_reply(
                    message,
                    f"❓ Найдено несколько игроков ({len(players)}). "
                    f"Выберите, кому добавить абонемент на <b>{hall_label}</b>:\n\n"
                    + "\n".join(player_lines),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    action_name="reply to /subs add ambiguity",
                )
                return

            target_user_id = int(players[0]["id"])

        result_text = _add_subscription_result_text(poll_template_id, target_user_id)
        await safe_reply(
            message,
            result_text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            action_name="reply to /subs add result",
        )

    @router.message(Command("subs"))
    async def subscriptions_handler(message: Message) -> None:
        """Команда для отображения и добавления абонементов (только для администратора)."""
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

        args = (message.text or "").split()
        if len(args) >= 2:
            action = args[1].lower()
            if action == "add":
                await _handle_subs_add(message, args)
                return
            await safe_reply(
                message,
                "❌ Неизвестное действие для /subs.\n\n"
                "<code>/subs</code> — список абонементов\n"
                "<code>/subs add HALL_ID игрок</code> — добавить абонемент",
                parse_mode="HTML",
                action_name="reply to /subs unknown action",
            )
            return

        poll_templates = get_poll_templates()
        if not poll_templates:
            await safe_reply(
                message,
                "📅 Шаблоны опросов не найдены.",
                action_name="reply to /subs empty templates",
            )
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
                    await safe_reply(
                        message,
                        "\n".join(lines),
                        parse_mode="HTML",
                        action_name="reply to /stats poll ambiguity",
                    )
                    return
                await safe_reply(
                    message,
                    f"❌ Опрос не найден: <code>{escape_html(identifier)}</code>",
                    parse_mode="HTML",
                    action_name="reply to /stats poll not found",
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
                        await safe_reply(
                            message,
                            f"❌ Игрок не найден: <code>{escape_html(identifier)}</code>",
                            parse_mode="HTML",
                            action_name="reply to /stats player not found",
                        )
                        return
                    if len(players) > 1:
                        matches = "\n".join(
                            f"• {format_player_link(player)}" for player in players[:10]
                        )
                        await safe_reply(
                            message,
                            "❌ Найдено несколько игроков. Уточните запрос:\n"
                            f"{matches}",
                            parse_mode="HTML",
                            action_name="reply to /stats player ambiguity",
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
                f"В листе ожидания: <b>{stats['booked_count']}</b>\n"
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

        await safe_reply(
            message,
            text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            action_name="reply to /stats",
        )

    async def _is_message_admin(message: Message) -> bool:
        """Проверяет права администратора для message-команды."""
        user = message.from_user
        if user is None:
            return False

        admin_service: AdminService = dp.workflow_data["admin_service"]
        return await admin_service.is_admin(bot, user, message.chat.id)

    def _get_hall_by_id(poll_template_id: int) -> PollTemplate | None:
        """Возвращает шаблон зала по ID."""
        return next(
            (
                template
                for template in get_poll_templates()
                if int(template.get("id", 0) or 0) == poll_template_id
            ),
            None,
        )

    def _hall_name_conflicts(name: str, current_id: int | None = None) -> bool:
        """Проверяет конфликт имени с другим залом."""
        normalized = name.strip()
        return any(
            str(template.get("name", "")).strip() == normalized
            and int(template.get("id", 0) or 0) != current_id
            for template in get_poll_templates()
        )

    async def _refresh_hall_scheduler() -> None:
        """Обновляет планировщик после изменения залов."""
        scheduler = dp.workflow_data["scheduler"]
        bot_state_service: BotStateService = dp.workflow_data["bot_state_service"]
        poll_service: PollService = dp.workflow_data["poll_service"]
        refresh_scheduler(scheduler, bot, bot_state_service, poll_service)

    async def _show_hall_list(message: Message) -> None:
        """Показывает список залов."""
        poll_templates = get_poll_templates()
        if not poll_templates:
            await safe_reply(
                message,
                "🏟 Залы пока не настроены.\nДобавьте первый зал: <code>/hall add</code>",
                parse_mode="HTML",
                action_name="reply to /hall empty list",
            )
            return

        lines = ["🏟 <b>Залы</b>\n"]
        for template in poll_templates:
            template_id = int(template.get("id", 0) or 0)
            name = escape_html(str(template.get("name") or "Без названия"))
            place = str(template.get("place") or "")
            place_text = f" ({escape_html(place)})" if place else ""
            status = _format_poll_status(template)
            time_text = _format_hall_time_summary(template)
            cost = int(template.get("cost", 0) or 0)
            rent = int(template.get("cost_per_game", 0) or 0)
            lines.append(
                f"{template_id}. <b>{name}</b>{place_text} — {time_text} — "
                f"{cost} ₽ / аренда {rent} ₽ — {status}"
            )

        lines.append(
            "\nКоманды: <code>/hall add</code>, <code>/hall edit ID</code>, "
            "<code>/hall on ID</code>, <code>/hall off ID</code>."
        )
        await safe_reply(
            message,
            "\n".join(lines),
            parse_mode="HTML",
            action_name="reply to /hall list",
        )

    def _hall_step_prompt(field: str, template: dict[str, object], mode: str) -> str:
        """Возвращает текст вопроса для текущего шага мастера."""
        keep_text = ""
        if mode == "edit":
            current = template.get(field)
            if field in {"game_day_msk", "open_day_msk"}:
                current = HALL_DAY_LABELS.get(str(current), str(current))
            keep_text = f"\nТекущее значение: <b>{escape_html(str(current or ''))}</b>\nОтправьте <code>-</code>, чтобы оставить без изменений."

        default_text = ""
        if mode == "add":
            if field == "open_day_msk":
                game_day = str(template.get("game_day_msk") or "")
                if game_day in HALL_DAYS:
                    default_day = HALL_DAY_LABELS[_previous_hall_day(game_day)]
                else:
                    default_day = "предыдущий день от дня игры"
                default_text = f"\nПо умолчанию: <b>{escape_html(default_day)}</b>."
            elif field == "open_time_msk":
                default_text = "\nПо умолчанию: <b>19:00</b>."
            elif field == "message":
                hall_name = str(template.get("name") or "зал")
                default_message = f"Играем в {hall_name}?"
                default_text = (
                    f"\nПо умолчанию: <b>{escape_html(default_message)}</b>."
                )
            elif field == "enabled":
                default_text = "\nПо умолчанию: <b>да</b>."

        prompts = {
            "name": "Введите название зала.",
            "place": "Введите место/адрес зала. Для пустого значения отправьте <code>-</code>.",
            "game_day_msk": "Введите день игры: Пн, Вт, Ср, Чт, Пт, Сб или Вс.",
            "game_time_msk": "Введите время игры в МСК в формате <code>HH:MM</code>.",
            "open_day_msk": "Введите день открытия опроса. Отправьте <code>-</code>, чтобы использовать значение по умолчанию.",
            "open_time_msk": "Введите время открытия опроса в МСК в формате <code>HH:MM</code>. Отправьте <code>-</code>, чтобы использовать значение по умолчанию.",
            "cost": "Введите стоимость разовой игры в рублях.",
            "cost_per_game": "Введите стоимость аренды зала за одну игру в рублях.",
            "message": "Введите текст вопроса для Telegram-опроса. Отправьте <code>-</code>, чтобы использовать текст по умолчанию.",
            "enabled": "Включить зал в расписание? Ответьте <code>да</code> или <code>нет</code>. Отправьте <code>-</code>, чтобы использовать значение по умолчанию.",
        }
        return (
            f"{prompts[field]}{default_text}{keep_text}\n\n"
            "Отмена: <code>/cancel</code>"
        )

    async def _ask_hall_step(
        message: Message, state: FSMContext, step_index: int
    ) -> None:
        """Задаёт следующий вопрос мастера."""
        data = await state.get_data()
        template = dict(data.get("template", {}))
        mode = str(data.get("mode", "add"))
        field, state_value = HALL_WIZARD_STEPS[step_index]
        await state.update_data(step_index=step_index)
        await state.set_state(state_value)
        await safe_reply(
            message,
            _hall_step_prompt(field, template, mode),
            parse_mode="HTML",
            action_name="reply to hall wizard step",
        )

    async def _show_hall_confirm(message: Message, state: FSMContext) -> None:
        """Показывает финальную сводку мастера."""
        data = await state.get_data()
        template = dict(data.get("template", {}))
        await state.set_state(HallWizard.confirm)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Сохранить", callback_data="hall_save"),
                    InlineKeyboardButton(text="Отменить", callback_data="hall_cancel"),
                ]
            ]
        )
        await safe_reply(
            message,
            _format_hall_wizard_summary(template),
            reply_markup=keyboard,
            parse_mode="HTML",
            action_name="reply to hall wizard confirm",
        )

    async def _start_hall_add(message: Message, state: FSMContext) -> None:
        """Запускает мастер добавления зала."""
        await state.clear()
        await state.update_data(mode="add", template={}, step_index=0)
        await _ask_hall_step(message, state, 0)

    async def _start_hall_edit(
        message: Message, state: FSMContext, poll_template_id: int
    ) -> None:
        """Запускает мастер редактирования зала."""
        template = _get_hall_by_id(poll_template_id)
        if template is None:
            await safe_reply(
                message,
                f"❌ Зал с ID {poll_template_id} не найден.",
                parse_mode="HTML",
                action_name="reply to /hall edit missing",
            )
            return
        await state.clear()
        await state.update_data(
            mode="edit",
            template=_prepare_hall_template_for_wizard(template),
            step_index=0,
        )
        await _ask_hall_step(message, state, 0)

    async def _cancel_hall_wizard(message: Message, state: FSMContext) -> None:
        """Отменяет мастер зала."""
        await state.clear()
        await safe_reply(
            message,
            "✅ Действие отменено.",
            action_name="reply to hall wizard cancel",
        )

    async def _set_hall_enabled(message: Message, enabled: bool, raw_id: str) -> None:
        """Включает или выключает зал по ID."""
        if not raw_id.isdigit():
            await safe_reply(
                message,
                "❌ Укажите числовой ID зала.\nПример: <code>/hall off 3</code>",
                parse_mode="HTML",
                action_name="reply to /hall toggle invalid id",
            )
            return

        poll_template_id = int(raw_id)
        template = _get_hall_by_id(poll_template_id)
        if template is None:
            await safe_reply(
                message,
                f"❌ Зал с ID {poll_template_id} не найден.",
                parse_mode="HTML",
                action_name="reply to /hall toggle missing",
            )
            return

        poll_name = str(template.get("name") or "Без названия")
        current_enabled = _is_poll_enabled(template)
        desired_status = "активен" if enabled else "⏸️ выключен"

        if current_enabled == enabled:
            await safe_reply(
                message,
                f"ℹ️ Опрос <b>{escape_html(poll_name)}</b> (ID: {template.get('id')}) уже {desired_status}.",
                parse_mode="HTML",
                action_name="reply to poll toggle unchanged",
            )
            return

        template["enabled"] = 1 if enabled else 0
        create_backup("hall_command")
        saved_id = save_poll_template(template, match_by="id")
        if saved_id is None:
            await safe_reply(
                message,
                "❌ Не удалось сохранить изменения зала.",
                action_name="reply to /hall toggle save failure",
            )
            return

        await _refresh_hall_scheduler()

        result_lines = [
            f"✅ Опрос <b>{escape_html(poll_name)}</b> (ID: {template.get('id')}) теперь {desired_status}.",
        ]
        if not enabled:
            result_lines.append(
                "Расписание обновлено. Опрос больше не будет открываться автоматически."
            )
        else:
            result_lines.append(
                "Расписание обновлено. Опрос будет открываться по расписанию."
            )

        await safe_reply(
            message,
            "\n".join(result_lines),
            parse_mode="HTML",
            action_name="reply to poll toggle success",
        )

    @router.message(Command("hall"))
    async def hall_handler(message: Message, state: FSMContext) -> None:
        """Единая команда управления залами."""
        if not await _is_message_admin(message):
            return

        raw_text = (message.text or "").strip()
        parts = raw_text.split(maxsplit=2)
        action = parts[1].lower() if len(parts) >= 2 else "list"

        if action in {"cancel", "отмена"}:
            await _cancel_hall_wizard(message, state)
        elif action in {"list", "список"}:
            await _show_hall_list(message)
        elif action == "add":
            await _start_hall_add(message, state)
        elif action == "edit":
            if len(parts) < 3 or not parts[2].strip().isdigit():
                await safe_reply(
                    message,
                    "❌ Укажите ID зала.\nПример: <code>/hall edit 3</code>",
                    parse_mode="HTML",
                    action_name="reply to /hall edit usage",
                )
                return
            await _start_hall_edit(message, state, int(parts[2].strip()))
        elif action == "on":
            raw_id = parts[2].strip() if len(parts) >= 3 else ""
            await _set_hall_enabled(message, True, raw_id)
        elif action == "off":
            raw_id = parts[2].strip() if len(parts) >= 3 else ""
            await _set_hall_enabled(message, False, raw_id)
        else:
            await safe_reply(
                message,
                "🏟 <b>Управление залами</b>\n\n"
                "<code>/hall</code> или <code>/hall list</code> — список\n"
                "<code>/hall add</code> — добавить зал\n"
                "<code>/hall edit ID</code> — редактировать зал\n"
                "<code>/hall on ID</code> — включить зал\n"
                "<code>/hall off ID</code> — выключить зал\n"
                "<code>/hall cancel</code> — отменить мастер",
                parse_mode="HTML",
                action_name="reply to /hall usage",
            )

    @router.message(Command("cancel"))
    async def cancel_handler(message: Message, state: FSMContext) -> None:
        """Отменяет активный мастер."""
        current_state = await state.get_state()
        if current_state and current_state.startswith("HallWizard:"):
            await _cancel_hall_wizard(message, state)

    async def _process_hall_wizard_value(
        message: Message, state: FSMContext
    ) -> None:
        """Обрабатывает один ответ мастера add/edit."""
        if message.text is None:
            return
        if not await _is_message_admin(message):
            return

        raw_value = message.text.strip()
        if raw_value.lower() in {"/cancel", "/hall cancel", "отмена"}:
            await _cancel_hall_wizard(message, state)
            return

        data = await state.get_data()
        mode = str(data.get("mode", "add"))
        step_index = int(data.get("step_index", 0) or 0)
        field, _state_value = HALL_WIZARD_STEPS[step_index]
        template = dict(data.get("template", {}))
        is_skip = raw_value in {"-", "skip", "пропустить", "оставить"}

        def fail(text: str) -> None:
            raise ValueError(text)

        try:
            if is_skip and mode == "edit":
                pass
            elif field == "name":
                name = raw_value.strip()
                if not name:
                    fail("❌ Название не должно быть пустым.")
                current_id = int(template["id"]) if "id" in template else None
                if _hall_name_conflicts(name, current_id):
                    fail("❌ Зал с таким названием уже есть. Введите другое название.")
                template["name"] = name
            elif field == "place":
                template["place"] = "" if is_skip else raw_value
            elif field in {"game_day_msk", "open_day_msk"}:
                if is_skip and mode == "add" and field == "open_day_msk":
                    game_day = str(template.get("game_day_msk") or "")
                    if game_day not in HALL_DAYS:
                        fail("❌ Сначала нужен корректный день игры.")
                    template[field] = _previous_hall_day(game_day)
                else:
                    parsed_day = _parse_hall_day(raw_value)
                    if parsed_day is None:
                        fail("❌ День не распознан. Используйте Пн, Вт, Ср, Чт, Пт, Сб или Вс.")
                    template[field] = parsed_day
            elif field in {"game_time_msk", "open_time_msk"}:
                if is_skip and mode == "add" and field == "open_time_msk":
                    template[field] = "19:00"
                else:
                    parsed_time = _parse_hall_time(raw_value)
                    if parsed_time is None:
                        fail("❌ Время должно быть в формате HH:MM, например 20:30.")
                    template[field] = f"{parsed_time[0]:02d}:{parsed_time[1]:02d}"
            elif field in {"cost", "cost_per_game"}:
                try:
                    amount = int(raw_value)
                except ValueError:
                    fail("❌ Сумма должна быть целым числом.")
                if amount < 0:
                    fail("❌ Сумма не может быть отрицательной.")
                template[field] = amount
            elif field == "message":
                if is_skip and mode == "add":
                    hall_name = str(template.get("name") or "зал")
                    template["message"] = f"Играем в {hall_name}?"
                else:
                    poll_message = raw_value.strip()
                    if not poll_message:
                        fail("❌ Текст опроса не должен быть пустым.")
                    template["message"] = poll_message
            elif field == "enabled":
                if is_skip and mode == "add":
                    template["enabled"] = 1
                else:
                    enabled = _parse_hall_enabled(raw_value)
                    if enabled is None:
                        fail("❌ Ответьте да или нет.")
                    template["enabled"] = enabled
        except ValueError as exc:
            await safe_reply(
                message,
                str(exc),
                parse_mode="HTML",
                action_name="reply to hall wizard validation error",
            )
            await _ask_hall_step(message, state, step_index)
            return

        await state.update_data(template=template)
        next_step = step_index + 1
        if next_step >= len(HALL_WIZARD_STEPS):
            await _show_hall_confirm(message, state)
            return
        await _ask_hall_step(message, state, next_step)

    @router.message(HallWizard.name)
    @router.message(HallWizard.place)
    @router.message(HallWizard.game_day)
    @router.message(HallWizard.game_time)
    @router.message(HallWizard.open_day)
    @router.message(HallWizard.open_time)
    @router.message(HallWizard.cost)
    @router.message(HallWizard.cost_per_game)
    @router.message(HallWizard.message)
    @router.message(HallWizard.enabled)
    async def hall_wizard_value_handler(
        message: Message, state: FSMContext
    ) -> None:
        """Обрабатывает ответы мастера залов."""
        await _process_hall_wizard_value(message, state)

    @router.callback_query(lambda c: c.data in {"hall_save", "hall_cancel"})
    async def hall_wizard_confirm_handler(
        callback_query: CallbackQuery, state: FSMContext
    ) -> None:
        """Обрабатывает финальное подтверждение мастера залов."""
        user = callback_query.from_user
        if user is None or callback_query.message is None:
            return
        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )
        if not is_admin:
            await safe_answer_callback(
                callback_query,
                text="❌ У вас нет прав для этого действия.",
                show_alert=True,
                action_name="answer hall wizard forbidden",
            )
            return

        current_state = await state.get_state()
        data = await state.get_data()
        if current_state != HallWizard.confirm.state or not data.get("template"):
            await safe_answer_callback(
                callback_query,
                text="❌ Мастер уже завершён или устарел.",
                show_alert=True,
                action_name="answer hall wizard stale state",
            )
            return

        if callback_query.data == "hall_cancel":
            await state.clear()
            await safe_edit_message_text(
                callback_query.message,
                "✅ Действие отменено.",
                action_name="edit hall wizard cancel",
            )
            await safe_answer_callback(
                callback_query, action_name="answer hall wizard cancel"
            )
            return

        mode = str(data.get("mode", "add"))
        template = dict(data.get("template", {}))
        save_template = _build_hall_template_for_save(template)
        current_id = int(save_template["id"]) if "id" in save_template else None
        if _hall_name_conflicts(str(save_template["name"]), current_id):
            await state.set_state(HallWizard.name)
            await state.update_data(step_index=0)
            await safe_edit_message_text(
                callback_query.message,
                "❌ Зал с таким названием уже есть. Введите другое название.",
                parse_mode="HTML",
                action_name="edit hall wizard name conflict",
            )
            await safe_answer_callback(
                callback_query,
                text="Название уже занято.",
                show_alert=True,
                action_name="answer hall wizard name conflict",
            )
            return

        create_backup("hall_command")
        saved_id = save_poll_template(
            save_template,
            match_by="id" if mode == "edit" else "name",
        )
        if saved_id is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Не удалось сохранить зал.",
                show_alert=True,
                action_name="answer hall wizard save failure",
            )
            return

        await _refresh_hall_scheduler()
        await state.clear()
        action_text = "обновлён" if mode == "edit" else "добавлен"
        await safe_edit_message_text(
            callback_query.message,
            f"✅ Зал <b>{escape_html(str(save_template['name']))}</b> {action_text}. ID: {saved_id}",
            parse_mode="HTML",
            action_name="edit hall wizard success",
        )
        await safe_answer_callback(
            callback_query,
            action_name="answer hall wizard success",
        )

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

        poll_service: PollService = dp.workflow_data["poll_service"]
        if poll_service.build_monthly_subscription_poll_spec() is None:
            await safe_reply(
                message,
                "❌ Нет платных залов. Добавьте опросы с cost > 0 в БД.",
                action_name="reply to /open_monthly missing halls",
            )
            return
        if get_open_monthly_game() is not None:
            await safe_reply(
                message,
                "ℹ️ Месячный опрос уже открыт.",
                action_name="reply to /open_monthly already open",
            )
            return

        bot_state_service: BotStateService = dp.workflow_data["bot_state_service"]
        chat_id = bot_state_service.get_chat_id()
        new_chat_id = await poll_service.open_monthly_subscription_poll(
            bot,
            chat_id,
            bot_state_service.is_enabled(),
        )
        if new_chat_id != chat_id:
            bot_state_service.set_chat_id(new_chat_id)

        await safe_reply(
            message,
            "✅ Месячный опрос открыт. Проголосуйте, затем используйте /close_monthly для закрытия.",
            action_name="reply to /open_monthly success",
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
            await safe_reply(
                message,
                "❌ Нет активного опроса. Сначала откройте месячный опрос: /open_monthly",
                action_name="reply to /close_monthly missing poll",
            )
            return

        await poll_service.close_poll(bot, str(monthly_game["poll_id"]))
        await safe_reply(
            message,
            "✅ Месячный опрос закрыт. Расчёт абонемента выполнен.",
            action_name="reply to /close_monthly success",
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

            await safe_reply(
                message,
                info_text,
                action_name="reply to /webhookinfo",
            )
            logging.info(
                f"🔍 Webhook info запрошен админом @{user.username} (ID: {user.id})"
            )
        except Exception as e:
            await safe_reply(
                message,
                f"❌ Ошибка получения webhook info: {e}",
                action_name="reply to /webhookinfo error",
            )
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
                    await safe_reply(
                        message,
                        "❌ Ошибка: сумма должна быть числом.\nПример: <code>/pay 500</code>",
                        parse_mode="HTML",
                        action_name="reply to direct amount parse error",
                    )
                    return None
            else:
                await safe_reply(
                    message,
                    "❌ Укажите сумму.\nПример: <code>/pay 500</code> (в ответ на сообщение)",
                    parse_mode="HTML",
                    action_name="reply to missing amount",
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
                        await safe_reply(
                            message,
                            f"❌ Игрок с ID {target_user_id} не найден.",
                            action_name="reply to missing player by id",
                        )
                        return None
                else:
                    # Поиск по имени или @username (убираем @ если есть)
                    clean_query = search_query.lstrip("@")
                    players = find_player_by_name(clean_query)
                    if not players:
                        await safe_reply(
                            message,
                            f"❌ Игрок '{search_query}' не найден.",
                            action_name="reply to missing player by name",
                        )
                        return None
                    if len(players) > 1:
                        keyboard = []
                        player_lines = []
                        for p in players[:10]:  # Ограничим 10 игроками
                            p_name = _format_player_choice_label(p)
                            callback_data = f"{callback_prefix}:{p['id']}:{amount}"
                            keyboard.append(
                                [
                                    InlineKeyboardButton(
                                        text=p_name, callback_data=callback_data
                                    )
                                ]
                            )
                            player_lines.append(
                                f"• {format_player_link(p)} — <b>{int(p.get('balance', 0) or 0)} ₽</b>"
                            )

                        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
                        players_list = "\n".join(player_lines)
                        await safe_reply(
                            message,
                            f"❓ Найдено несколько игроков ({len(players)}). Выберите нужного:\n\n{players_list}",
                            reply_markup=reply_markup,
                            parse_mode="HTML",
                            link_preview_options=LinkPreviewOptions(is_disabled=True),
                            action_name="reply to ambiguous player search",
                        )
                        return None

                    target_user_id = players[0]["id"]
                    target_name = (
                        players[0]["fullname"]
                        or players[0]["name"]
                        or f"ID: {target_user_id}"
                    )
            except ValueError:
                await safe_reply(
                    message,
                    "❌ Ошибка: сумма должна быть числом в конце команды.\nПример: <code>/pay Иван 500</code>",
                    parse_mode="HTML",
                    action_name="reply to trailing amount parse error",
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
            await safe_reply(
                message,
                "ℹ️ <b>Управление балансом:</b>\n\n"
                "1. Ответьте на сообщение игрока: <code>/pay 500</code>\n"
                "2. Поиск по имени: <code>/pay Иван 500</code>\n"
                "3. По @username: <code>/pay @username 500</code>\n"
                "4. По ID игрока: <code>/pay 12345678 500</code>\n"
                "5. Оплата зала: <code>/pay Оплата зала</code>\n\n"
                "<i>Сумма может быть отрицательной для списания.</i>",
                parse_mode="HTML",
                action_name="reply to /pay usage",
            )
            return

        admin_name = f"@{user.username}" if user.username else f"ID:{user.id}"
        description = f"Оплата (admin: {admin_name})"
        
        if update_player_and_fund_balance_atomic(
            target_user_id, amount, description
        ):
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
            await safe_reply(
                message,
                "❌ Не удалось обновить баланс. Убедитесь, что игрок взаимодействовал с ботом ранее.",
                action_name="reply to /pay update failure",
            )

    async def _handle_hall_payment(message: Message, user: User) -> None:
        """Обработка команды /pay Оплата зала."""
        current_month = datetime.now().strftime("%Y-%m")
        unpaid = get_unpaid_halls(current_month)

        if not unpaid:
            await safe_reply(
                message,
                "✅ Все залы за этот месяц оплачены.",
                parse_mode="HTML",
                action_name="reply to hall payment no unpaid halls",
            )
            return

        keyboard = []
        lines = [f"🏦 <b>Неоплаченные залы за {current_month}:</b>\n"]
        for hall in unpaid:
            poll_template_id = int(hall["id"])
            hall_name = str(hall.get("name", ""))
            cost_per_game = int(hall.get("cost_per_game", 0) or 0)
            games_in_month = count_games_in_month(
                str(hall.get("game_day", "*") or "*"), current_month
            )
            monthly_rent = cost_per_game * games_in_month
            place = str(hall.get("place", ""))
            label = f"{hall_name}"
            if place:
                label += f" ({place})"
            label += f" — {cost_per_game}×{games_in_month} = {monthly_rent} ₽"
            lines.append(f"• {escape_html(label)}")
            callback_data = f"hall_pay:{poll_template_id}:{current_month}"
            keyboard.append(
                [InlineKeyboardButton(text=label, callback_data=callback_data)]
            )

        fund = get_fund_balance()
        lines.append(f"\n🏦 Касса: <b>{fund} ₽</b>")
        lines.append("\nВыберите зал для оплаты:")

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await safe_reply(
            message,
            "\n".join(lines),
            reply_markup=reply_markup,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            action_name="reply to hall payment selection",
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
            await safe_reply(
                message,
                "ℹ️ <b>Восстановление баланса</b> (касса не меняется):\n\n"
                "1. Ответьте на сообщение игрока: <code>/restore 150</code>\n"
                "2. Поиск по имени: <code>/restore Иван 150</code>\n"
                "3. По @username: <code>/restore @username 150</code>\n"
                "4. По ID игрока: <code>/restore 12345678 150</code>\n\n"
                "<i>Используйте для игроков, которые проголосовали, но не пришли.</i>",
                parse_mode="HTML",
                action_name="reply to /restore usage",
            )
            return

        admin_name = f"@{user.username}" if user.username else f"ID:{user.id}"
        description = f"Восстановление (admin: {admin_name})"
        
        if update_player_and_transaction_atomic(
            target_user_id, amount, description
        ):
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
            await safe_reply(
                message,
                "❌ Не удалось обновить баланс. Убедитесь, что игрок взаимодействовал с ботом ранее.",
                action_name="reply to /restore update failure",
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
                await safe_reply(
                    message,
                    text,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    action_name="reply to /player by reply",
                )
            else:
                await safe_reply(
                    message,
                    "❌ Не удалось получить данные игрока.",
                    action_name="reply to /player missing data",
                )
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
                    await safe_reply(
                        message,
                        text,
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                        action_name="reply to /player by id",
                    )
                else:
                    await safe_reply(
                        message,
                        f"❌ Игрок с ID {pid} не найден.",
                        action_name="reply to /player missing id",
                    )
                return
            else:
                clean_query = search_query.lstrip("@")
                players = find_player_by_name(clean_query)
                if not players:
                    await safe_reply(
                        message,
                        f"❌ Игрок '{search_query}' не найден.",
                        action_name="reply to /player missing name",
                    )
                    return
                if len(players) == 1:
                    p = get_player_info(players[0]["id"])
                    if p:
                        text = _format_player_detail(p)
                        await safe_reply(
                            message,
                            text,
                            parse_mode="HTML",
                            link_preview_options=LinkPreviewOptions(is_disabled=True),
                            action_name="reply to /player single match",
                        )
                    else:
                        await safe_reply(
                            message,
                            "❌ Не удалось получить данные игрока.",
                            action_name="reply to /player single missing data",
                        )
                    return
                # Несколько совпадений — клавиатура выбора
                keyboard = []
                player_lines = []
                for p in players[:10]:
                    p_name = _format_player_choice_label(p)
                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                text=p_name,
                                callback_data=f"player_select:{p['id']}",
                            )
                        ]
                    )
                    player_lines.append(
                        f"• {format_player_link(p)} — <b>{int(p.get('balance', 0) or 0)} ₽</b>"
                    )
                reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
                players_list = "\n".join(player_lines)
                await safe_reply(
                    message,
                    f"❓ Найдено несколько игроков ({len(players)}). Выберите:\n\n{players_list}",
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    action_name="reply to /player ambiguity",
                )
                return

        # 3. Без аргументов — список всех игроков (кратко)
        all_players = get_all_players()
        if not all_players:
            await safe_reply(
                message,
                "📋 В базе пока нет игроков.",
                action_name="reply to /player empty list",
            )
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
        await safe_reply(
            message,
            text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            action_name="reply to /player list",
        )

    @router.message(Command("ball_donate"))
    async def ball_donate_handler(message: Message) -> None:
        """Переключает флаг доната мяча у игрока (только для администратора)."""
        user = message.from_user
        if user is None:
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(bot, user, message.chat.id)
        if not is_admin:
            return

        if message.text is None:
            return

        args = message.text.split(maxsplit=1)
        target_user_id: int | None = None

        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            ensure_player(
                user_id=target_user.id,
                name=target_user.username,
                fullname=target_user.full_name,
            )
            target_user_id = target_user.id
        elif len(args) >= 2:
            search_query = args[1].strip()
            if not search_query:
                target_user_id = None
            elif search_query.isdigit():
                target_user_id = int(search_query)
            else:
                clean_query = search_query.lstrip("@")
                players = find_player_by_name(clean_query)
                if not players:
                    await safe_reply(
                        message,
                        f"❌ Игрок '{escape_html(search_query)}' не найден.",
                        parse_mode="HTML",
                        action_name="reply to /ball_donate missing player",
                    )
                    return
                if len(players) > 1:
                    matches = "\n".join(
                        f"• {format_player_link(player)}" for player in players[:10]
                    )
                    await safe_reply(
                        message,
                        "❌ Найдено несколько игроков. Уточните запрос:\n"
                        f"{matches}",
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                        action_name="reply to /ball_donate ambiguity",
                    )
                    return
                target_user_id = int(players[0]["id"])

        if target_user_id is None:
            await safe_reply(
                message,
                "ℹ️ <b>Переключение доната мяча</b>:\n\n"
                "1. Ответьте на сообщение игрока: <code>/ball_donate</code>\n"
                "2. Поиск по имени: <code>/ball_donate Иван</code>\n"
                "3. По @username: <code>/ball_donate @username</code>\n"
                "4. По ID игрока: <code>/ball_donate 12345678</code>",
                parse_mode="HTML",
                action_name="reply to /ball_donate usage",
            )
            return

        new_status = toggle_player_ball_donate(target_user_id)
        if new_status is None:
            await safe_reply(
                message,
                "❌ Не удалось изменить донат мяча. Убедитесь, что игрок есть в базе.",
                action_name="reply to /ball_donate toggle failure",
            )
            return

        player = get_player_info(target_user_id)
        player_link = format_player_link(player, target_user_id)
        status_text = "включён" if new_status else "выключен"
        ball_text = "да" if new_status else "нет"
        await safe_reply(
            message,
            f"✅ Для игрока {player_link} донат мяча {status_text}.\n"
            f"🏐 Донат: <b>{ball_text}</b>",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            action_name="reply to /ball_donate success",
        )

    @router.callback_query(
        lambda c: c.data and c.data.startswith("subs_add_select:")
    )
    async def process_subs_add_select(callback_query: CallbackQuery):
        """Обработка выбора игрока для /subs add."""
        user = callback_query.from_user
        if user is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка: нет информации о пользователе",
                show_alert=True,
                action_name="answer subs_add_select missing user",
            )
            return

        if callback_query.message is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка: сообщение не найдено",
                show_alert=True,
                action_name="answer subs_add_select missing message",
            )
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )
        if not is_admin:
            await safe_answer_callback(
                callback_query,
                text="❌ Нет прав для этого действия.",
                show_alert=True,
                action_name="answer subs_add_select forbidden",
            )
            return

        if callback_query.data is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка данных.",
                show_alert=True,
                action_name="answer subs_add_select missing data",
            )
            return

        result = _validate_subs_add_select_callback_data(callback_query.data)
        if result is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка формата данных.",
                show_alert=True,
                action_name="answer subs_add_select invalid format",
            )
            return

        poll_template_id, target_user_id = result
        result_text = _add_subscription_result_text(poll_template_id, target_user_id)
        await safe_edit_message_text(
            callback_query.message,
            result_text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            action_name="edit subs_add_select result",
        )
        await safe_answer_callback(
            callback_query,
            action_name="answer subs_add_select success",
        )

    @router.callback_query(lambda c: c.data and c.data.startswith("player_select:"))
    async def process_player_select(callback_query: CallbackQuery):
        """Обработка выбора игрока из списка для просмотра информации."""
        user = callback_query.from_user
        if user is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка: нет информации о пользователе",
                show_alert=True,
                action_name="answer player_select missing user",
            )
            return

        if callback_query.message is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка: сообщение не найдено",
                show_alert=True,
                action_name="answer player_select missing message",
            )
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )
        if not is_admin:
            await safe_answer_callback(
                callback_query,
                text="❌ Нет прав для этого действия.",
                show_alert=True,
                action_name="answer player_select forbidden",
            )
            return

        if callback_query.data is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка данных.",
                show_alert=True,
                action_name="answer player_select missing data",
            )
            return

        player_id = validate_player_select_callback_data(
            callback_query.data, "player_select"
        )
        if player_id is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка формата данных.",
                show_alert=True,
                action_name="answer player_select invalid format",
            )
            return

        p = get_player_info(player_id)
        if not p:
            await safe_answer_callback(
                callback_query,
                text="❌ Игрок не найден.",
                show_alert=True,
                action_name="answer player_select player missing",
            )
            return

        text = _format_player_detail(p)
        await safe_edit_message_text(
            callback_query.message,
            text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            action_name="edit player_select result",
        )
        await safe_answer_callback(
            callback_query,
            action_name="answer player_select success",
        )

    async def _process_balance_select(
        callback_query: CallbackQuery, update_fund: bool
    ) -> None:
        """Общая обработка выбора игрока из списка для изменения баланса."""
        user = callback_query.from_user
        if user is None:
            logging.error("❌ callback_query.from_user is None")
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка: нет информации о пользователе",
                show_alert=True,
                action_name="answer balance_select missing user",
            )
            return

        if callback_query.message is None:
            logging.error("❌ callback_query.message is None")
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка: сообщение не найдено",
                show_alert=True,
                action_name="answer balance_select missing message",
            )
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )

        if not is_admin:
            await safe_answer_callback(
                callback_query,
                text="❌ У вас нет прав для этого действия.",
                show_alert=True,
                action_name="answer balance_select forbidden",
            )
            return

        # Парсим callback_data: pay_select:player_id:amount
        if callback_query.data is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка данных.",
                show_alert=True,
                action_name="answer balance_select missing data",
            )
            return

        prefix = "pay_select" if update_fund else "restore_select"
        result = validate_balance_callback_data(callback_query.data, prefix)
        if result is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка формата данных.",
                show_alert=True,
                action_name="answer balance_select invalid format",
            )
            return

        target_user_id, amount = result

        admin_name = f"@{user.username}" if user.username else f"ID:{user.id}"
        description = (
            f"Оплата (admin: {admin_name})"
            if update_fund
            else f"Восстановление (admin: {admin_name})"
        )

        if update_fund:
            success = update_player_and_fund_balance_atomic(
                target_user_id, amount, description
            )
        else:
            success = update_player_and_transaction_atomic(
                target_user_id, amount, description
            )

        if success:
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

            await safe_edit_message_text(
                callback_query.message,
                result_text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                action_name="edit balance_select result",
            )

            await safe_answer_callback(
                callback_query,
                action_name="answer balance_select success",
            )
            action = "изменил" if update_fund else "восстановил"
            logging.info(
                f"💰 Админ @{user.username} (ID: {user.id}) {action} баланс через меню: "
                f"ID={target_user_id}, сумма={amount}"
            )
        else:
            await safe_answer_callback(
                callback_query,
                text="❌ Не удалось обновить баланс.",
                show_alert=True,
                action_name="answer balance_select update failure",
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
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка: нет информации о пользователе",
                show_alert=True,
                action_name="answer hall_pay missing user",
            )
            return

        if callback_query.message is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка: сообщение не найдено",
                show_alert=True,
                action_name="answer hall_pay missing message",
            )
            return

        admin_service: AdminService = dp.workflow_data["admin_service"]
        is_admin = await admin_service.is_admin(
            bot, user, callback_query.message.chat.id
        )

        if not is_admin:
            await safe_answer_callback(
                callback_query,
                text="❌ У вас нет прав для этого действия.",
                show_alert=True,
                action_name="answer hall_pay forbidden",
            )
            return

        if callback_query.data is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка данных.",
                show_alert=True,
                action_name="answer hall_pay missing data",
            )
            return

        # Парсим и валидируем callback_data: hall_pay:{poll_template_id}:{month}
        result = validate_hall_pay_callback_data(callback_query.data)
        if result is None:
            await safe_answer_callback(
                callback_query,
                text="❌ Ошибка формата данных.",
                show_alert=True,
                action_name="answer hall_pay invalid format",
            )
            return

        poll_template_id, month = result

        # Получаем стоимость зала
        poll_templates = get_poll_templates()
        hall = next((p for p in poll_templates if p["id"] == poll_template_id), None)
        if not hall:
            await safe_answer_callback(
                callback_query,
                text="❌ Зал не найден.",
                show_alert=True,
                action_name="answer hall_pay hall missing",
            )
            return
        poll_name = str(hall["name"])

        cost_per_game = int(hall.get("cost_per_game", 0) or 0)
        games_in_month = count_games_in_month(
            str(hall.get("game_day", "*") or "*"), month
        )
        monthly_rent = cost_per_game * games_in_month
        if monthly_rent <= 0:
            await safe_answer_callback(
                callback_query,
                text="❌ У этого зала нулевая стоимость.",
                show_alert=True,
                action_name="answer hall_pay zero cost",
            )
            return

        # Записываем оплату атомарно
        ensure_player(user_id=user.id, name=user.username, fullname=user.full_name)

        payment_result = record_hall_payment_atomic(
            user.id, poll_template_id, month, monthly_rent, poll_name
        )
        if payment_result == "duplicate":
            await safe_answer_callback(
                callback_query,
                text=f"⚠️ Зал '{poll_name}' за {month} уже оплачен.",
                show_alert=True,
                action_name="answer hall_pay already paid",
            )
            return
        if payment_result != "success":
            await safe_answer_callback(
                callback_query,
                text="❌ Не удалось оплатить зал. Попробуйте ещё раз.",
                show_alert=True,
                action_name="answer hall_pay payment failure",
            )
            return

        fund = get_fund_balance()
        place = str(hall.get("place", ""))
        hall_label = poll_name
        if place:
            hall_label += f" ({place})"

        result_text = (
            f"✅ <b>Зал оплачен</b>\n\n"
            f"🏟 {escape_html(hall_label)}\n"
            f"📅 Месяц: {month}\n"
            f"💸 Сумма: {cost_per_game} ₽ × {games_in_month} = {monthly_rent} ₽\n"
            f"🏦 Касса: <b>{fund} ₽</b>"
        )

        await safe_edit_message_text(
            callback_query.message,
            result_text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            action_name="edit hall_pay result",
        )

        await safe_answer_callback(
            callback_query,
            action_name="answer hall_pay success",
        )
        logging.info(
            f"🏟 Админ @{user.username} (ID: {user.id}) оплатил зал {poll_name} за {month}: {monthly_rent}₽ "
            f"(cost_per_game={cost_per_game}, games={games_in_month})"
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
        name = (
            f"@{user.username}"
            if user.username
            else (user.full_name or "Неизвестный")
        )
        voted_at = datetime.now(timezone.utc).isoformat()

        # Обновляем список голосующих
        yes_voters = poll_service.update_voters(
            poll_id=poll_id,
            user_id=user.id,
            user_name=name,
            update_id=update_id,
            voted_at=voted_at,
            voted_yes=voted_yes,
        )
        logging.debug(
            f"Обновленный список голосующих за опрос {poll_id}: {len(yes_voters)} чел."
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
