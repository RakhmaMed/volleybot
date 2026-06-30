FROM python:3.14-slim

# Берем готовый бинарь uv из официального образа, чтобы не ставить его через curl/pip
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /bin/

# Устанавливаем рабочую директорию
WORKDIR /app

# Не устанавливаем dev-группу зависимостей в production-образ
ENV UV_NO_DEV=1
# Копируем пакеты из кэша uv в .venv, чтобы окружение не зависело от cache mount
ENV UV_LINK_MODE=copy
# Делаем виртуальное окружение uv основным Python-окружением контейнера
ENV PATH="/app/.venv/bin:$PATH"

# Сначала копируем только описание зависимостей, чтобы Docker кэшировал их установку
COPY pyproject.toml uv.lock /app/

# Устанавливаем только runtime-зависимости из lock-файла в /app/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

# После установки зависимостей копируем код приложения
COPY . /app

# Создаем директории для сертификатов и базы данных
RUN mkdir -p /app/certs /app/data

# Открываем порт для webhook
EXPOSE 8443

# Запускаем бота
CMD ["python", "-m", "src.bot"]
