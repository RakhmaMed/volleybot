#!/usr/bin/env python3
"""
Утилита для получения ID чата в Telegram.

Использование:
    1. Запустите скрипт: python get_chat_id.py
    2. Отправьте любое сообщение боту (в личку или добавьте бота в группу)
    3. Скрипт выведет ID чата/пользователя

Это полезно для:
- Настройки CHAT_ID в .env для тестовых групп
- Получения USER_ID для добавления админов
- Отладки и тестирования в разных чатах
"""

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


async def main():
    """Запуск утилиты для получения chat_id."""
    # Проверяем наличие токена
    token = os.getenv("TELEGRAM_TOKEN")

    if not token:
        # Пробуем загрузить из .env через pydantic-settings
        if os.path.exists(".env"):
            try:
                from src.config import TOKEN

                token = TOKEN
            except Exception:
                pass

    if not token:
        logger.error("❌ Не найден TELEGRAM_TOKEN!")
        logger.info("💡 Убедитесь что:")
        logger.info("   1. Файл .env существует")
        logger.info("   2. В нем указан TELEGRAM_TOKEN=ваш_токен")
        logger.info("   3. Или установите переменную окружения TELEGRAM_TOKEN")
        sys.exit(1)

    logger.info(f"🔑 Используется токен: {token[:10]}...")

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        """Обработчик команды /start."""
        await show_chat_info(message)

    @dp.message()
    async def any_message(message: Message):
        """Обработчик любого сообщения."""
        await show_chat_info(message)

    async def show_chat_info(message: Message):
        """Показывает информацию о чате."""
        chat = message.chat
        user = message.from_user

        info_lines = ["📊 Информация о чате:\n"]

        # Тип чата
        chat_type_emoji = {
            "private": "👤",
            "group": "👥",
            "supergroup": "👥",
            "channel": "📢",
        }
        emoji = chat_type_emoji.get(chat.type, "❓")
        info_lines.append(f"{emoji} Тип: {chat.type}")

        # ID чата
        info_lines.append(f"🆔 Chat ID: <code>{chat.id}</code>")

        # Название чата (если есть)
        if chat.title:
            info_lines.append(f"📝 Название: {chat.title}")

        # Информация о пользователе
        if user:
            info_lines.append(f"\n👤 Отправитель:")
            info_lines.append(f"   🆔 User ID: <code>{user.id}</code>")
            info_lines.append(f"   👤 Username: @{user.username or 'нет'}")
            info_lines.append(f"   📛 Имя: {user.full_name}")

        # Дополнительная информация для групп
        if chat.type in ["group", "supergroup"]:
            info_lines.append(f"\n💡 Для использования этого чата:")
            info_lines.append(f"   Добавьте в .env: CHAT_ID={chat.id}")

        info = "\n".join(info_lines)

        # Отправляем информацию
        await message.answer(info, parse_mode="HTML")

        # Выводим в консоль
        print("\n" + "=" * 60)
        print("📊 ПОЛУЧЕНА ИНФОРМАЦИЯ О ЧАТЕ")
        print("=" * 60)
        print(f"Тип чата: {chat.type}")
        print(f"Chat ID: {chat.id}")
        if chat.title:
            print(f"Название: {chat.title}")
        if user:
            print(f"\nОтправитель:")
            print(f"  User ID: {user.id}")
            print(f"  Username: @{user.username or 'нет'}")
            print(f"  Имя: {user.full_name}")
        print("=" * 60)
        print(f"\n💾 Для сохранения в .env используйте:")
        print(f"   CHAT_ID={chat.id}")
        if user:
            print(f"   ADMIN_USER_ID={user.id}")
        print()

    logger.info("\n" + "=" * 60)
    logger.info("🚀 УТИЛИТА ЗАПУЩЕНА")
    logger.info("=" * 60)
    logger.info("\n📱 Инструкция:")
    logger.info("   1. Отправьте боту любое сообщение")
    logger.info("   2. Или добавьте бота в группу и отправьте сообщение там")
    logger.info("   3. Получите ID чата для настройки .env")
    logger.info("\n⏸️  Для остановки нажмите Ctrl+C\n")
    logger.info("=" * 60 + "\n")

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("\n⏹️  Остановка утилиты...")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
