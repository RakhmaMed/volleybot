"""Модели и утилиты для работы с опросами."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .config import MAX_PLAYERS, RESERVE_PLAYERS
from .db import get_player_info
from .utils import normalize_telegram_username

SUBSCRIPTION_PRIORITY_WINDOW_HOURS = 14
GUEST_RELEASE_HOUR_MSK = 9
MSK_TZ = ZoneInfo("Europe/Moscow")


class VoterInfo(BaseModel):
    """Информация о проголосовавшем."""

    id: int = Field(..., description="ID пользователя Telegram")
    name: str = Field(..., description="Имя пользователя")
    update_id: int = Field(default=0, description="ID обновления для сортировки")
    voted_at: str = Field(default="", description="Время голоса в UTC ISO-формате")
    is_guest: bool = Field(default=False, description="Гость ли игрок")

    model_config = {"frozen": False}  # Разрешаем изменение полей


class PollRosterEntry(BaseModel):
    """Нормализованная запись игрока в составе regular-опроса."""

    player_id: int = Field(..., description="ID пользователя Telegram")
    rendered_name: str = Field(..., description="Актуальное отображаемое имя")
    update_id: int = Field(default=0, description="ID обновления для сортировки")
    voted_at: str = Field(default="", description="Время голоса в UTC ISO-формате")
    is_subscriber: bool = Field(default=False, description="Есть ли абонемент")
    has_subscription_priority: bool = Field(
        default=False, description="Получил ли временный приоритет абонемента"
    )
    has_ball_donate: bool = Field(default=False, description="Есть ли донат на мяч")
    is_guest: bool = Field(default=False, description="Гость ли игрок")
    guest_free_reason: str = Field(
        default="none", description="Причина бесплатного гостевого участия"
    )
    roster_bucket: str = Field(
        default="booked", description="main, reserve или booked"
    )
    sort_order: int = Field(default=0, description="Порядок в итоговом составе")


class PollRoster(BaseModel):
    """Полный состав игроков regular-опроса."""

    entries: list[PollRosterEntry] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def main_entries(self) -> list[PollRosterEntry]:
        return [entry for entry in self.entries if entry.roster_bucket == "main"]

    @property
    def reserve_entries(self) -> list[PollRosterEntry]:
        return [entry for entry in self.entries if entry.roster_bucket == "reserve"]

    @property
    def booked_entries(self) -> list[PollRosterEntry]:
        return [entry for entry in self.entries if entry.roster_bucket == "booked"]


class PollData(BaseModel):
    """Данные активного опроса."""

    kind: str = Field(default="regular", description="Тип опроса")
    status: str = Field(default="open", description="Статус игры")
    poll_template_id: int | None = Field(default=None, description="ID шаблона")
    poll_name_snapshot: str = Field(default="", description="Имя опроса в момент создания")
    question_snapshot: str = Field(default="", description="Текст вопроса")
    opened_at: str = Field(default="", description="Время открытия")
    chat_id: int = Field(..., description="ID чата")
    poll_msg_id: int = Field(..., description="ID сообщения с опросом")
    info_msg_id: int | None = Field(
        default=None, description="ID информационного сообщения"
    )
    final_message_id: int | None = Field(
        default=None, description="ID финального сообщения"
    )
    yes_voters: list[VoterInfo] = Field(
        default_factory=list, description="Список проголосовавших 'Да'"
    )
    last_message_text: str = Field(
        default="⏳ Идёт сбор голосов...", description="Последний отправленный текст"
    )
    subs: list[int] = Field(default_factory=list, description="Список ID подписчиков")
    options: list[str] = Field(default_factory=list, description="Список опций опроса")
    option_poll_names: list[str | None] = Field(
        default_factory=list, description="Соответствие опция → poll_name"
    )
    monthly_votes: dict[int, list[int]] = Field(
        default_factory=dict, description="Выборы пользователей в месячном опросе"
    )
    target_month: str | None = Field(
        default=None, description="Целевой месяц абонемента в формате YYYY-MM"
    )

    model_config = {"arbitrary_types_allowed": True, "frozen": False}

    @property
    def poll_kind(self) -> str:
        """Совместимость со старым именем поля."""
        return self.kind

    @poll_kind.setter
    def poll_kind(self, value: str) -> None:
        self.kind = value


def _parse_iso_datetime(value: str) -> datetime | None:
    """Парсит ISO-дату и нормализует её в UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strip_voter_status_prefix(name: str) -> str:
    """Убирает legacy-эмодзи из старого снимка имени."""
    cleaned = name.lstrip()
    changed = True
    while changed:
        changed = False
        for prefix in ("⭐️", "⭐", "🏐", "🙋"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].lstrip()
                changed = True
    return cleaned or name


def _render_voter_name(voter: VoterInfo, is_subscriber: bool) -> tuple[str, bool]:
    """Строит отображаемое имя и признак доната по данным БД."""
    player = get_player_info(voter.id)
    fallback_name = _strip_voter_status_prefix(voter.name) or "Неизвестный"

    username = ""
    display_name = fallback_name
    ball_donate = False

    if player:
        username = normalize_telegram_username(player.get("name")) or ""
        fullname = str(player.get("fullname") or "").strip()
        ball_donate = bool(player.get("ball_donate"))
        if fullname:
            display_name = fullname
        elif username:
            display_name = f"@{username}"

    emojis = ""
    if is_subscriber:
        emojis += "⭐️"
    if ball_donate:
        emojis += "🏐"
    if emojis:
        display_name = f"{emojis} {display_name}"

    if not username:
        return display_name, ball_donate

    username_mention = f"@{username}"
    if display_name == username_mention or display_name.endswith(f" {username_mention}"):
        return display_name, ball_donate
    return f"{display_name} ({username_mention})", ball_donate


def _resolve_voter_datetime(voter: VoterInfo, opened_at: str) -> tuple[datetime | None, str]:
    """Возвращает время голоса или fallback на время открытия опроса."""
    voted_at = voter.voted_at or opened_at
    return _parse_iso_datetime(voted_at), voted_at


def _guest_release_deadline(opened_at: str) -> datetime | None:
    opened_dt = _parse_iso_datetime(opened_at)
    if opened_dt is None:
        return None
    opened_msk = opened_dt.astimezone(MSK_TZ)
    release_date = opened_msk.date() + timedelta(days=1)
    return datetime.combine(
        release_date,
        time(hour=GUEST_RELEASE_HOUR_MSK),
        tzinfo=MSK_TZ,
    ).astimezone(timezone.utc)


def build_regular_poll_roster(
    data: PollData, now: datetime | None = None
) -> PollRoster:
    """Собирает единый состав игроков для regular-опроса."""
    opened_dt = _parse_iso_datetime(data.opened_at)
    priority_deadline = None
    if opened_dt is not None:
        priority_deadline = opened_dt + timedelta(
            hours=SUBSCRIPTION_PRIORITY_WINDOW_HOURS
        )
    guest_release_deadline = _guest_release_deadline(data.opened_at)
    current_dt = now or datetime.now(timezone.utc)
    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=timezone.utc)
    current_dt = current_dt.astimezone(timezone.utc)
    guests_released = (
        guest_release_deadline is None or current_dt >= guest_release_deadline
    )

    subs = set(data.subs)
    prepared: list[tuple[PollRosterEntry, datetime | None]] = []
    for voter in data.yes_voters:
        voted_dt, voted_at = _resolve_voter_datetime(voter, data.opened_at)
        is_subscriber = voter.id in subs
        has_priority = (
            data.kind == "regular"
            and is_subscriber
            and priority_deadline is not None
            and voted_dt is not None
            and voted_dt <= priority_deadline
        )
        rendered_name, has_ball_donate = _render_voter_name(voter, is_subscriber)
        if voter.is_guest:
            rendered_name = f"🙋 {rendered_name}"
        prepared.append(
            (
                PollRosterEntry(
                    player_id=voter.id,
                    rendered_name=rendered_name,
                    update_id=voter.update_id,
                    voted_at=voted_at,
                    is_subscriber=is_subscriber,
                    has_subscription_priority=has_priority,
                    has_ball_donate=has_ball_donate,
                    is_guest=voter.is_guest,
                ),
                voted_dt,
            )
        )

    def sort_key(item: tuple[PollRosterEntry, datetime | None]) -> tuple[object, ...]:
        return (
            not item[0].has_subscription_priority,
            item[0].update_id,
            item[1] or datetime.min.replace(tzinfo=timezone.utc),
            item[0].player_id,
        )

    group_players = [item for item in prepared if not item[0].is_guest]
    guest_players = [item for item in prepared if item[0].is_guest]
    group_players.sort(key=sort_key)
    guest_players.sort(key=sort_key)

    ordered_items = group_players + guest_players

    entries: list[PollRosterEntry] = []

    def bucket_for_index(index: int) -> str:
        if index <= MAX_PLAYERS:
            return "main"
        if index <= MAX_PLAYERS + RESERVE_PLAYERS:
            return "reserve"
        return "booked"

    if guests_released:
        for index, (entry, _) in enumerate(ordered_items, start=1):
            entries.append(
                entry.model_copy(
                    update={
                        "roster_bucket": bucket_for_index(index),
                        "sort_order": index,
                    }
                )
            )
    else:
        for index, (entry, _) in enumerate(group_players, start=1):
            entries.append(
                entry.model_copy(
                    update={
                        "roster_bucket": bucket_for_index(index),
                        "sort_order": index,
                    }
                )
            )
        guest_start = max(MAX_PLAYERS, len(group_players)) + 1
        for offset, (entry, _) in enumerate(guest_players):
            sort_order = guest_start + offset
            entries.append(
                entry.model_copy(
                    update={
                        "roster_bucket": bucket_for_index(sort_order),
                        "sort_order": sort_order,
                    }
                )
            )

    return PollRoster(entries=entries)


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
