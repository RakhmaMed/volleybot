#!/usr/bin/env python3
"""
Скрипт для локального тестирования бота.

Использование:
    uv run python test_bot.py

Особенности:
- Работает с временной БД (не трогает основную БД)
- Можно тестировать в любом чате (не только в основном CHAT_ID)
- Автоматически создаёт тестовые опросы, игроков и кассу
- Позволяет использовать все команды бота, включая /pay, /restore, /balance
"""

import asyncio
import logging
import os
import tempfile

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def _seed_test_data() -> None:
    """Заполняет тестовую БД начальными данными."""
    from src.db import (
        ensure_player,
        get_poll_templates,
        init_db,
        save_poll_template,
        update_fund_balance,
        update_player_balance,
    )

    init_db()

    # ── Игроки ───────────────────────────────────────────────────────────
    test_players = [
        (1001, "alice", "Алиса Тестова"),
        (1002, "bob", "Борис Тестов"),
        (1003, "carol", "Карина Тестова"),
    ]
    for pid, name, fullname in test_players:
        ensure_player(user_id=pid, name=name, fullname=fullname)
    logger.info(f"✅ Создано {len(test_players)} тестовых игроков")

    # Устанавливаем тестовые балансы
    update_player_balance(1001, -300)   # Алиса должна 300₽
    update_player_balance(1002, -150)   # Борис должен 150₽
    # Карина — баланс 0
    logger.info("   💰 Алиса: -300₽, Борис: -150₽, Карина: 0₽")

    # ── Опросы ───────────────────────────────────────────────────────────
    save_poll_template({
        "name": "Понедельник",
        "place": "Зал №1 (тест)",
        "message": "🧪 Понедельник — тестовый опрос",
        "open_day": "*",
        "open_hour_utc": 14,
        "open_minute_utc": 42,
        "game_day": "*",
        "game_hour_utc": 15,
        "game_minute_utc": 14,
        "cost": 150,
        "cost_per_game": 1500,
        "subs": [1001, 1003],
    })
    save_poll_template({
        "name": "Среда",
        "place": "Школа (бесплатно)",
        "message": "🧪 Среда — бесплатный зал",
        "open_day": "*",
        "open_hour_utc": 10,
        "open_minute_utc": 0,
        "game_day": "*",
        "game_hour_utc": 19,
        "game_minute_utc": 0,
        "cost": 0,
        "cost_per_game": 0,
        "subs": [],
    })
    save_poll_template({
        "name": "Пятница",
        "place": "Зал №2 (тест)",
        "message": "🧪 Пятница — тестовый опрос",
        "open_day": "*",
        "open_hour_utc": 19,
        "open_minute_utc": 48,
        "game_day": "*",
        "game_hour_utc": 20,
        "game_minute_utc": 20,
        "cost": 150,
        "cost_per_game": 1125,
        "subs": [1002, 1003],
    })
    logger.info(
        "✅ Создано 3 тестовых опроса "
        "(Понедельник 1500₽×4=6000₽, Среда бесплатно, Пятница 1125₽×4=4500₽)"
    )

    # ── Касса ────────────────────────────────────────────────────────────
    update_fund_balance(2500)
    logger.info("   🏦 Касса: 2500₽")

    # ── Отладочная сводка по шаблонам ───────────────────────────────────
    logger.info("   🧾 Шаблоны опросов в тестовой БД:")
    for template in get_poll_templates():
        logger.info(
            "      id=%s | %s | cost=%s₽ | cost_per_game=%s₽ | monthly≈%s₽ | subs=%s",
            template["id"],
            template["name"],
            template.get("cost", 0),
            template.get("cost_per_game", 0),
            int(template.get("cost_per_game", 0) or 0) * 4,
            template.get("subs", []),
        )


async def main():
    """Запуск бота в тестовом режиме."""
    # Проверяем наличие .env файла
    if not os.path.exists(".env"):
        logger.error("❌ Файл .env не найден!")
        logger.info("Создайте .env файл с необходимыми настройками (см. .env.example)")
        return

    # Создаём временную БД
    temp_db = tempfile.NamedTemporaryFile(
        mode="w", suffix=".db", delete=False, prefix="test_volleybot_"
    )
    temp_db_path = temp_db.name
    temp_db.close()

    logger.info("🧪 Тестовый режим")
    logger.info(f"📁 Временная БД: {temp_db_path}")
    logger.info("⚠️  Все изменения будут утеряны после завершения (БД удалится автоматически)")

    original_db_path = os.environ.get("VOLLEYBOT_DB_PATH", "")
    os.environ["VOLLEYBOT_DB_PATH"] = temp_db_path

    try:
        # Импортируем модули ПОСЛЕ установки переменной окружения
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from src.config import CHAT_ID, TOKEN
        from src.handlers import register_handlers, setup_bot_commands
        from src.scheduler import setup_scheduler
        from src.services import AdminService, BotStateService, PollService

        logger.info(f"🔑 Токен бота: {TOKEN[:10]}...")
        logger.info(f"💬 Основной чат ID: {CHAT_ID}")

        # Заполняем тестовую БД
        logger.info("🗄️  Инициализация временной БД...")
        _seed_test_data()

        # Инициализация сервисов
        admin_service = AdminService(default_chat_id=CHAT_ID)
        bot_state_service = BotStateService(default_chat_id=CHAT_ID)
        poll_service = PollService()

        # Инициализация бота и диспетчера
        bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher()
        scheduler = AsyncIOScheduler(timezone="UTC")

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": bot_state_service,
            "poll_service": poll_service,
            "scheduler": scheduler,
        })

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
        logger.info("")
        logger.info("=" * 65)
        logger.info("  🚀 БОТ ЗАПУЩЕН В ТЕСТОВОМ РЕЖИМЕ")
        logger.info("=" * 65)
        logger.info("")
        logger.info("  📋 Команды для тестирования:")
        logger.info("     /start           — Включить бота")
        logger.info("     /help            — Справка по всем командам")
        logger.info("     /balance         — Долги и касса (для админа)")
        logger.info("     /schedule        — Расписание опросов")
        logger.info("     /subs            — Абонементы по дням")
        logger.info("     /player          — Список всех игроков")
        logger.info("     /webhookinfo     — Проверить webhook/allowed_updates")
        logger.info("")
        logger.info("  💰 Финансы:")
        logger.info("     /pay Алиса 300   — Оплатить за Алису (касса +300)")
        logger.info("     /restore Борис 150 — Восстановить Борису (касса без изменений)")
        logger.info("     /pay Оплата зала — Оплатить аренду зала из кассы")
        logger.info("")
        logger.info("  📅 Месячный опрос (абонемент):")
        logger.info("     /open_monthly  — Открыть опрос абонемента")
        logger.info("     /close_monthly — Закрыть опрос и выполнить расчёт")
        logger.info("")
        logger.info("  🧪 Тестовые данные:")
        logger.info(
            "     Залы: Понедельник (150₽, аренда 1500₽×4≈6000₽), "
            "Среда (бесплатно), Пятница (150₽, аренда 1125₽×4≈4500₽)"
        )
        logger.info("     Игроки: Алиса (-300₽), Борис (-150₽), Карина (0₽)")
        logger.info("     Подписки: Понедельник → Алиса, Карина; Пятница → Борис, Карина")
        logger.info("     Касса: 2500₽")
        logger.info("")
        logger.info("  💡 Советы:")
        logger.info("     — Добавьте бота в тестовую группу")
        logger.info("     — Дайте боту права администратора")
        logger.info("     — /balance, /subs, /player и финансовые команды проверяйте из-под админа")
        logger.info("     — /pay Оплата зала покажет inline-кнопки по внутренним poll_template_id")
        logger.info("     — Для остановки нажмите Ctrl+C")
        logger.info("")
        logger.info("=" * 65)
        logger.info("")

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
