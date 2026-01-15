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


def sort_voters_by_update_id(
    voters: list[VoterInfo], subs: list[int] | None = None
) -> list[VoterInfo]:
    """
    Возвращает список голосовавших, отсортированный по наличию подписки и update_id.
    Игроки с подпиской всегда отображаются вверху списка.
    """
    # Сначала идут игроки с подпиской, затем остальные.
    # Внутри каждой группы сохраняется порядок голосования (по update_id).
    return sorted(
        voters,
        key=lambda v: (
            v.id not in (subs or []),
            v.update_id,
            v.id,
        ),
    )
