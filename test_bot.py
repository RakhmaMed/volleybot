#!/usr/bin/env python3
"""
Скрипт для локального тестирования бота.

Использование:
    python test_bot.py

Особенности:
- Работает с временной БД в памяти (не трогает основную БД)
- Можно тестировать в любом чате (не только в основном CHAT_ID)
- Автоматически создает тестовый опрос
- Позволяет использовать все команды бота
"""

import asyncio
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


async def main():
    """Запуск бота в тестовом режиме."""
    # Проверяем наличие .env файла
    if not os.path.exists(".env"):
        logger.error("❌ Файл .env не найден!")
        logger.info("Создайте .env файл с необходимыми настройками")
        return

    # Создаем временную БД для тестирования
    temp_db = tempfile.NamedTemporaryFile(
        mode="w", suffix=".db", delete=False, prefix="test_volleybot_"
    )
    temp_db_path = temp_db.name
    temp_db.close()

    logger.info(f"🧪 Тестовый режим")
    logger.info(f"📁 Временная БД: {temp_db_path}")
    logger.info(
        "⚠️  Все изменения будут утеряны после завершения (БД удалится автоматически)"
    )

    # Подменяем путь к БД на временный
    original_db_path = os.environ.get("VOLLEYBOT_DB_PATH", "")
    os.environ["VOLLEYBOT_DB_PATH"] = temp_db_path

    try:
        # Импортируем модули ПОСЛЕ установки переменной окружения
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from src.config import CHAT_ID, TOKEN
        from src.db import init_db
        from src.handlers import register_handlers, setup_bot_commands
        from src.scheduler import setup_scheduler
        from src.services import AdminService, BotStateService, PollService
        from src.utils import load_players

        logger.info(f"🔑 Используется токен бота: {TOKEN[:10]}...")
        logger.info(f"💬 Основной чат ID: {CHAT_ID}")

        # Инициализация БД
        logger.info("🗄️  Инициализация временной БД...")
        init_db()

        # Добавляем тестовый опрос в БД
        logger.info("➕ Создание тестового опроса...")
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()

        # Вычисляем время открытия (через 2 минуты) и игры (через 1 час)
        now = datetime.now()
        open_time = now + timedelta(minutes=2)
        game_time = now + timedelta(hours=1)

        cursor.execute(
            """
            INSERT INTO polls (name, place, message, open_day, open_hour_utc, open_minute_utc,
                             game_day, game_hour_utc, game_minute_utc, subs, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                "Тестовый опрос",
                "Тестовая площадка",
                "🧪 Это тестовый опрос для проверки бота",
                "*",  # Каждый день
                open_time.hour,
                open_time.minute,
                "*",  # Каждый день
                game_time.hour,
                game_time.minute,
                "[]",  # Пустой список замен
                1,  # Включен
            ),
        )
        conn.commit()
        poll_id = cursor.lastrowid
        conn.close()

        logger.info(f"✅ Тестовый опрос создан (ID: {poll_id})")
        logger.info(
            f"   📅 Откроется в {open_time.strftime('%H:%M')}, игра в {game_time.strftime('%H:%M')}"
        )

        # Инициализация сервисов
        admin_service = AdminService(default_chat_id=CHAT_ID)
        bot_state_service = BotStateService(default_chat_id=CHAT_ID)
        poll_service = PollService()

        # Загружаем игроков
        load_players()

        # Инициализация бота и диспетчера
        bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher()

        # Сохраняем сервисы в workflow_data
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        # Планировщик задач (для автоматического открытия опросов)
        scheduler = AsyncIOScheduler(timezone="UTC")

        # Регистрация обработчиков
        register_handlers(dp, bot)

        # Устанавливаем команды бота
        await setup_bot_commands(bot)

        # Загружаем сохраненное состояние
        poll_service.load_persisted_state()

        # Настраиваем планировщик
        setup_scheduler(scheduler, bot, bot_state_service, poll_service)
        scheduler.start()
        logger.info("⏰ Планировщик запущен")

        # Startup сообщение
        logger.info("\n" + "=" * 60)
        logger.info("🚀 БОТ ЗАПУЩЕН В ТЕСТОВОМ РЕЖИМЕ")
        logger.info("=" * 60)
        logger.info("\n📋 Доступные команды для тестирования:")
        logger.info("   /start - Приветственное сообщение")
        logger.info("   /list - Список всех опросов")
        logger.info("   /open <poll_id> - Открыть опрос вручную")
        logger.info("   /close <poll_id> - Закрыть опрос")
        logger.info("   /results <poll_id> - Показать результаты")
        logger.info("   /players - Список зарегистрированных игроков")
        logger.info("   /pay - Показать QR-код для оплаты")
        logger.info("\n💡 Советы:")
        logger.info("   - Можете писать боту из любого чата (не обязательно основной)")
        logger.info(
            "   - Тестовый опрос откроется автоматически через 2 минуты или используйте /open 1"
        )
        logger.info("   - Все изменения временные, основная БД не затрагивается")
        logger.info("   - Для остановки нажмите Ctrl+C\n")
        logger.info("=" * 60 + "\n")

        # Запуск polling
        await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("\n⏹️  Остановка бота...")
    except Exception as e:
        logger.exception(f"❌ Ошибка при запуске бота: {e}")
    finally:
        # Удаляем временную БД
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
            logger.info(f"🗑️  Временная БД удалена: {temp_db_path}")

        # Восстанавливаем оригинальный путь к БД
        if original_db_path:
            os.environ["VOLLEYBOT_DB_PATH"] = original_db_path
        elif "VOLLEYBOT_DB_PATH" in os.environ:
            del os.environ["VOLLEYBOT_DB_PATH"]


if __name__ == "__main__":
    asyncio.run(main())
