# Инструкция по деплою бота

## Предварительные требования

1. **Установите Docker на сервере:**
   ```bash
   # Для Ubuntu/Debian
   curl -fsSL https://get.docker.com -o get-docker.sh
   sudo sh get-docker.sh
   
   # Проверьте установку
   docker --version
   ```

2. **Подготовьте файлы на сервере:**
   - Скопируйте все файлы проекта на сервер
   - Убедитесь, что `config.json` содержит правильные данные
   - У вас должен быть настроен домен с SSL сертификатом Let's Encrypt

## Настройка Webhook (рекомендуется)

Бот поддерживает работу через webhook, что более эффективно, чем long polling.

### Шаг 1: Настройка Nginx как Reverse Proxy

Установите и настройте Nginx для проксирования запросов к боту:

```bash
# Установка Nginx (если еще не установлен)
sudo apt update
sudo apt install nginx

# Создайте конфигурацию для вашего домена
sudo nano /etc/nginx/sites-available/bot_itv
```

Добавьте следующую конфигурацию (замените `yourdomain.com` на ваш домен):

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    
    # Редирект на HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    # Пути к сертификатам Let's Encrypt
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    # SSL настройки
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Проксирование на бота
    location /webhook {
        proxy_pass https://localhost:8443;
        proxy_ssl_verify off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активируйте конфигурацию:

```bash
sudo ln -s /etc/nginx/sites-available/bot_itv /etc/nginx/sites-enabled/
sudo nginx -t  # Проверка конфигурации
sudo systemctl reload nginx
```

### Шаг 2: Обновление config.json

Обновите `config.json` с настройками webhook:

```json
{
    "telegram_token": "YOUR_TOKEN",
    "chat_id": "YOUR_CHAT_ID",
    "admin_username": "YOUR_USERNAME",
    "webhook_host": "https://yourdomain.com",
    "webhook_path": "/webhook",
    "webhook_port": 8443,
    "ssl_cert_path": "/app/certs/fullchain.pem",
    "ssl_key_path": "/app/certs/privkey.pem"
}
```

**Важно:** Замените `yourdomain.com` на ваш реальный домен.

### Шаг 3: Подготовка сертификатов для контейнера

Скопируйте сертификаты Let's Encrypt в директорию проекта:

```bash
# Создайте директорию для сертификатов
mkdir -p /path/to/bot_itv/certs

# Скопируйте сертификаты (замените yourdomain.com на ваш домен)
sudo cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem /path/to/bot_itv/certs/
sudo cp /etc/letsencrypt/live/yourdomain.com/privkey.pem /path/to/bot_itv/certs/

# Установите правильные права доступа
sudo chmod 644 /path/to/bot_itv/certs/fullchain.pem
sudo chmod 600 /path/to/bot_itv/certs/privkey.pem
sudo chown $USER:$USER /path/to/bot_itv/certs/*
```

**Примечание:** При обновлении сертификатов Let's Encrypt (обычно автоматически через certbot) вам нужно будет скопировать их заново и перезапустить контейнер.

## Способ 1: Прямой запуск через Docker

### Шаг 1: Соберите Docker образ
```bash
# Перейдите в директорию проекта
cd /path/to/bot_itv

# Соберите образ
docker build -t bot_itv:latest .
```

### Шаг 2: Запустите контейнер

**Для webhook режима:**
```bash
# Запуск с монтированием сертификатов и пробросом порта
docker run -d \
  --name bot_itv \
  --restart unless-stopped \
  -p 8443:8443 \
  -v $(pwd)/certs:/app/certs:ro \
  -v $(pwd)/config.json:/app/config.json:ro \
  bot_itv:latest
```

**Для polling режима (fallback, если webhook не настроен):**
```bash
# Запуск в фоновом режиме с автоперезапуском
docker run -d \
  --name bot_itv \
  --restart unless-stopped \
  bot_itv:latest
```

### Шаг 3: Проверьте работу
```bash
# Просмотр логов
docker logs -f bot_itv

# Проверка статуса
docker ps | grep bot_itv
```

### Управление контейнером
```bash
# Остановить
docker stop bot_itv

# Запустить
docker start bot_itv

# Перезапустить
docker restart bot_itv

# Удалить контейнер
docker rm bot_itv

# Удалить образ
docker rmi bot_itv:latest
```

## Способ 2: Использование Docker Compose (рекомендуется)

Создайте файл `docker-compose.yml`:

```yaml
version: '3.8'

services:
  bot:
    build: .
    container_name: bot_itv
    restart: unless-stopped
    ports:
      - "8443:8443"  # Порт для webhook
    volumes:
      - ./config.json:/app/config.json:ro  # Для возможности обновления конфига без пересборки
      - ./certs:/app/certs:ro  # Монтирование SSL сертификатов
```

Запуск:
```bash
docker-compose up -d
```

Просмотр логов:
```bash
docker-compose logs -f
```

## Обновление бота

1. Остановите контейнер:
   ```bash
   docker stop bot_itv
   ```

2. Удалите старый контейнер:
   ```bash
   docker rm bot_itv
   ```

3. Обновите файлы (например, через git pull)

4. Пересоберите образ:
   ```bash
   docker build -t bot_itv:latest .
   ```

5. Запустите новый контейнер:
   ```bash
   docker run -d --name bot_itv --restart unless-stopped bot_itv:latest
   ```

Или с Docker Compose:
```bash
docker-compose up -d --build
```

## Важные замечания

1. **Безопасность:** Не коммитьте `config.json` с реальными токенами в Git
2. **Логи:** Регулярно проверяйте логи на наличие ошибок
3. **Время:** Убедитесь, что на сервере правильно настроено время (важно для планировщика)
4. **Сеть:** Убедитесь, что сервер имеет доступ к интернету для работы с Telegram API

## Решение проблем

### Бот не запускается
```bash
# Проверьте логи
docker logs bot_itv

# Проверьте конфигурацию
docker exec bot_itv cat /app/config.json
```

### Ошибки подключения
- Проверьте правильность токена в `config.json`
- Убедитесь, что `chat_id` указан правильно (с минусом для групп)

### Конфликт имени контейнера
Если при запуске появляется ошибка "The container name is already in use":
```bash
# Остановите и удалите старый контейнер
docker stop bot_itv
docker rm bot_itv

# Затем запустите новый контейнер
docker run -d --name bot_itv --restart unless-stopped bot_itv:latest
```

### Проблемы со временем
- Проверьте часовой пояс сервера: `date`
- При необходимости измените `hour` и `minute` в `bot.py`

### Проблемы с Webhook

**Webhook не устанавливается:**
```bash
# Проверьте логи бота
docker logs bot_itv

# Проверьте, что сертификаты доступны в контейнере
docker exec bot_itv ls -la /app/certs/

# Проверьте, что домен доступен извне
curl -I https://yourdomain.com/webhook
```

**Ошибки SSL:**
- Убедитесь, что сертификаты скопированы в директорию `certs/`
- Проверьте права доступа к файлам сертификатов
- Убедитесь, что пути в `config.json` соответствуют путям в контейнере

**Nginx не проксирует запросы:**
```bash
# Проверьте логи Nginx
sudo tail -f /var/log/nginx/error.log

# Проверьте конфигурацию Nginx
sudo nginx -t

# Перезагрузите Nginx
sudo systemctl reload nginx
```

**Проверка статуса webhook:**
Вы можете проверить статус webhook через Telegram Bot API:
```bash
curl https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo
```

**Переключение обратно на polling:**
Если нужно временно переключиться на polling, просто удалите или закомментируйте `webhook_host` в `config.json`:
```json
{
    "telegram_token": "...",
    "chat_id": "...",
    "admin_username": "..."
    // "webhook_host": ""  // Закомментируйте или удалите эту строку
}
```
Затем перезапустите контейнер.


