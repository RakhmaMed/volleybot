FROM python:3.10

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта
COPY . /app

# Очищаем ненужные папки (как в install-python.sh)
RUN find . -type d -name "__MACOSX" -prune -exec rm -rf {} + 2>/dev/null || true && \
    find . -type d -name ".vs" -prune -exec rm -rf {} + 2>/dev/null || true && \
    find . -type d -name ".idea" -prune -exec rm -rf {} + 2>/dev/null || true && \
    find . -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true && \
    find . -type d -name "venv" -prune -exec rm -rf {} + 2>/dev/null || true

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Создаем директорию для сертификатов
RUN mkdir -p /app/certs

# Открываем порт для webhook
EXPOSE 8443

# Запускаем бота напрямую
CMD ["python", "bot.py"]