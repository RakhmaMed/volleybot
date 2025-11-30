FROM python:3.10

# Устанавливаем рабочую директорию
WORKDIR /app

# Сначала копируем только requirements.txt для кэширования зависимостей
# Это позволяет переиспользовать слой с установленными пакетами при изменении кода
COPY requirements.txt /app/

# Устанавливаем зависимости Python
# Этот слой будет кэшироваться, если requirements.txt не изменился
RUN pip install --no-cache-dir -r requirements.txt

# Теперь копируем остальные файлы проекта
# При изменении bot.py, config.json и т.д. пересоберётся только этот слой
COPY . /app

# Создаем директорию для сертификатов
RUN mkdir -p /app/certs

# Открываем порт для webhook
EXPOSE 8443

# Запускаем бота напрямую
CMD ["python", "bot.py"]