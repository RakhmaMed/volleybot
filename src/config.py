"""Загрузка конфигурации и константы приложения."""

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO)

# Загрузка конфигурации
# Путь к config.json относительно корня проекта
config_path = Path(__file__).parent.parent / "config.json"
with open(config_path, encoding="utf-8") as f:
    config: dict[str, Any] = json.load(f)

# Telegram настройки
TOKEN: str = config["telegram_token"]
CHAT_ID: int = int(config["chat_id"])
ADMIN_USERNAME: str = config.get("admin_username", "TwinkleDev55")
POLLS_SCHEDULE: list[dict[str, Any]] = config.get("polls", [])

# Webhook настройки
WEBHOOK_HOST: str = config.get("webhook_host", "")
WEBHOOK_PATH: str = config.get("webhook_path", "/webhook")
WEBHOOK_PORT: int = config.get("webhook_port", 8443)

# Формируем полный URL webhook
WEBHOOK_URL: str
if WEBHOOK_HOST:
    parsed = urlparse(WEBHOOK_HOST)
    if not parsed.port and WEBHOOK_PORT != 443:
        host_with_port: str = f"{parsed.scheme}://{parsed.netloc}:{WEBHOOK_PORT}"
        WEBHOOK_URL = f"{host_with_port}{WEBHOOK_PATH}"
    else:
        WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
else:
    WEBHOOK_URL = ""

# SSL сертификаты
WEBHOOK_SSL_CERT: str = config.get("ssl_cert_path", "/app/certs/fullchain.pem")
WEBHOOK_SSL_PRIV: str = config.get("ssl_key_path", "/app/certs/privkey.pem")

# Настройки опросов
REQUIRED_PLAYERS: int = 18
POLL_OPTIONS: list[str] = ["Да", "Нет"]
