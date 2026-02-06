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
    updated_at: str  # Время последнего обновления
    subs: list[int]  # Список user_id подписчиков (добавляется в get_poll_temp
