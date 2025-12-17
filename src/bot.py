"""
Telegram-бот для организации опросов.
Точка входа приложения.

Требования: Python 3.13+, aiogram 3.x
"""

from __future__ import annotations

import asyncio
import logging
import ssl

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import (
    CHAT_ID,
    TOKEN,
    WEBHOOK_HOST,
    WEBHOOK_PATH,
    WEBHOOK_PORT,
    WEBHOOK_SSL_CERT,
    WEBHOOK_SSL_PRIV,
    WEBHOOK_URL,
)
from .db import init_db
from .handlers import register_handlers
from .scheduler import setup_scheduler
from .services import BotStateService, PollService
from .utils import load_players

logging.basicConfig(level=logging.INFO)


async def on_startup(
    bot: Bot,
    scheduler: AsyncIOScheduler,
    bot_state_service: BotStateService,
    poll_service: PollService,
) -> None:
    """Выполняется при запуске бота."""
    # Загружаем список игроков один раз при старте
    load_players()
    poll_service.load_persisted_state()

    setup_scheduler(scheduler, bot, bot_state_service, poll_service)
    scheduler.start()
    logging.info("Планировщик запущен")

    if WEBHOOK_HOST:
        try:
            await bot.set_webhook(WEBHOOK_URL)
            logging.info(f"Webhook установлен: {WEBHOOK_URL}")
        except Exception as e:
            logging.error(f"Ошибка установки webhook: {e}")
    else:
        logging.info("Режим polling активен")


async def on_shutdown(
    bot: Bot,
    scheduler: AsyncIOScheduler,
    bot_state_service: BotStateService,
    poll_service: PollService,
) -> None:
    """Выполняется при остановке бота."""
    logging.info("Остановка бота...")

    if scheduler.running:
        scheduler.shutdown()
        logging.info("Планировщик остановлен")

    if WEBHOOK_HOST:
        await bot.delete_webhook()
        logging.info("Webhook удален")

    await bot.session.close()
    poll_service.persist_state()
    bot_state_service.persist_state()


async def run_polling() -> None:
    """Запуск в режиме polling."""
    # Инициализация БД
    init_db()

    # Инициализация сервисов
    bot_state_service = BotStateService(default_chat_id=CHAT_ID)
    poll_service = PollService()

    # Инициализация бота и диспетчера
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Сохраняем сервисы в workflow_data для доступа из handlers
    dp.workflow_data.update(
        {
            "bot_state_service": bot_state_service,
            "poll_service": poll_service,
        }
    )

    # Планировщик задач
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Регистрация обработчиков
    register_handlers(dp, bot)

    # Регистрация startup/shutdown
    async def startup_handler():
        await on_startup(bot, scheduler, bot_state_service, poll_service)

    async def shutdown_handler():
        await on_shutdown(bot, scheduler, bot_state_service, poll_service)

    dp.startup.register(startup_handler)
    dp.shutdown.register(shutdown_handler)

    logging.info("Запуск бота в режиме polling")
    await dp.start_polling(bot)


def run_webhook() -> None:
    """Запуск в режиме webhook."""
    logging.info("Запуск бота в режиме webhook")

    # Инициализация БД
    init_db()

    # Инициализация сервисов
    bot_state_service = BotStateService(default_chat_id=CHAT_ID)
    poll_service = PollService()

    # Инициализация бота и диспетчера
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Сохраняем сервисы в workflow_data для доступа из handlers
    dp.workflow_data.update(
        {
            "bot_state_service": bot_state_service,
            "poll_service": poll_service,
        }
    )

    # Планировщик задач
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Настройка SSL
    ssl_context: ssl.SSLContext | None = None
    try:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(WEBHOOK_SSL_CERT, WEBHOOK_SSL_PRIV)
        logging.info("SSL сертификаты загружены")
    except FileNotFoundError as e:
        logging.error(f"Не удалось загрузить SSL сертификаты: {e}")
        exit(1)
    except Exception as e:
        logging.error(f"Ошибка при загрузке SSL сертификатов: {e}")
        exit(1)

    # Регистрация обработчиков
    register_handlers(dp, bot)

    # Регистрируем startup/shutdown
    async def startup_handler():
        await on_startup(bot, scheduler, bot_state_service, poll_service)

    async def shutdown_handler():
        await on_shutdown(bot, scheduler, bot_state_service, poll_service)

    dp.startup.register(startup_handler)
    dp.shutdown.register(shutdown_handler)

    # Создаём aiohttp приложение
    app: web.Application = web.Application()

    # Настраиваем webhook handler
    webhook_handler: SimpleRequestHandler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    # Настраиваем приложение с диспетчером
    setup_application(app, dp, bot=bot)

    # Запускаем сервер
    web.run_app(app, host="0.0.0.0", port=WEBHOOK_PORT, ssl_context=ssl_context)


if __name__ == "__main__":
    if WEBHOOK_HOST:
        run_webhook()
    else:
        asyncio.run(run_polling())
