"""Сервис для управления состоянием бота."""

import logging

from pydantic import BaseModel, Field

from ..db import BOT_STATE_KEY, load_state, save_state


class BotState(BaseModel):
    """Типизированное состояние бота."""

    bot_enabled: bool = Field(default=True, description="Флаг включения бота")
    chat_id: int = Field(..., description="ID чата для отправки сообщений")

    model_config = {"frozen": False}  # Разрешаем изменение полей


class BotStateService:
    """Сервис для управления состоянием бота."""

    def __init__(self, default_chat_id: int) -> None:
        """
        Инициализация сервиса состояния бота.

        Args:
            default_chat_id: ID чата по умолчанию из конфигурации
        """
        self._state = BotState(bot_enabled=True, chat_id=default_chat_id)
        self._restore_state()

    def _restore_state(self) -> None:
        """Восстановить состояние бота из базы данных."""
        stored_state = load_state(BOT_STATE_KEY, default={})
        if isinstance(stored_state, dict):
            self._state.bot_enabled = bool(
                stored_state.get("bot_enabled", self._state.bot_enabled)
            )
            try:
                self._state.chat_id = int(
                    stored_state.get("chat_id", self._state.chat_id)
                )
            except (TypeError, ValueError):
                logging.warning(
                    "Сохранённый chat_id повреждён, оставляем значение из config.json"
                )

    def persist_state(self) -> None:
        """Сохранить состояние бота в базу данных."""
        save_state(BOT_STATE_KEY, self._state.model_dump(mode="json"))

    def is_enabled(self) -> bool:
        """Получить состояние включения бота."""
        return self._state.bot_enabled

    def set_enabled(self, value: bool) -> None:
        """
        Установить состояние включения бота.

        Args:
            value: Новое значение флага включения
        """
        self._state.bot_enabled = value
        self.persist_state()

    def get_chat_id(self) -> int:
        """Получить ID текущего чата."""
        return self._state.chat_id

    def set_chat_id(self, value: int) -> None:
        """
        Установить ID текущего чата.

        Args:
            value: Новый ID чата
        """
        self._state.chat_id = value
        self.persist_state()
