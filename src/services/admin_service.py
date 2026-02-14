"""Сервис для управления и проверки администраторов группы."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramNetworkError

from ..utils import retry_async

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import User


# Время жизни кэша администраторов (в секундах)
ADMIN_CACHE_TTL = 300  # 5 минут


class AdminService:
    """
    Сервис для проверки администраторов группы через Telegram API.

    Кэширует список администраторов для уменьшения нагрузки на API.
    """

    def __init__(self, default_chat_id: int) -> None:
        """
        Инициализация сервиса администраторов.

        Args:
            default_chat_id: ID чата по умолчанию для проверки администраторов
        """
        self._default_chat_id = default_chat_id
        # Кэш администраторов: {chat_id: {user_ids}}
        self._admin_cache: dict[int, set[int]] = {}
        # Время последнего обновления кэша: {chat_id: timestamp}
        self._cache_updated_at: dict[int, float] = {}
        # Lock для безопасного обновления кэша
        self._cache_lock = asyncio.Lock()

        logging.debug(
            f"AdminService инициализирован с default_chat_id={default_chat_id}"
        )

    def _is_cache_valid(self, chat_id: int) -> bool:
        """
        Проверяет, валиден ли кэш для данного чата.

        Args:
            chat_id: ID чата

        Returns:
            True если кэш валиден, иначе False
        """
        if chat_id not in self._cache_updated_at:
            return False

        age = time.time() - self._cache_updated_at[chat_id]
        return age < ADMIN_CACHE_TTL

    async def _fetch_admins(self, bot: Bot, chat_id: int) -> set[int]:
        """
        Загружает список администраторов из Telegram API.

        Args:
            bot: Экземпляр бота
            chat_id: ID чата

        Returns:
            Множество ID администраторов
        """
        try:
            logging.debug(f"Загрузка администраторов для чата {chat_id}...")

            @retry_async(
                (TelegramNetworkError, asyncio.TimeoutError, OSError),
                tries=3,
                delay=2,
            )
            async def fetch_with_retry():
                return await bot.get_chat_administrators(chat_id)

            admins = await fetch_with_retry()
            admin_ids = {admin.user.id for admin in admins}

            logging.info(
                f"✅ Загружено {len(admin_ids)} администраторов для чата {chat_id}"
            )
            logging.debug(f"   ID администраторов: {admin_ids}")

            return admin_ids

        except Exception as e:
            logging.exception(
                f"❌ Ошибка при загрузке администраторов для чата {chat_id}: {e}"
            )
            # Возвращаем пустой набор при ошибке
            return set()

    async def refresh_cache(self, bot: Bot, chat_id: int | None = None) -> None:
        """
        Принудительно обновляет кэш администраторов.

        Args:
            bot: Экземпляр бота
            chat_id: ID чата (если None или приватный чат, используется default_chat_id)
        """
        # Если chat_id положительный (приватный чат) или None, используем default_chat_id
        # Групповые чаты имеют отрицательные ID
        if chat_id is None or chat_id > 0:
            target_chat_id = self._default_chat_id
        else:
            target_chat_id = chat_id

        async with self._cache_lock:
            admin_ids = await self._fetch_admins(bot, target_chat_id)
            self._admin_cache[target_chat_id] = admin_ids
            self._cache_updated_at[target_chat_id] = time.time()

            logging.info(f"🔄 Кэш администраторов обновлён для чата {target_chat_id}")

    async def is_admin(self, bot: Bot, user: User, chat_id: int | None = None) -> bool:
        """
        Проверяет, является ли пользователь администратором группы.

        Args:
            bot: Экземпляр бота
            user: Объект пользователя Telegram
            chat_id: ID чата для проверки (если None или приватный чат, используется default_chat_id)

        Returns:
            True если пользователь является администратором, иначе False
        """
        # Если chat_id положительный (приватный чат) или None, используем default_chat_id
        # Групповые чаты имеют отрицательные ID
        if chat_id is None or chat_id > 0:
            target_chat_id = self._default_chat_id
        else:
            target_chat_id = chat_id

        # Проверяем кэш
        if not self._is_cache_valid(target_chat_id):
            await self.refresh_cache(bot, target_chat_id)

        # Проверяем наличие в кэше
        admin_ids = self._admin_cache.get(target_chat_id, set())
        is_admin = user.id in admin_ids

        if is_admin:
            logging.debug(
                f"✅ Пользователь @{user.username} (ID: {user.id}) "
                f"является администратором чата {target_chat_id}"
            )
        else:
            logging.debug(
                f"❌ Пользователь @{user.username} (ID: {user.id}) "
                f"НЕ является администратором чата {target_chat_id}"
            )

        return is_admin

    def invalidate_cache(self, chat_id: int | None = None) -> None:
        """
        Инвалидирует кэш администраторов.

        Args:
            chat_id: ID чата для инвалидации (если None, инвалидируется весь кэш)
        """
        if chat_id is None:
            self._admin_cache.clear()
            self._cache_updated_at.clear()
            logging.info("🗑️ Весь кэш администраторов очищен")
        else:
            self._admin_cache.pop(chat_id, None)
            self._cache_updated_at.pop(chat_id, None)
            logging.info(f"🗑️ Кэш администраторов очищен для чата {chat_id}")

    def get_cached_admins(self, chat_id: int | None = None) -> set[int]:
        """
        Возвращает кэшированный список администраторов (для отладки).

        Args:
            chat_id: ID чата (если None, используется default_chat_id)

        Returns:
            Множество ID администраторов из кэша
        """
        target_chat_id = chat_id or self._default_chat_id
        return self._admin_cache.get(target_chat_id, set()).copy()
