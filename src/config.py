"""Загрузка конфигурации и константы приложения с валидацией через Pydantic."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


class PollSchedule(BaseModel):
    """Модель конфигурации одного опроса."""

    name: str = Field(..., min_length=1, description="Название опроса")
    message: str = Field(..., min_length=1, description="Текст вопроса опроса")
    open_day: str = Field(
        default="*",
        description="День открытия опроса (mon/tue/wed/thu/fri/sat/sun или *)",
    )
    open_hour_utc: Annotated[int, Field(ge=0, le=23)] = Field(
        default=0, description="Час открытия опроса (UTC, 0-23)"
    )
    open_minute_utc: Annotated[int, Field(ge=0, le=59)] = Field(
        default=0, description="Минута открытия опроса (UTC, 0-59)"
    )
    close_day: str = Field(
        default="*",
        description="День закрытия опроса (mon/tue/wed/thu/fri/sat/sun или *)",
    )
    close_hour_utc: Annotated[int, Field(ge=0, le=23)] = Field(
        default=0, description="Час закрытия опроса (UTC, 0-23)"
    )
    close_minute_utc: Annotated[int, Field(ge=0, le=59)] = Field(
        default=0, description="Минута закрытия опроса (UTC, 0-59)"
    )
    subs: list[int] = Field(
        default_factory=list, description="Список ID подписчиков для этого опроса"
    )

    @field_validator("open_day", "close_day")
    @classmethod
    def validate_day(cls, v: str) -> str:
        """Валидация дня недели."""
        valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun", "*"}
        if v.lower() not in valid_days:
            raise ValueError(f"День должен быть одним из {valid_days}, получено: {v}")
        return v.lower()


class BotConfig(BaseModel):
    """Модель конфигурации бота с валидацией."""

    # Telegram настройки
    telegram_token: str = Field(..., min_length=1, description="Токен бота Telegram")
    chat_id: int = Field(..., description="ID чата для отправки опросов")
    admin_username: str = Field(default="", description="Username администратора бота")

    # Webhook настройки
    webhook_host: str = Field(default="", description="Хост для webhook")
    webhook_path: str = Field(default="/webhook", description="Путь для webhook")
    webhook_port: Annotated[int, Field(ge=1, le=65535)] = Field(
        default=8443, description="Порт для webhook"
    )

    # SSL сертификаты
    ssl_cert_path: str = Field(
        default="/app/certs/fullchain.pem", description="Путь к SSL сертификату"
    )
    ssl_key_path: str = Field(
        default="/app/certs/privkey.pem", description="Путь к приватному ключу SSL"
    )

    # Расписание опросов
    polls: list[PollSchedule] = Field(
        default_factory=list, description="Список опросов по расписанию"
    )

    # Настройки опросов
    required_players: Annotated[int, Field(ge=1)] = Field(
        default=18, description="Необходимое количество игроков"
    )
    poll_options: list[str] = Field(
        default_factory=lambda: ["Да", "Нет"],
        min_length=2,
        description="Опции опроса",
    )

    # Настройки планировщика
    scheduler_timezone: str = Field(
        default="UTC", description="Таймзона для планировщика задач"
    )

    # Настройки логирования
    log_level: str = Field(
        default="INFO",
        description="Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Валидация уровня логирования."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(
                f"log_level должен быть одним из {valid_levels}, получено: {v}"
            )
        return v_upper

    @field_validator("admin_username")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        """Убирает @ из username если есть."""
        return v.lstrip("@")

    @model_validator(mode="after")
    def validate_webhook_config(self) -> "BotConfig":
        """Валидация webhook конфигурации."""
        if self.webhook_host:
            if not self.webhook_host.startswith(("http://", "https://")):
                raise ValueError(
                    f"webhook_host должен начинаться с http:// или https://, получено: {self.webhook_host}"
                )
        return self


# Глобальная переменная для ленивой загрузки конфигурации
_config: BotConfig | None = None

# Формат логирования для всего приложения
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def load_config_from_file(config_path: Path) -> BotConfig:
    """Загружает конфигурацию из JSON файла."""
    with open(config_path, encoding="utf-8") as f:
        config_data = json.load(f)
    return BotConfig(**config_data)


def get_config() -> BotConfig:
    """Ленивая загрузка и валидация конфигурации."""
    global _config
    if _config is None:
        config_path = Path(__file__).parent.parent / "config.json"

        try:
            logging.debug(f"Попытка загрузки конфигурации из: {config_path}")
            _config = load_config_from_file(config_path)
            # Настраиваем логирование на основе конфигурации
            log_level = getattr(logging, _config.log_level, logging.INFO)
            logging.basicConfig(level=log_level, force=True, format=LOG_FORMAT)
            logging.info(
                f"✅ Конфигурация успешно загружена из {config_path} (уровень логирования: {_config.log_level})"
            )
            if _config.polls:
                logging.info(f"📋 Загружено {len(_config.polls)} опросов в расписании")
                for poll in _config.polls:
                    logging.debug(
                        f"  - {poll.name}: открытие {poll.open_day} {poll.open_hour_utc:02d}:{poll.open_minute_utc:02d} UTC, "
                        f"закрытие {poll.close_day} {poll.close_hour_utc:02d}:{poll.close_minute_utc:02d} UTC"
                    )
        except FileNotFoundError:
            # Используем дефолтный уровень для ошибки
            logging.basicConfig(level=logging.INFO, force=True, format=LOG_FORMAT)
            logging.exception(
                f"❌ Файл конфигурации не найден: {config_path}. "
                f"Убедитесь, что файл config.json существует в корне проекта."
            )
            raise
        except json.JSONDecodeError:
            logging.basicConfig(level=logging.INFO, force=True, format=LOG_FORMAT)
            logging.exception(
                f"❌ Ошибка парсинга JSON в файле {config_path}. "
                f"Проверьте синтаксис файла конфигурации."
            )
            raise
        except (ValueError, KeyError):
            logging.basicConfig(level=logging.INFO, force=True, format=LOG_FORMAT)
            logging.exception(
                f"❌ Ошибка валидации конфигурации из {config_path}. "
                f"Проверьте, что все обязательные поля присутствуют и имеют корректные значения."
            )
            raise
    return _config


# Загружаем конфигурацию при импорте (если config.json существует)
config_file = Path(__file__).parent.parent / "config.json"
if config_file.exists():
    config = get_config()

    # Экспортируем переменные для обратной совместимости
    TOKEN: str = config.telegram_token
    CHAT_ID: int = config.chat_id
    ADMIN_USERNAME: str = config.admin_username
    POLLS_SCHEDULE: list[PollSchedule] = config.polls
    REQUIRED_PLAYERS: int = config.required_players
    POLL_OPTIONS: tuple[str, ...] = tuple(config.poll_options)
    SCHEDULER_TIMEZONE: str = config.scheduler_timezone
    LOG_LEVEL: str = config.log_level

    # Webhook настройки
    WEBHOOK_HOST: str = config.webhook_host
    WEBHOOK_PATH: str = config.webhook_path
    WEBHOOK_PORT: int = config.webhook_port
    WEBHOOK_SSL_CERT: str = config.ssl_cert_path
    WEBHOOK_SSL_PRIV: str = config.ssl_key_path

    # Формируем полный URL webhook
    WEBHOOK_URL: str = ""
    if WEBHOOK_HOST:
        from urllib.parse import urlparse

        parsed = urlparse(WEBHOOK_HOST)
        if not parsed.port and WEBHOOK_PORT != 443:
            host_with_port: str = f"{parsed.scheme}://{parsed.netloc}:{WEBHOOK_PORT}"
            WEBHOOK_URL = f"{host_with_port}{WEBHOOK_PATH}"
        else:
            WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
else:
    # Для тестов - используем значения по умолчанию
    TOKEN = "test_token"
    CHAT_ID = -1001234567890
    ADMIN_USERNAME = "test_admin"
    POLLS_SCHEDULE = []
    REQUIRED_PLAYERS = 18
    POLL_OPTIONS = ("Да", "Нет")
    SCHEDULER_TIMEZONE = "UTC"
    LOG_LEVEL = "INFO"
    WEBHOOK_HOST = ""
    WEBHOOK_PATH = "/webhook"
    WEBHOOK_PORT = 8443
    WEBHOOK_SSL_CERT = "/app/certs/fullchain.pem"
    WEBHOOK_SSL_PRIV = "/app/certs/privkey.pem"
    WEBHOOK_URL = ""
