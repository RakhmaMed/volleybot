"""Планировщик задач для автоматической отправки опросов."""

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import POLLS_SCHEDULE
from poll import send_poll


def create_poll_job(bot: Bot, message: str, poll_name: str, get_chat_id, set_chat_id, get_bot_enabled):
    """
    Создаёт асинхронную задачу для отправки опроса.
    
    Args:
        bot: Экземпляр бота
        message: Текст опроса
        poll_name: Название опроса
        get_chat_id: Функция получения текущего chat_id
        set_chat_id: Функция установки chat_id
        get_bot_enabled: Функция получения состояния бота
    """
    async def job():
        chat_id = get_chat_id()
        new_chat_id = await send_poll(bot, chat_id, message, poll_name, get_bot_enabled())
        if new_chat_id != chat_id:
            set_chat_id(new_chat_id)
    return job


def setup_scheduler(scheduler: AsyncIOScheduler, bot: Bot, get_chat_id, set_chat_id, get_bot_enabled):
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
        poll_name = poll_config.get("name", f"Опрос #{idx + 1}")
        message = poll_config.get("message", "")
        day_of_week = poll_config.get("day_of_week", "*")
        hour_utc = poll_config.get("hour_utc", 0)
        minute_utc = poll_config.get("minute_utc", 0)
        
        if not message:
            logging.warning(f"Пропущен опрос '{poll_name}': отсутствует текст сообщения")
            continue
        
        job_id = f"poll_{idx}"
        
        trigger_kwargs = {
            'hour': hour_utc,
            'minute': minute_utc,
            'timezone': 'UTC'
        }
        
        if day_of_week != "*":
            trigger_kwargs['day_of_week'] = day_of_week
        
        poll_job = create_poll_job(bot, message, poll_name, get_chat_id, set_chat_id, get_bot_enabled)
        
        scheduler.add_job(
            poll_job,
            trigger=CronTrigger(**trigger_kwargs),
            id=job_id,
            name=poll_name,
            replace_existing=True
        )
        
        if day_of_week == "*":
            logging.info(f"  - Ежедневно {hour_utc:02d}:{minute_utc:02d} UTC - {poll_name}")
        else:
            logging.info(f"  - {day_of_week.upper()} {hour_utc:02d}:{minute_utc:02d} UTC - {poll_name}")
