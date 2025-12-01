"""Планировщик задач для автоматической отправки опросов."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import POLLS_SCHEDULE
from .poll import send_poll, close_poll


def create_poll_job(
    bot: Bot,
    message: str,
    poll_name: str,
    get_chat_id: Callable[[], int],
    set_chat_id: Callable[[int], None],
    get_bot_enabled: Callable[[], bool]
) -> Callable[[], Awaitable[None]]:
    """
    Создаёт асинхронную задачу для отправки опроса.
    
    Args:
        bot: Экземпляр бота
        message: Текст опроса
        poll_name: Название опроса
        get_chat_id: Функция получения текущего chat_id
        set_chat_id: Функция установки chat_id
        get_bot_enabled: Функция получения состояния бота
        
    Returns:
        Асинхронная функция-задача для планировщика
    """
    async def job() -> None:
        chat_id: int = get_chat_id()
        new_chat_id: int = await send_poll(bot, chat_id, message, poll_name, get_bot_enabled())
        if new_chat_id != chat_id:
            set_chat_id(new_chat_id)
    return job


def create_close_poll_job(
    bot: Bot,
    poll_name: str,
    get_chat_id: Callable[[], int]
) -> Callable[[], Awaitable[None]]:
    """
    Создаёт асинхронную задачу для закрытия опроса.
    
    Args:
        bot: Экземпляр бота
        poll_name: Название опроса
        get_chat_id: Функция получения текущего chat_id
        
    Returns:
        Асинхронная функция-задача для планировщика
    """
    async def job() -> None:
        await close_poll(bot, poll_name, get_chat_id)
    return job


def setup_scheduler(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    get_chat_id: Callable[[], int],
    set_chat_id: Callable[[int], None],
    get_bot_enabled: Callable[[], bool]
) -> None:
    """
    Настройка планировщика задач из конфигурации.
    
    Args:
        scheduler: Экземпляр планировщика
        bot: Экземпляр бота
        get_chat_id: Функция получения текущего chat_id
        set_chat_id: Функция установки chat_id
        get_bot_enabled: Функция получения состояния бота
    """
    if not POLLS_SCHEDULE:
        logging.warning("Расписание опросов не найдено в config.json")
        return
    
    logging.info("Настройка планировщика:")
    
    for idx, poll_config in enumerate(POLLS_SCHEDULE):
        poll_name: str = poll_config.get("name", f"Опрос #{idx + 1}")
        message: str = poll_config.get("message", "")
        
        # Время открытия опроса
        open_day: str = poll_config.get("open_day", "*")
        open_hour_utc: int = poll_config.get("open_hour_utc", 0)
        open_minute_utc: int = poll_config.get("open_minute_utc", 0)
        
        # Время закрытия опроса
        close_day: str = poll_config.get("close_day", "*")
        close_hour_utc: int = poll_config.get("close_hour_utc", 0)
        close_minute_utc: int = poll_config.get("close_minute_utc", 0)
        
        if not message:
            logging.warning(f"Пропущен опрос '{poll_name}': отсутствует текст сообщения")
            continue
        
        # === Задача открытия опроса ===
        open_job_id: str = f"poll_open_{idx}"
        
        open_trigger_kwargs: dict[str, Any] = {
            'hour': open_hour_utc,
            'minute': open_minute_utc,
            'timezone': 'UTC'
        }
        
        if open_day != "*":
            open_trigger_kwargs['day_of_week'] = open_day
        
        poll_job: Callable[[], Awaitable[None]] = create_poll_job(
            bot, message, poll_name, get_chat_id, set_chat_id, get_bot_enabled
        )
        
        scheduler.add_job(
            poll_job,
            trigger=CronTrigger(**open_trigger_kwargs),
            id=open_job_id,
            name=f"{poll_name} (открытие)",
            replace_existing=True
        )
        
        if open_day == "*":
            logging.info(f"  - ОТКРЫТИЕ: Ежедневно {open_hour_utc:02d}:{open_minute_utc:02d} UTC - {poll_name}")
        else:
            logging.info(f"  - ОТКРЫТИЕ: {open_day.upper()} {open_hour_utc:02d}:{open_minute_utc:02d} UTC - {poll_name}")
        
        # === Задача закрытия опроса ===
        close_job_id: str = f"poll_close_{idx}"
        
        close_trigger_kwargs: dict[str, Any] = {
            'hour': close_hour_utc,
            'minute': close_minute_utc,
            'timezone': 'UTC'
        }
        
        if close_day != "*":
            close_trigger_kwargs['day_of_week'] = close_day
        
        close_job: Callable[[], Awaitable[None]] = create_close_poll_job(bot, poll_name, get_chat_id)
        
        scheduler.add_job(
            close_job,
            trigger=CronTrigger(**close_trigger_kwargs),
            id=close_job_id,
            name=f"{poll_name} (закрытие)",
            replace_existing=True
        )
        
        if close_day == "*":
            logging.info(f"  - ЗАКРЫТИЕ: Ежедневно {close_hour_utc:02d}:{close_minute_utc:02d} UTC - {poll_name}")
        else:
            logging.info(f"  - ЗАКРЫТИЕ: {close_day.upper()} {close_hour_utc:02d}:{close_minute_utc:02d} UTC - {poll_name}")
