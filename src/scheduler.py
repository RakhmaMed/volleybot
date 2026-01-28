"""Планировщик задач для автоматической отправки опросов."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import get_poll_templates
from .services import BotStateService, PollService


def create_poll_job(
    bot: Bot,
    message: str,
    poll_name: str,
    bot_state_service: BotStateService,
    poll_service: PollService,
    subs: list[int] | None = None,
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
            bot, chat_id, message, poll_name, bot_state_service.is_enabled(), subs
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
