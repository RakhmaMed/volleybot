"""
Telegram-бот для организации опросов.
Точка входа приложения.

Требования: Python 3.12+, aiogram 3.x
"""

import asyncio
import logging
import ssl
from typing import TypedDict

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import (
    TOKEN,
    CHAT_ID,
    WEBHOOK_HOST,
    WEBHOOK_PATH,
    WEBHOOK_PORT,
    WEBHOOK_URL,
    WEBHOOK_SSL_CERT,
    WEBHOOK_SSL_PRIV,
)
from .db import BOT_STATE_KEY, init_db, load_state, save_state
from .handlers import register_handlers
from .poll import load_persisted_poll_state, persist_poll_state
from .scheduler import setup_scheduler
from .utils import load_players

logging.basicConfig(level=logging.INFO)


class BotState(TypedDict):
    """Типизированное состояние бота."""
    bot_enabled: bool
    chat_id: int


# Инициализация бота и диспетчера
bot: Bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp: Dispatcher = Dispatcher()

# Глобальное состояние
_state: BotState = {
    'bot_enabled': True,
    'chat_id': CHAT_ID,
}

# Инициализация БД и восстановление состояния после рестарта
init_db()


def _restore_bot_state() -> None:
    """Подтягивает сохранённые значения bot_enabled/chat_id из БД."""
    stored_state = load_state(BOT_STATE_KEY, default={})
    if isinstance(stored_state, dict):
        _state['bot_enabled'] = bool(stored_state.get('bot_enabled', _state['bot_enabled']))
        try:
            _state['chat_id'] = int(stored_state.get('chat_id', _state['chat_id']))
        except (TypeError, ValueError):
            logging.warning("Сохранённый chat_id повреждён, оставляем значение из config.json")


def _persist_bot_state() -> None:
    """Фиксирует текущее состояние бота в БД."""
    save_state(BOT_STATE_KEY, {
        'bot_enabled': _state['bot_enabled'],
        'chat_id': _state['chat_id'],
    })


_restore_bot_state()
_persist_bot_state()

# Планировщик задач
scheduler: AsyncIOScheduler = AsyncIOScheduler(timezone='UTC')


# Функции доступа к состоянию
def get_bot_enabled() -> bool:
    """Возвращает состояние включения бота."""
    return _state['bot_enabled']


def set_bot_enabled(value: bool) -> None:
    """Устанавливает состояние включения бота."""
    _state['bot_enabled'] = value
    _persist_bot_state()


def get_chat_id() -> int:
    """Возвращает ID текущего чата."""
    return _state['chat_id']


def set_chat_id(value: int) -> None:
    """Устанавливает ID текущего чата."""
    _state['chat_id'] = value
    _persist_bot_state()


# Регистрация обработчиков
register_handlers(dp, bot, get_bot_enabled, set_bot_enabled)


async def on_startup(bot: Bot) -> None:
    """Выполняется при запуске бота."""
    # Загружаем список игроков один раз при старте
    load_players()
    load_persisted_poll_state()

    setup_scheduler(scheduler, bot, get_chat_id, set_chat_id, get_bot_enabled)
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


async def on_shutdown(bot: Bot) -> None:
    """Выполняется при остановке бота."""
    logging.info("Остановка бота...")
    
    if scheduler.running:
        scheduler.shutdown()
        logging.info("Планировщик остановлен")
    
    if WEBHOOK_HOST:
        await bot.delete_webhook()
        logging.info("Webhook удален")
    
    await bot.session.close()
    persist_poll_state()
    _persist_bot_state()


async def run_polling() -> None:
    """Запуск в режиме polling."""
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    logging.info("Запуск бота в режиме polling")
    await dp.start_polling(bot)


def run_webhook() -> None:
    """Запуск в режиме webhook."""
    logging.info("Запуск бота в режиме webhook")
    
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
    
    # Регистрируем startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Создаём aiohttp приложение
    app: web.Application = web.Application()
    
    # Настраиваем webhook handler
    webhook_handler: SimpleRequestHandler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    
    # Настраиваем приложение с диспетчером
    setup_application(app, dp, bot=bot)
    
    # Запускаем сервер
    web.run_app(
        app,
        host="0.0.0.0",
        port=WEBHOOK_PORT,
        ssl_context=ssl_context
    )


if __name__ == "__main__":
    if WEBHOOK_HOST:
        run_webhook()
    else:
        asyncio.run(run_polling())
