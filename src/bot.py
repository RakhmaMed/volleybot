"""
Telegram-бот для организации опросов.
Точка входа приложения.

Требования: Python 3.13+, aiogram 3.x
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from aiohttp.typedefs import Handler
from aiohttp.web import Request, StreamResponse, middleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import (
    CHAT_ID,
    LOG_FORMAT,
    LOG_LEVEL,
    SCHEDULER_TIMEZONE,
    TOKEN,
    TRUST_PROXY,
    WEBHOOK_HOST,
    WEBHOOK_PATH,
    WEBHOOK_PORT,
    WEBHOOK_SECRET,
    WEBHOOK_URL,
)
from .db import cleanup_old_backups, create_backup, init_db
from .handlers import register_handlers, setup_bot_commands
from .scheduler import setup_scheduler
from .services import AdminService, BotStateService, PollService
from .utils import (
    generate_webhook_secret_path,
    is_telegram_ip,
    retry_async,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT,
)


async def on_startup(
    bot: Bot,
    scheduler: AsyncIOScheduler,
    bot_state_service: BotStateService,
    poll_service: PollService,
    effective_webhook_path: str | None = None,
) -> None:
    """Выполняется при запуске бота."""
    logging.info("Инициализация бота...")
    cleanup_old_backups()
    create_backup("startup")

    # Загружаем список игроков один раз при старте
    poll_service.load_persisted_state()
    logging.debug(
        f"Активных опросов после восстановления: {len(poll_service.get_all_polls())}"
    )

    # Устанавливаем команды бота
    await setup_bot_commands(bot)

    setup_scheduler(scheduler, bot, bot_state_service, poll_service)
    scheduler.start()
    logging.info("Планировщик запущен")

    if effective_webhook_path:
        try:
            # Формируем URL с правильным путём
            webhook_path = effective_webhook_path
            if WEBHOOK_URL:
                # Заменяем путь в URL на эффективный
                from urllib.parse import urlparse, urlunparse

                parsed = urlparse(WEBHOOK_URL)
                effective_url = urlunparse(parsed._replace(path=webhook_path))
            else:
                effective_url = f"{WEBHOOK_HOST}{webhook_path}"

            logging.debug(f"Попытка установки webhook на URL: {effective_url}")

            # Устанавливаем webhook с секретным токеном если настроен
            @retry_async(
                (TelegramAPIError, TelegramNetworkError, asyncio.TimeoutError, OSError),
                tries=None,
                delay=2,
                backoff=2.0,
                max_delay=60.0,
            )
            async def set_webhook_with_retry():
                # ВАЖНО: Явно указываем все типы updates, которые должен получать бот
                #
                # Проблема: Если параметр allowed_updates не указан при вызове set_webhook(),
                # Telegram API сохраняет список из ПРЕДЫДУЩЕЙ установки webhook.
                # Это может привести к тому, что некоторые типы updates (например, callback_query)
                # не будут отправляться на webhook, даже если обработчики для них зарегистрированы.
                #
                # Симптомы:
                # - Команды работают (/start, /pay и т.д.)
                # - Inline кнопки создаются и отображаются
                # - При нажатии на кнопку ничего не происходит
                # - В логах НЕ появляется "Incoming update" с типом callback_query
                #
                # Решение: Всегда явно указывать allowed_updates при установке webhook
                allowed_updates = [
                    "message",  # Обычные текстовые сообщения и команды
                    "callback_query",  # Нажатия на inline кнопки (КРИТИЧНО!)
                    "poll_answer",  # Ответы пользователей на опросы
                ]

                if WEBHOOK_SECRET:
                    await bot.set_webhook(
                        effective_url,
                        secret_token=WEBHOOK_SECRET,
                        allowed_updates=allowed_updates,
                    )
                    logging.info(
                        f"✅ Webhook успешно установлен: {effective_url} (с секретным токеном)"
                    )
                else:
                    await bot.set_webhook(
                        effective_url, allowed_updates=allowed_updates
                    )
                    logging.info(f"✅ Webhook успешно установлен: {effective_url}")

                logging.info(
                    f"📋 Разрешенные типы updates: {', '.join(allowed_updates)}"
                )

            await set_webhook_with_retry()
        except (TelegramAPIError, TelegramNetworkError, asyncio.TimeoutError, OSError):
            logging.exception("❌ Не удалось установить webhook")
    else:
        logging.info("Режим polling активен")


async def on_shutdown(
    bot: Bot,
    scheduler: AsyncIOScheduler,
    bot_state_service: BotStateService,
    poll_service: PollService,
    is_webhook: bool = False,
) -> None:
    """Выполняется при остановке бота."""
    logging.info("🛑 Начало процедуры остановки бота...")
    create_backup("shutdown")

    if scheduler.running:
        logging.debug("Остановка планировщика...")
        scheduler.shutdown()
        logging.info("✅ Планировщик остановлен")

    if is_webhook:
        logging.debug("Удаление webhook...")

        @retry_async(
            (TelegramAPIError, TelegramNetworkError, asyncio.TimeoutError, OSError),
            tries=3,
            delay=2,
        )
        async def delete_webhook_with_retry():
            await bot.delete_webhook()

        try:
            await delete_webhook_with_retry()
            logging.info("✅ Webhook удален")
        except (TelegramAPIError, TelegramNetworkError, asyncio.TimeoutError, OSError):
            logging.warning("⚠️ Не удалось удалить webhook при выключении")

    logging.debug("Закрытие сессии бота...")
    await bot.session.close()

    logging.debug("Сохранение состояния сервисов...")
    poll_service.persist_state()
    bot_state_service.persist_state()
    logging.info("✅ Бот успешно остановлен")


async def run_polling() -> None:
    """Запуск в режиме polling."""
    # Инициализация БД
    init_db()

    # Инициализация сервисов
    admin_service = AdminService(default_chat_id=CHAT_ID)
    bot_state_service = BotStateService(default_chat_id=CHAT_ID)
    poll_service = PollService()

    # Инициализация бота и диспетчера
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Сохраняем сервисы в workflow_data для доступа из handlers
    dp.workflow_data.update(
        {
            "admin_service": admin_service,
            "bot_state_service": bot_state_service,
            "poll_service": poll_service,
        }
    )

    # Планировщик задач
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)

    # Регистрация обработчиков
    register_handlers(dp, bot)

    # Регистрация startup/shutdown
    async def startup_handler():
        await on_startup(bot, scheduler, bot_state_service, poll_service)

    async def shutdown_handler():
        await on_shutdown(
            bot, scheduler, bot_state_service, poll_service, is_webhook=False
        )

    dp.startup.register(startup_handler)
    dp.shutdown.register(shutdown_handler)

    logging.info("🚀 Запуск бота в режиме polling...")
    await dp.start_polling(bot)


def run_webhook() -> None:
    """Запуск в режиме webhook."""
    logging.info("🚀 Запуск бота в режиме webhook")
    logging.debug(
        f"Webhook настройки: Host={WEBHOOK_HOST}, Port={WEBHOOK_PORT}, Path={WEBHOOK_PATH}"
    )

    # Инициализация БД
    init_db()

    # Инициализация сервисов
    admin_service = AdminService(default_chat_id=CHAT_ID)
    bot_state_service = BotStateService(default_chat_id=CHAT_ID)
    poll_service = PollService()

    # Инициализация бота и диспетчера
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Сохраняем сервисы в workflow_data для доступа из handlers
    dp.workflow_data.update(
        {
            "admin_service": admin_service,
            "bot_state_service": bot_state_service,
            "poll_service": poll_service,
        }
    )

    # Планировщик задач
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)

    # Регистрация обработчиков
    register_handlers(dp, bot)

    # Определяем путь webhook (секретный если не указан явно)
    effective_webhook_path = (
        WEBHOOK_PATH if WEBHOOK_PATH else generate_webhook_secret_path(TOKEN)
    )

    # Регистрируем startup/shutdown
    async def startup_handler():
        await on_startup(
            bot, scheduler, bot_state_service, poll_service, effective_webhook_path
        )

    async def shutdown_handler():
        await on_shutdown(
            bot, scheduler, bot_state_service, poll_service, is_webhook=True
        )

    dp.startup.register(startup_handler)
    dp.shutdown.register(shutdown_handler)

    # Создаём middleware для проверки безопасности webhook
    @middleware
    async def security_middleware(
        request: Request,
        handler: Handler,
    ) -> StreamResponse:
        """Middleware для проверки безопасности входящих webhook запросов."""
        # Получаем реальный IP
        # Если TRUST_PROXY=True, доверяем заголовку X-Forwarded-For (от reverse proxy)
        # Если TRUST_PROXY=False, используем только прямой IP подключения
        if TRUST_PROXY:
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                client_ip = forwarded_for.split(",")[0].strip()
            else:
                client_ip = request.remote if request.remote else "unknown"
        else:
            client_ip = request.remote if request.remote else "unknown"

        # Проверяем IP только для webhook пути (используем effective_webhook_path через замыкание)
        if request.path == effective_webhook_path:
            # Проверяем что запрос от Telegram
            if client_ip != "unknown" and not is_telegram_ip(client_ip):
                logging.warning(
                    f"🚫 Отклонен webhook запрос от не-Telegram IP: {client_ip}"
                )
                return web.Response(status=403, text="Forbidden")

            # Проверяем секретный токен если настроен
            if WEBHOOK_SECRET:
                request_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
                if request_secret != WEBHOOK_SECRET:
                    logging.warning(
                        f"🚫 Отклонен webhook запрос с неверным секретным токеном от {client_ip}"
                    )
                    return web.Response(status=403, text="Forbidden")

        return await handler(request)

    # Создаём aiohttp приложение с middleware безопасности
    app: web.Application = web.Application(middlewares=[security_middleware])

    # Настраиваем webhook handler
    webhook_handler: SimpleRequestHandler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        handle_in_background=True,
    )
    webhook_handler.register(app, path=effective_webhook_path)

    logging.info(f"🔐 Webhook path: {effective_webhook_path}")
    logging.info(f"🛡️ Trust Proxy: {'ENABLED' if TRUST_PROXY else 'DISABLED'}")
    if WEBHOOK_SECRET:
        logging.info("🔐 Webhook secret token verification: ENABLED")
    else:
        logging.warning(
            "⚠️ БЕЗОПАСНОСТЬ: WEBHOOK_SECRET не настроен. "
            "Рекомендуется добавить WEBHOOK_SECRET в .env"
        )

    # Настраиваем приложение с диспетчером
    setup_application(app, dp, bot=bot)

    # Запускаем сервер
    logging.info(f"🌐 Запуск веб-сервера на порту {WEBHOOK_PORT}...")
    web.run_app(app, host="0.0.0.0", port=WEBHOOK_PORT)


if __name__ == "__main__":
    if WEBHOOK_HOST:
        run_webhook()
    else:
        asyncio.run(run_polling())
