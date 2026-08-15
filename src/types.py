from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


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
    poll_id: int
    kind: str
    status: str
    poll_template_id: int
    poll_name_snapshot: str
    question_snapshot: str
    chat_id: int
    poll_message_id: int
    info_message_id: int
    final_message_id: int
    opened_at: str
    closed_at: str
    game_date: str # Always empty
    place_snapshot: str
    cost_snapshot: int
    cost_per_game_snapshot: int
    options_json: dict
    option_poll_names_json: dict
    last_info_text: str
    created_at: str
    updated_at: str
    target_month_snapshot: str


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

    # Опциональные поля
    place: str  # Место проведения
    cost: int  # Стоимость одной игры
    cost_per_game: int  # Стоимость аренды зала за игру
    enabled: int  # 1 = шаблон включён, 0 = выключен
    created_at: str  # Время создания
    updated_at: str  # Время последнего обновления
    subs: list[int]  # Список user_id подписчиков (добавляется в get_poll_templates)


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
