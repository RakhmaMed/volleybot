"""Загрузка конфигурации из переменных окружения (.env) с использованием pydantic-settings."""

from __future__ import annotations

import logging

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PollSchedule(BaseSettings):
    """
    Модель конфигурации одного опроса.
    Примечание: Теперь используется только для миграции или типизации,
    так как основные данные опросов перенесены в БД.
    """

    name: str
    place: str = ""
    message: str
    open_day: str = "*"
    open_hour_utc: int = 0
    open_minute_utc: int = 0
    game_day: str = "*"
    game_hour_utc: int = 0
    game_minute_utc: int = 0
    subs: list[int] = []


class Settings(BaseSettings):
    """Настройки приложения, загружаемые из .env или переменных окружения."""

    # Telegram настройки
    telegram_token: str = Field(..., validation_alias="TELEGRAM_TOKEN")
    chat_id: int = Field(..., validation_alias="CHAT_ID")
    admin_user_id: int | None = Field(default=None, validation_alias="ADMIN_USER_ID")

    # Webhook настройки
    webhook_host: str = Field(default="", validation_alias="WEBHOOK_HOST")
    webhook_path: str = Field(default="/webhook", validation_alias="WEBHOOK_PATH")
    webhook_secret: str = Field(default="", validation_alias="WEBHOOK_SECRET")
    webhook_port: int = Field(default=8443, validation_alias="WEBHOOK_PORT")
    trust_proxy: bool = Field(default=False, validation_alias="TRUST_PROXY")

    # Настройки опросов
    min_players: int = Field(default=12, validation_alias="MIN_PLAYERS")
    max_players: int = Field(default=18, validation_alias="MAX_PLAYERS")
    reserve_players: int = Field(default=3, validation_alias="RESERVE_PLAYERS")
    poll_options_raw: str = Field(default="Да,Нет", validation_alias="POLL_OPTIONS")

    # Настройки планировщика
    scheduler_timezone: str = Field(
        default="UTC", validation_alias="SCHEDULER_TIMEZONE"
    )

    # Настройки логирования
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # Настройка Pydantic Settings
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"log_level должен быть одним из {valid_levels}")
        return v_upper

    @model_validator(mode="after")
    def validate_webhook_config(self) -> "Settings":
        if self.webhook_host:
            if not self.webhook_host.startswith(("http://", "https://")):
                raise ValueError(
                    "webhook_host должен начинаться с http:// или https://"
                )
        return self

    @property
    def poll_options(self) -> tuple[str, ...]:
        """Преобразует строку опций через запятую в кортеж."""
        return tuple(opt.strip() for opt in self.poll_options_raw.split(","))


# Инициализация настроек
try:
    settings = Settings()  # type: ignore[call-arg]
except Exception as e:
    logging.error(f"❌ Ошибка загрузки конфигурации из .env: {e}")
    # В случае ошибки в тестах или при отсутствии .env,
    # если не установлены обязательные переменные, бросаем исключение
    raise

# Формат логирования
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format=LOG_FORMAT,
    force=True,
)

# Экспорт переменных для обратной совместимости
TOKEN: str = settings.telegram_token
CHAT_ID: int = settings.chat_id
ADMIN_USER_ID: int | None = settings.admin_user_id
WEBHOOK_SECRET: str = settings.webhook_secret
MIN_PLAYERS: int = settings.min_players
MAX_PLAYERS: int = settings.max_players
RESERVE_PLAYERS: int = settings.reserve_players
POLL_OPTIONS: tuple[str, ...] = settings.poll_options
SCHEDULER_TIMEZONE: str = settings.scheduler_timezone
LOG_LEVEL: str = settings.log_level

# Webhook настройки
WEBHOOK_HOST: str = settings.webhook_host
WEBHOOK_PATH: str = settings.webhook_path
WEBHOOK_PORT: int = settings.webhook_port
TRUST_PROXY: bool = settings.trust_proxy

# Расписание опросов теперь в БД, оставляем пустой список для совместимости миграции
POLLS_SCHEDULE: list[PollSchedule] = []

# Формируем полный URL webhook
# При использовании reverse proxy (Nginx), WEBHOOK_HOST должен быть публичным URL (напр. https://domain.com)
# Мы не добавляем WEBHOOK_PORT автоматически, так как внешний порт (443) обычно отличается от внутреннего.
WEBHOOK_URL: str = ""
if WEBHOOK_HOST:
    # Просто соединяем хост и путь. Если нужен порт в URL, его следует указать в WEBHOOK_HOST.
    # Это позволяет избежать проблем при работе за прокси.
    WEBHOOK_URL = f"{WEBHOOK_HOST.rstrip('/')}{WEBHOOK_PATH}"

logging.info("✅ Конфигурация успешно загружена из .env")
