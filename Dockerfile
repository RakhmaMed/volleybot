FROM python:3.12-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Сначала копируем только requirements.txt для кэширования зависимостей
COPY requirements.txt /app/

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы проекта
COPY . /app

# Создаем директорию для сертификатов
RUN mkdir -p /app/certs

# Открываем порт для webhook
EXPOSE 8443

# Запускаем бота
CMD ["python", "bot.py"]
