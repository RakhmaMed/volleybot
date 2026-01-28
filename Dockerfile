FROM python:3.13-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Сначала копируем только requirements.txt для кэширования зависимостей
COPY requirements.txt /app/

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы проекта
COPY . /app

# Создаем директории для сертификатов и базы данных
RUN mkdir -p /app/certs /app/data

# Открываем порт для webhook
EXPOSE 8443

# Запускаем бота
CMD ["python", "-m", "src.bot"]
