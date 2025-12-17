"""Модели и утилиты для работы с опросами."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VoterInfo(BaseModel):
    """Информация о проголосовавшем."""

    id: int = Field(..., description="ID пользователя Telegram")
    name: str = Field(..., description="Имя пользователя")
    update_id: int = Field(default=0, description="ID обновления для сортировки")

    model_config = {"frozen": False}  # Разрешаем изменение полей


class PollData(BaseModel):
    """Данные активного опроса."""

    chat_id: int = Field(..., description="ID чата")
    poll_msg_id: int = Field(..., description="ID сообщения с опросом")
    info_msg_id: int | None = Field(
        default=None, description="ID информационного сообщения"
    )
    yes_voters: list[VoterInfo] = Field(
        default_factory=list, description="Список проголосовавших 'Да'"
    )
    last_message_text: str = Field(
        default="⏳ Идёт сбор голосов...", description="Последний отправленный текст"
    )
    subs: list[int] = Field(default_factory=list, description="Список ID подписчиков")

    model_config = {"arbitrary_types_allowed": True, "frozen": False}


def sort_voters_by_update_id(voters: list[VoterInfo]) -> list[VoterInfo]:
    """Возвращает список голосовавших, отсортированный по update_id (порядок событий)."""
    return sorted(voters, key=lambda v: (v.update_id, v.id))
