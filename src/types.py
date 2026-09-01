from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict

from returns.result import Result

# --- Generic Types

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

@dataclass(frozen=True, slots=True)
class InvalidData:
    field: str
    detail: str


# ── DB Types ────────────────────────────────────────────

class TransactionMode(StrEnum):
    DEFERRED = "DEFERRED"
    IMMEDIATE = "IMMEDIATE"

class ConstraintKind(StrEnum):
    UNIQUE = "unique"
    NOT_NULL = "not null"
    CHECK = "check"
    PRIMARY_KEY = "primary key"
    FOREIGN_KEY = "foreign key"


@dataclass(frozen=True, slots=True)
class NotFound:
    entity: str
    key: object


@dataclass(frozen=True, slots=True)
class AlreadyExists:
    entity: str
    key: object


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    kind: ConstraintKind
    operation: str
    detail: str


@dataclass(frozen=True, slots=True)
class InvariantViolation:
    detail: str


@dataclass(frozen=True, slots=True)
class StorageFailure:
    operation: str
    sqlite_code: int | None
    sqlite_name: str | None
    detail: str


type DBError = (
    NotFound
    | InvalidData
    | AlreadyExists
    | ConstraintViolation
    | InvariantViolation
    | StorageFailure
)

type DBResult[T] = Result[T, DBError]


# --- Player Types ---

class Player(TypedDict):
    id: int
    name: str | None
    fullname: str | None
    ball_donate: bool
    is_guest: bool
    balance: int

class PlayerStats(TypedDict):
    games_total: int
    main_count: int
    reserve_count: int
    booked_count: int
    subscription_games: int
    single_game_count: int
    single_game_sum: int
    balance: int


# --- Game Types ---

class GamePollStats(TypedDict):
    games_count: int
    unique_players: int
    avg_main: float
    avg_interest: float
    subscription_uses: int
    single_game_sum: int
    last_game: str | None
    poll_name_snapshot: str

class GamePollStatsSummary(TypedDict):
    games_count: int
    unique_players: int
    avg_main: float
    avg_interest: float
    subscription_uses: int
    single_game_charges: int
    single_game_sum: int
    topups_sum: int
    hall_payments_sum: int
    fund_balance: int
    monthly_polls: int


class GameInfo(TypedDict):
    poll_id: str
    kind: str
    status: str
    poll_template_id: int | None
    poll_name_snapshot: str
    question_snapshot: str
    chat_id: int
    poll_message_id: int
    info_message_id: int | None
    final_message_id: int | None
    opened_at: str
    closed_at: str | None
    game_date: str | None
    place_snapshot: str | None
    cost_snapshot: int
    cost_per_game_snapshot: int
    options_json: str
    option_poll_names_json: str
    last_info_text: str
    created_at: str
    updated_at: str
    target_month_snapshot: str | None


# --- Poll Types ---

class PollTemplateRequired(TypedDict):
    """Обязательные поля шаблона опроса."""

    id: int  # Стабильный идентификатор шаблона
    name: str  # Уникальное отображаемое имя
    message: str  # Текст сообщения
    open_day: str  # День открытия опроса (mon, tue, wed, ...)
    open_hour_utc: int  # Час открытия (UTC)
    open_minute_utc: int  # Минута открытия (UTC)
    game_day: str  # День игры
    game_hour_utc: int  # Час игры (UTC)
    game_minute_utc: int  # Минута игры (UTC)


class PollTemplate(PollTemplateRequired, total=False):
    """Полная структура шаблона опроса из БД."""

    place: str
    cost: int
    cost_per_game: int
    enabled: int
    created_at: str
    updated_at: str
    subs: list[int]


class PollTemplateInput(TypedDict):
    """Данные для создания или изменения шаблона опроса."""

    name: str
    message: str
    id: NotRequired[int]
    place: NotRequired[str]
    open_day: NotRequired[str]
    open_hour_utc: NotRequired[int]
    open_minute_utc: NotRequired[int]
    game_day: NotRequired[str]
    game_hour_utc: NotRequired[int]
    game_minute_utc: NotRequired[int]
    cost: NotRequired[int]
    cost_per_game: NotRequired[int]
    enabled: NotRequired[int]
    subs: NotRequired[list[int]]


class GameParticipant(TypedDict):
    """Финансовый итог участия игрока в закрытой игре."""

    player_id: int
    roster_bucket: Literal["main", "reserve", "booked"]
    sort_order: int
    is_subscriber: NotRequired[bool]
    is_guest: NotRequired[bool]
    guest_free_reason: NotRequired[Literal["first_games", "fill_min_players", "none"]]
    charged_amount: NotRequired[int]
    charge_source: NotRequired[Literal["single_game", "subscription", "none"]]
    balance_before: NotRequired[int | None]
    balance_after: NotRequired[int | None]


class SingleGameIncomeRow(TypedDict):
    games_count: int
    single_game_charges: int
    single_game_sum: int
    avg_income_per_game: float


SingleGameIncomeStats = TypedDict(
    "SingleGameIncomeStats",
    {
        "global": SingleGameIncomeRow,
        "by_poll_template_id": dict[int, SingleGameIncomeRow],
    },
)


@dataclass(frozen=True)
class PollCreationSpec:
    """Готовый payload и снапшоты для создания опроса."""

    kind: str
    poll_name: str
    question: str
    options: tuple[str, ...] = ()
    allows_multiple_answers: bool = False
    subs: tuple[int, ...] = ()
    option_poll_names: tuple[str | None, ...] = ()
    poll_template_id: int | None = None
    place_snapshot: str = ""
    cost_snapshot: int = 0
    cost_per_game_snapshot: int = 0
    target_month_snapshot: str | None = None


# ── Результаты расчёта абонемента ────────────────────────────────────────────


@dataclass
class HallBreakdown:
    """Расчёт стоимости одного зала."""

    poll_template_id: int
    name: str
    cost_per_game: int
    games_in_month: int
    monthly_rent: int
    num_subs: int
    per_person: int  # Единая цена абонемента за 1 зал, 0 если нет подписчиков


@dataclass
class SubscriberCharge:
    """Итоговая сумма списания с одного подписчика."""

    user_id: int
    total: int  # Суммарная стоимость по всем залам
    halls: list[str] = field(default_factory=list)  # Названия залов


@dataclass
class SubscriptionResult:
    """Полный результат расчёта абонемента."""

    paid_polls: list[HallBreakdown]
    subscriber_charges: list[SubscriberCharge]
    price_per_hall: int = 0          # Единая цена абонемента за 1 зал
    combo_price: int = 0             # Комбо-цена за 2 зала, со скидкой
    expected_singles_income: int = 0  # Прогнозируемый доход с разовых игроков
    projected_savings: int = 0       # Прогноз казны на конец месяца
    # Цена абонемента по количеству выбранных залов: hall_count -> price.
    tier_prices: dict[int, int] = field(default_factory=dict)

# --- Message Types ---

class MessageRecord(TypedDict):
    message_id: int
    chat_id: int
    user_id: int
    text: str
    date: int
    username: str
