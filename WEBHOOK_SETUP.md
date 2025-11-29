# Пошаговая инструкция по настройке Webhook для twinkledev.ru

## Вариант 1: Без Nginx (проще, рекомендуется для начала)

### Шаг 1: Подготовка сертификатов на сервере

```bash
# 1. Подключитесь к серверу по SSH
ssh user@twinkledev.ru

# 2. Перейдите в директорию проекта
cd /path/to/bot_itv  # Замените на реальный путь

# 3. Создайте директорию для сертификатов
mkdir -p certs

# 4. Скопируйте сертификаты Let's Encrypt
# (замените путь, если ваши сертификаты в другом месте)
sudo cp /etc/letsencrypt/live/twinkledev.ru/fullchain.pem ./certs/
sudo cp /etc/letsencrypt/live/twinkledev.ru/privkey.pem ./certs/

# 5. Установите правильные права доступа
sudo chmod 644 ./certs/fullchain.pem
sudo chmod 600 ./certs/privkey.pem
sudo chown $USER:$USER ./certs/*
```

### Шаг 2: Проверка config.json

Убедитесь, что `config.json` содержит правильные настройки:

```json
{
    "telegram_token": "YOUR-TOKEN",
    "chat_id": "YOUR-CHAR",
    "admin_username": "TwinkleDev55",
    "webhook_host": "https://twinkledev.ru",
    "webhook_path": "/volley_webhook",
    "webhook_port": 8443,
    "ssl_cert_path": "/app/certs/fullchain.pem",
    "ssl_key_path": "/app/certs/privkey.pem"
}
```

### Шаг 3: Настройка файрвола

Откройте порт 8443 в файрволе:

```bash
# Для UFW (Ubuntu)
sudo ufw allow 8443/tcp

# Для firewalld (CentOS/RHEL)
sudo firewall-cmd --permanent --add-port=8443/tcp
sudo firewall-cmd --reload

# Проверьте, что порт открыт
sudo ufw status  # или sudo firewall-cmd --list-ports
```

### Шаг 4: Сборка и запуск Docker контейнера

```bash
# 1. Перейдите в директорию проекта
cd /path/to/bot_itv

# 2. Соберите Docker образ
docker build -t bot_itv:latest .

# 3. Остановите старый контейнер (если запущен)
docker stop bot_itv 2>/dev/null || true
docker rm bot_itv 2>/dev/null || true

# 4. Запустите новый контейнер с webhook
docker run -d \
  --name bot_itv \
  --restart unless-stopped \
  -p 8443:8443 \
  -v $(pwd)/certs:/app/certs:ro \
  -v $(pwd)/config.json:/app/config.json:ro \
  bot_itv:latest

# 5. Проверьте логи
docker logs -f bot_itv
```

В логах вы должны увидеть:
```
INFO: Запуск бота в режиме webhook
INFO: SSL сертификаты загружены
INFO: Webhook установлен: https://twinkledev.ru:8443/volley_webhook
```

### Шаг 5: Проверка работы webhook

```bash
# 1. Проверьте статус webhook через Telegram API
curl https://api.telegram.org/bot7787907902:AAHEusvkSpwVVIw0hLG-xocnGvJHVOrUvx4/getWebhookInfo

# Должен вернуться JSON с информацией о webhook, включая URL:
# "url": "https://twinkledev.ru:8443/volley_webhook"

# 2. Проверьте доступность webhook извне
curl -k https://twinkledev.ru:8443/volley_webhook
# Должна быть ошибка 405 или подобная (это нормально, главное что сервер отвечает)

# 3. Отправьте тестовое сообщение боту в Telegram
# Бот должен ответить (если включен через /start)
```

### Шаг 6: Настройка автообновления сертификатов

Сертификаты Let's Encrypt обновляются автоматически, но нужно обновлять их в контейнере:

```bash
# Создайте скрипт для обновления сертификатов
cat > /path/to/bot_itv/update_certs.sh << 'EOF'
#!/bin/bash
cd /path/to/bot_itv
sudo cp /etc/letsencrypt/live/twinkledev.ru/fullchain.pem ./certs/
sudo cp /etc/letsencrypt/live/twinkledev.ru/privkey.pem ./certs/
sudo chmod 644 ./certs/fullchain.pem
sudo chmod 600 ./certs/privkey.pem
sudo chown $USER:$USER ./certs/*
docker restart bot_itv
EOF

chmod +x /path/to/bot_itv/update_certs.sh

# Добавьте в cron для автоматического обновления после обновления certbot
# (certbot обычно обновляет сертификаты автоматически)
# Добавьте в /etc/crontab или через crontab -e:
# 0 3 * * * /path/to/bot_itv/update_certs.sh
```

---

## Вариант 2: С Nginx (для использования стандартного порта 443)

Если вы хотите использовать стандартный порт 443 (без указания порта в URL), используйте Nginx как reverse proxy.

### Шаг 1: Установка и настройка Nginx

```bash
# 1. Установите Nginx (если еще не установлен)
sudo apt update
sudo apt install nginx

# 2. Создайте конфигурацию
sudo nano /etc/nginx/sites-available/bot_itv
```

Добавьте следующую конфигурацию:

```nginx
server {
    listen 80;
    server_name twinkledev.ru;
    
    # Редирект на HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name twinkledev.ru;

    # Пути к сертификатам Let's Encrypt
    ssl_certificate /etc/letsencrypt/live/twinkledev.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/twinkledev.ru/privkey.pem;

    # SSL настройки
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Проксирование на бота
    location /volley_webhook {
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
# 3. Создайте символическую ссылку
sudo ln -s /etc/nginx/sites-available/bot_itv /etc/nginx/sites-enabled/

# 4. Проверьте конфигурацию
sudo nginx -t

# 5. Перезагрузите Nginx
sudo systemctl reload nginx
```

### Шаг 2: Обновите config.json

Если используете Nginx, порт в URL не нужен:

```json
{
    "webhook_host": "https://twinkledev.ru",
    "webhook_path": "/volley_webhook",
    "webhook_port": 8443,
    ...
}
```

Webhook URL будет: `https://twinkledev.ru/volley_webhook` (без порта).

### Шаг 3: Запуск контейнера

```bash
docker run -d \
  --name bot_itv \
  --restart unless-stopped \
  -p 8443:8443 \
  -v $(pwd)/certs:/app/certs:ro \
  -v $(pwd)/config.json:/app/config.json:ro \
  bot_itv:latest
```

---

## Решение проблем

### Webhook не устанавливается

```bash
# Проверьте логи
docker logs bot_itv

# Проверьте доступность порта
sudo netstat -tlnp | grep 8443

# Проверьте файрвол
sudo ufw status
```

### Ошибки SSL

```bash
# Проверьте наличие сертификатов
ls -la certs/

# Проверьте права доступа
ls -l certs/

# Проверьте содержимое сертификатов (должны быть читаемы)
head certs/fullchain.pem
```

### Бот не отвечает на сообщения

```bash
# Проверьте статус webhook
curl https://api.telegram.org/bot7787907902:AAHEusvkSpwVVIw0hLG-xocnGvJHVOrUvx4/getWebhookInfo

# Проверьте логи бота
docker logs -f bot_itv

# Убедитесь, что бот включен (отправьте /start от администратора)
```

### Проблемы с Nginx

```bash
# Проверьте логи Nginx
sudo tail -f /var/log/nginx/error.log

# Проверьте конфигурацию
sudo nginx -t

# Перезапустите Nginx
sudo systemctl restart nginx
```

---

## Переключение обратно на Polling

Если нужно временно переключиться на polling (например, для отладки):

1. Удалите или закомментируйте `webhook_host` в `config.json`:
```json
{
    "telegram_token": "...",
    "chat_id": "...",
    "admin_username": "..."
    // "webhook_host": ""  // Закомментируйте эту строку
}
```

2. Перезапустите контейнер:
```bash
docker restart bot_itv
```

Бот автоматически переключится на режим polling.

