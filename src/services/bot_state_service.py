"""Сервис для управления состоянием бота."""

from __future__ import annotations

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
        logging.debug(
            f"Инициализация BotStateService с default_chat_id={default_chat_id}"
        )
        self._state = BotState(bot_enabled=True, chat_id=default_chat_id)
        self._restore_state()
        logging.debug(
            f"BotStateService инициализирован: enabled={self._state.bot_enabled}, chat_id={self._state.chat_id}"
        )

    def _restore_state(self) -> None:
        """Восстановить состояние бота из базы данных."""
        logging.debug("Восстановление состояния бота из БД...")
        chat_id_from_config: int = self._state.chat_id  # CHAT_ID из .env
        stored_state = load_state(BOT_STATE_KEY, default={})
        if isinstance(stored_state, dict):
            old_enabled = self._state.bot_enabled
            old_chat_id = self._state.chat_id

            self._state.bot_enabled = bool(
                stored_state.get("bot_enabled", self._state.bot_enabled)
            )
            try:
                self._state.chat_id = int(
                    stored_state.get("chat_id", self._state.chat_id)
                )
            except (TypeError, ValueError):
                logging.warning(
                    f"⚠️ Сохранённый chat_id повреждён, используем значение из настроек: {self._state.chat_id}"
                )

            # Приоритет .env: если CHAT_ID в конфиге изменился — используем его и сохраняем в БД
            if self._state.chat_id != chat_id_from_config:
                logging.info(
                    f"🔄 CHAT_ID в .env изменился: {self._state.chat_id} → {chat_id_from_config}, используем значение из .env"
                )
                self._state.chat_id = chat_id_from_config
                self.persist_state()

            if (
                old_enabled != self._state.bot_enabled
                or old_chat_id != self._state.chat_id
            ):
                logging.info(
                    f"✅ Состояние бота восстановлено: enabled={self._state.bot_enabled}, chat_id={self._state.chat_id}"
                )
            else:
                logging.debug("Состояние бота не изменилось после восстановления")
        else:
            logging.debug(
                "Сохраненное состояние не найдено, используем значения по умолчанию"
            )

    def persist_state(self) -> None:
        """Сохранить состояние бота в базу данных."""
        logging.debug(
            f"Сохранение состояния бота: enabled={self._state.bot_enabled}, chat_id={self._state.chat_id}"
        )
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
        old_value = self._state.bot_enabled
        self._state.bot_enabled = value
        if old_value != value:
            logging.info(f"🔄 Состояние бота изменено: enabled={value}")
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
        old_value = self._state.chat_id
        self._state.chat_id = value
        if old_value != value:
            logging.info(f"🔄 Chat ID изменен: {old_value} → {value}")
        self.persist_state()
