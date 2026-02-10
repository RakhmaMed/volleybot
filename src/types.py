from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class PollTemplateRequired(TypedDict):
    """Обязательные поля шаблона опроса."""

    name: str  # PRIMARY KEY
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
    monthly_cost: int  # Стоимость аренды зала за месяц
    updated_at: str  # Время последнего обновления
    subs: list[int]  # Список user_id подписчиков (добавляется в get_poll_templates)


# ── Результаты расчёта абонемента ────────────────────────────────────────────


@dataclass
class HallBreakdown:
    """Расчёт стоимости одного зала."""

    name: str
    monthly_cost: int
    num_subs: int
    per_person: int  # Единая цена абонемента за 1 зал (400-500 руб.), 0 если нет подписчиков


@dataclass
class SubscriberCharge:
    """Итоговая сумма списания с одного подписчика."""

    user_id: int
    total: int  # Суммарная стоимость по всем залам
    halls: list[str] = field(default_factory=list)  # Названия залов


@dataclass
class SubscriptionResult:
    """Полный результат расчёта абонемента."""

    hall_breakdown: list[HallBreakdown]
    subscriber_charges: list[SubscriberCharge]
    price_per_hall: int = 0          # Единая цена абонемента за 1 зал (400-500 руб.)
    combo_price: int = 0             # Комбо-цена (2+ зала, со скидкой)
    expected_singles_income: int = 0  # Прогнозируемый доход с разовых игроков
    projected_savings: int = 0       # Прогноз казны на конец месяца
