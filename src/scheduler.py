"""Планировщик задач для автоматической отправки опросов."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .db import clear_paid_poll_subscriptions, get_poll_templates
from .services import BotStateService, PollService
from .types import PollTemplate


def get_monthly_subscription_poll_params() -> (
    tuple[str, list[str], list[str | None]] | None
):
    """
    Формирует параметры опроса «Абонемент на следующий месяц».

    Returns:
        (вопрос, список опций, option_poll_names) или None, если нет платных залов.
    """
    from typing import cast

    poll_templates = get_poll_templates()
    paid_polls = [
        p for p in poll_templates
        if int(cast(int, p.get("cost") or 0)) > 0
    ]
    if not paid_polls:
        return None

    utc_tz = ZoneInfo("UTC")
    moscow_tz = ZoneInfo("Europe/Moscow")
    options: list[str] = []
    option_poll_names: list[str | None] = []

    for poll in paid_polls:
        name = str(poll.get("name", ""))
        place = str(poll.get("place", ""))
        game_hour_utc: int = cast(int, poll.get("game_hour_utc") or 0)
        game_minute_utc: int = cast(int, poll.get("game_minute_utc") or 0)
        dt_utc = datetime(
            2000, 1, 1, game_hour_utc, game_minute_utc, tzinfo=utc_tz
        )
        dt_moscow = dt_utc.astimezone(moscow_tz)
        time_moscow = dt_moscow.strftime("%H:%M")
        if place:
            options.append(f"{name} — {place} — {time_moscow} МСК")
        else:
            options.append(f"{name} — {time_moscow} МСК")
        option_poll_names.append(name)

    options.append("смотреть результат")
    option_poll_names.append(None)

    question = (
        "Абонемент на следующий месяц.\n"
        "Выберите игры, по которым хотите подписку. Можно выбрать несколько вариантов."
    )
    return (question, options, option_poll_names)


def create_poll_job(
    bot: Bot,
    message: str,
    poll_name: str,
    bot_state_service: BotStateService,
    poll_service: PollService,
    subs: list[int] | None = None,
    options: list[str] | None = None,
    allows_multiple_answers: bool = False,
    poll_kind: str = "regular",
    option_poll_names: list[str | None] | None = None,
) -> Callable[[], Awaitable[None]]:
    """
    Создаёт асинхронную задачу для отправки опроса.

    Args:
        bot: Экземпляр бота
        message: Текст опроса
        poll_name: Название опроса
        bot_state_service: Сервис состояния бота
        poll_service: Сервис опросов
        subs: Список ID подписчиков

    Returns:
        Асинхронная функция-задача для планировщика
    """

    async def job() -> None:
        chat_id: int = bot_state_service.get_chat_id()
        new_chat_id: int = await poll_service.send_poll(
            bot,
            chat_id,
            message,
            poll_name,
            bot_state_service.is_enabled(),
            subs,
            options,
            allows_multiple_answers,
            poll_kind,
            option_poll_names,
        )
        if new_chat_id != chat_id:
            bot_state_service.set_chat_id(new_chat_id)

    return job


def create_close_poll_job(
    bot: Bot, poll_name: str, poll_service: PollService
) -> Callable[[], Awaitable[None]]:
    """
    Создаёт асинхронную задачу для закрытия опроса.

    Args:
        bot: Экземпляр бота
        poll_name: Название опроса
        poll_service: Сервис опросов

    Returns:
        Асинхронная функция-задача для планировщика
    """

    async def job() -> None:
        await poll_service.close_poll(bot, poll_name)

    return job


def create_reminder_job(
    bot: Bot,
    message: str,
    bot_state_service: BotStateService,
    poll_service: PollService,
) -> Callable[[], Awaitable[None]]:
    """
    Создаёт асинхронную задачу для напоминания об окончании голосования.

    Args:
        bot: Экземпляр бота
        message: Текст напоминания
        bot_state_service: Сервис состояния бота
        poll_service: Сервис опросов

    Returns:
        Асинхронная функция-задача для планировщика
    """

    async def job() -> None:
        if not poll_service.has_active_polls():
            return
        first_poll = poll_service.get_first_poll()
        if first_poll is None:
            return
        _, data = first_poll
        if data.poll_kind != "monthly_subscription":
            return
        chat_id: int = bot_state_service.get_chat_id()
        await bot.send_message(chat_id=chat_id, text=message)

    return job


def setup_scheduler(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    bot_state_service: BotStateService,
    poll_service: PollService,
) -> None:
    """
    Настройка планировщика задач из базы данных.

    Args:
        scheduler: Экземпляр планировщика
        bot: Экземпляр бота
        bot_state_service: Сервис состояния бота
        poll_service: Сервис опросов
    """
    poll_templates = get_poll_templates()

    if not poll_templates:
        logging.warning("⚠️ Расписание опросов не найдено в базе данных.")
        return

    logging.info(f"⏰ Настройка планировщика ({len(poll_templates)} опросов):")

    for idx, poll_config in enumerate(poll_templates):
        poll_name: str = poll_config["name"]
        message: str = poll_config["message"]

        # Время открытия опроса
        open_day: str = poll_config.get("open_day", "*")
        open_hour_utc: int = poll_config.get("open_hour_utc", 0)
        open_minute_utc: int = poll_config.get("open_minute_utc", 0)

        # Время проведения игры
        game_day: str = poll_config.get("game_day", "*")
        game_hour_utc: int = poll_config.get("game_hour_utc", 0)
        game_minute_utc: int = poll_config.get("game_minute_utc", 0)

        # === Задача открытия опроса ===
        open_job_id: str = f"poll_open_{idx}"

        open_trigger_kwargs: dict[str, Any] = {
            "hour": open_hour_utc,
            "minute": open_minute_utc,
            "timezone": "UTC",
        }

        if open_day != "*":
            open_trigger_kwargs["day_of_week"] = open_day

        # Получаем список подписчиков для этого опроса
        subs: list[int] = poll_config.get("subs", [])

        poll_job: Callable[[], Awaitable[None]] = create_poll_job(
            bot, message, poll_name, bot_state_service, poll_service, subs
        )

        scheduler.add_job(
            poll_job,
            trigger=CronTrigger(**open_trigger_kwargs),
            id=open_job_id,
            name=f"{poll_name} (открытие)",
            replace_existing=True,
        )

        if open_day == "*":
            logging.info(
                f"  📅 ОТКРЫТИЕ: Ежедневно {open_hour_utc:02d}:{open_minute_utc:02d} UTC - {poll_name}"
            )
        else:
            logging.info(
                f"  📅 ОТКРЫТИЕ: {open_day.upper()} {open_hour_utc:02d}:{open_minute_utc:02d} UTC - {poll_name}"
            )

        if subs:
            logging.debug(f"     Подписчиков для '{poll_name}': {len(subs)}")

        # === Задача закрытия опроса ===
        close_job_id: str = f"poll_close_{idx}"

        # Закрываем опрос за 30 минут до игры
        total_close_minutes = (game_hour_utc * 60 + game_minute_utc - 30) % (24 * 60)
        close_hour_utc = total_close_minutes // 60
        close_minute_utc = total_close_minutes % 60
        current_close_day = game_day

        close_trigger_kwargs: dict[str, Any] = {
            "hour": close_hour_utc,
            "minute": close_minute_utc,
            "timezone": "UTC",
        }

        if game_day != "*":
            # Если время закрытия перешло на предыдущий день, корректируем день недели
            if game_hour_utc * 60 + game_minute_utc < 30:
                days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                try:
                    day_idx = days.index(game_day)
                    current_close_day = days[(day_idx - 1) % 7]
                except ValueError:
                    pass
            close_trigger_kwargs["day_of_week"] = current_close_day

        close_job: Callable[[], Awaitable[None]] = create_close_poll_job(
            bot, poll_name, poll_service
        )

        scheduler.add_job(
            close_job,
            trigger=CronTrigger(**close_trigger_kwargs),
            id=close_job_id,
            name=f"{poll_name} (закрытие)",
            replace_existing=True,
        )

        if game_day == "*":
            logging.info(
                f"  🔒 ЗАКРЫТИЕ: Ежедневно {close_hour_utc:02d}:{close_minute_utc:02d} UTC (игра в {game_hour_utc:02d}:{game_minute_utc:02d}) - {poll_name}"
            )
        else:
            logging.info(
                f"  🔒 ЗАКРЫТИЕ: {current_close_day.upper()} {close_hour_utc:02d}:{close_minute_utc:02d} UTC (игра {game_day.upper()} в {game_hour_utc:02d}:{game_minute_utc:02d}) - {poll_name}"
            )

    logging.info(f"✅ Планировщик настроен: {len(poll_templates) * 2} задач добавлено")

    _schedule_monthly_subscription_poll(
        scheduler, bot, bot_state_service, poll_service, poll_templates
    )


def _schedule_monthly_subscription_poll(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    bot_state_service: BotStateService,
    poll_service: PollService,
    poll_templates: list[PollTemplate],
) -> None:
    """
    Планирует ежемесячный опрос на абонемент для платных игр.
    """
    from typing import cast

    params = get_monthly_subscription_poll_params()
    if params is None:
        logging.info("ℹ️ Платные опросы не найдены, месячный опрос не запланирован")
        return

    _question, _options, _option_poll_names = params
    paid_polls = [
        p for p in poll_templates
        if int(cast(int, p.get("cost") or 0)) > 0
    ]

    moscow_tz = ZoneInfo("Europe/Moscow")
    utc_tz = ZoneInfo("UTC")
    now_moscow = datetime.now(tz=moscow_tz)

    def month_bounds(base: datetime) -> tuple[datetime, datetime]:
        month_start = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if base.month == 12:
            next_month = base.replace(year=base.year + 1, month=1, day=1)
        else:
            next_month = base.replace(month=base.month + 1, day=1)
        next_month = next_month.replace(hour=0, minute=0, second=0, microsecond=0)
        return month_start, next_month

    def last_game_in_month(
        month_start_moscow: datetime, next_month_start_moscow: datetime
    ) -> datetime | None:
        utc_start = month_start_moscow.astimezone(utc_tz)
        utc_end = next_month_start_moscow.astimezone(utc_tz)
        end_date = (utc_end - timedelta(seconds=1)).date()
        day_map = {
            "mon": 0,
            "tue": 1,
            "wed": 2,
            "thu": 3,
            "fri": 4,
            "sat": 5,
            "sun": 6,
        }
        last_game: datetime | None = None
        for poll in paid_polls:
            game_day = str(poll.get("game_day", "*"))
            game_hour_utc: int = cast(int, poll.get("game_hour_utc") or 0)
            game_minute_utc: int = cast(int, poll.get("game_minute_utc") or 0)
            current_date = utc_start.date()
            while current_date <= end_date:
                if game_day == "*" or day_map.get(game_day) == current_date.weekday():
                    dt_utc = datetime(
                        current_date.year,
                        current_date.month,
                        current_date.day,
                        game_hour_utc,
                        game_minute_utc,
                        tzinfo=utc_tz,
                    )
                    dt_moscow = dt_utc.astimezone(moscow_tz)
                    if month_start_moscow <= dt_moscow < next_month_start_moscow:
                        if last_game is None or dt_moscow > last_game:
                            last_game = dt_moscow
                current_date += timedelta(days=1)
        return last_game

    month_start, next_month_start = month_bounds(now_moscow)
    last_game = last_game_in_month(month_start, next_month_start)
    if last_game is None:
        logging.warning("⚠️ Не удалось найти последнюю игру месяца для платных опросов")
        return

    open_moscow = last_game.replace(hour=22, minute=0, second=0, microsecond=0)
    if open_moscow <= now_moscow:
        month_start, next_month_start = month_bounds(next_month_start)
        last_game = last_game_in_month(month_start, next_month_start)
        if last_game is None:
            logging.warning(
                "⚠️ Не удалось найти последнюю игру следующего месяца для платных опросов"
            )
            return
        open_moscow = last_game.replace(hour=22, minute=0, second=0, microsecond=0)

    clear_moscow = open_moscow - timedelta(minutes=1)
    reminder_moscow = (open_moscow + timedelta(days=1)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    close_moscow = open_moscow + timedelta(hours=24)

    def to_utc(dt_moscow: datetime) -> datetime:
        return dt_moscow.astimezone(utc_tz)

    poll_question, paid_poll_options, option_poll_names = _question, _options, _option_poll_names

    open_job_id = "monthly_subs_open"
    clear_job_id = "monthly_subs_clear"
    reminder_job_id = "monthly_subs_reminder"
    close_job_id = "monthly_subs_close"
    poll_name = "monthly_subscription"

    def clear_job():
        return clear_paid_poll_subscriptions()

    scheduler.add_job(
        clear_job,
        trigger=DateTrigger(run_date=to_utc(clear_moscow)),
        id=clear_job_id,
        name="Абонемент (очистка подписок)",
        replace_existing=True,
    )

    poll_job = create_poll_job(
        bot,
        poll_question,
        poll_name,
        bot_state_service,
        poll_service,
        subs=[],
        options=paid_poll_options,
        allows_multiple_answers=True,
        poll_kind="monthly_subscription",
        option_poll_names=option_poll_names,
    )
    scheduler.add_job(
        poll_job,
        trigger=DateTrigger(run_date=to_utc(open_moscow)),
        id=open_job_id,
        name="Абонемент (открытие)",
        replace_existing=True,
    )

    reminder_text = (
        "⏰ Напоминание: голосование за абонемент заканчивается сегодня в 22:00 МСК."
    )
    reminder_job = create_reminder_job(
        bot, reminder_text, bot_state_service, poll_service
    )
    scheduler.add_job(
        reminder_job,
        trigger=DateTrigger(run_date=to_utc(reminder_moscow)),
        id=reminder_job_id,
        name="Абонемент (напоминание)",
        replace_existing=True,
    )

    close_job = create_close_poll_job(bot, poll_name, poll_service)
    scheduler.add_job(
        close_job,
        trigger=DateTrigger(run_date=to_utc(close_moscow)),
        id=close_job_id,
        name="Абонемент (закрытие)",
        replace_existing=True,
    )

    logging.info(
        "📆 Месячный опрос на абонемент запланирован: "
        f"очистка={clear_moscow}, открытие={open_moscow}, "
        f"напоминание={reminder_moscow}, закрытие={close_moscow}"
    )
