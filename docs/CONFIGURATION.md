# Руководство по конфигурации VolleyBot

Этот документ описывает все доступные параметры конфигурации бота и способы их настройки.

## Структура конфигурации

Бот использует файл `config.json` в корневой директории проекта для настройки всех параметров.

### Создание конфигурации

1. Скопируйте пример конфигурации:

   ```bash
   cp config.example.json config.json
   ```

2. Отредактируйте `config.json` под ваши нужды

3. **Важно**: Не коммитьте `config.json` в git (файл уже в `.gitignore`)

## Параметры конфигурации

### Обязательные параметры Telegram

#### `telegram_token` (string, обязательно)

Токен вашего Telegram бота, полученный от [@BotFather](https://t.me/BotFather).

```json
{
  "telegram_token": "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
}
```

**Получение токена:**

1. Напишите [@BotFather](https://t.me/BotFather)
2. Отправьте `/newbot`
3. Следуйте инструкциям
4. Скопируйте полученный токен

#### `chat_id` (integer, обязательно)

ID чата или группы, куда бот будет отправлять опросы.

```json
{
  "chat_id": -1001234567890
}
```

**Получение chat_id:**

Для группы:

1. Добавьте бота в группу
2. Отправьте команду `/start` в группе
3. Бот покажет ID группы
4. Для групп ID всегда отрицательный и начинается с `-100`

Для личных сообщений:

1. Напишите боту `/start`
2. ID будет положительным числом

#### `admin_username` (string, опционально)

Username администратора бота (без @). Используется для проверки прав доступа к командам.

```json
{
  "admin_username": "your_username"
}
```

### Настройки Webhook (опционально)

Для production окружения можно использовать webhook вместо polling.

#### `webhook_host` (string, по умолчанию: "")

URL вашего сервера для webhook. Если пустой, бот работает в режиме polling.

```json
{
  "webhook_host": "https://yourdomain.com"
}
```

**Требования:**

- HTTPS обязателен
- Должен быть доступен из интернета
- Нужны валидные SSL сертификаты

#### `webhook_path` (string, по умолчанию: "/webhook")

Путь для webhook endpoint.

```json
{
  "webhook_path": "/webhook"
}
```

#### `webhook_port` (integer, по умолчанию: 8443)

Порт для webhook сервера. Telegram поддерживает: 443, 80, 88, 8443.

```json
{
  "webhook_port": 8443
}
```

### SSL сертификаты (для webhook)

#### `ssl_cert_path` (string)

Путь к SSL сертификату (fullchain).

```json
{
  "ssl_cert_path": "/app/certs/fullchain.pem"
}
```

#### `ssl_key_path` (string)

Путь к приватному ключу SSL.

```json
{
  "ssl_key_path": "/app/certs/privkey.pem"
}
```

### Настройки опросов

#### `required_players` (integer, по умолчанию: 18)

Необходимое количество игроков для формирования основного состава.

```json
{
  "required_players": 18
}
```

При достижении этого числа:

- Остальные игроки попадают в запасные
- Формируется итоговый список

#### `poll_options` (array, по умолчанию: ["Да", "Нет"])

Варианты ответов в опросе.

```json
{
  "poll_options": ["Да", "Нет"]
}
```

Можно изменить на другие варианты:

```json
{
  "poll_options": ["Иду", "Не иду", "Под вопросом"]
}
```

### Настройки планировщика

#### `scheduler_timezone` (string, по умолчанию: "UTC")

Таймзона для планировщика задач.

```json
{
  "scheduler_timezone": "UTC"
}
```

**Доступные таймзоны:**

- `UTC` - всемирное время (рекомендуется)
- `Europe/Moscow` - московское время
- `Europe/Kiev` - киевское время
- Любая из [IANA таймзон](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)

**Рекомендация:** Используйте UTC и указывайте время в опросах в UTC. Это упрощает настройку при смене часовых поясов.

### Настройки логирования

#### `log_level` (string, по умолчанию: "INFO")

Уровень детализации логов.

```json
{
  "log_level": "INFO"
}
```

**Доступные уровни:**

- `DEBUG` - максимально подробные логи (для отладки)
- `INFO` - информационные сообщения (рекомендуется)
- `WARNING` - только предупреждения и ошибки
- `ERROR` - только ошибки и критические события
- `CRITICAL` - только критические события

### Расписание опросов

#### `polls` (array)

Список опросов с расписанием открытия и закрытия.

```json
{
  "polls": [
    {
      "name": "wednesday_poll",
      "message": "Волейбол в среду в 18:00",
      "open_day": "tue",
      "open_hour_utc": 15,
      "open_minute_utc": 0,
      "close_day": "wed",
      "close_hour_utc": 12,
      "close_minute_utc": 0,
      "subs": []
    }
  ]
}
```

**Параметры опроса:**

##### `name` (string, обязательно)

Уникальное имя опроса (используется внутри системы).

##### `message` (string, обязательно)

Текст вопроса, который увидят пользователи.

##### `open_day` (string, по умолчанию: "\*")

День недели для открытия опроса.

**Допустимые значения:**

- `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` - конкретный день
- `*` - каждый день

##### `open_hour_utc` (integer, 0-23, по умолчанию: 0)

Час открытия опроса в UTC.

##### `open_minute_utc` (integer, 0-59, по умолчанию: 0)

Минута открытия опроса в UTC.

##### `close_day` (string, по умолчанию: "\*")

День недели для закрытия опроса.

##### `close_hour_utc` (integer, 0-23, по умолчанию: 0)

Час закрытия опроса в UTC.

##### `close_minute_utc` (integer, 0-59, по умолчанию: 0)

Минута закрытия опроса в UTC.

##### `subs` (array, опционально)

Список Telegram ID пользователей для уведомлений.

```json
{
  "subs": [123456789, 987654321]
}
```

## Примеры конфигурации

### Минимальная конфигурация (polling)

```json
{
  "telegram_token": "YOUR_TOKEN",
  "chat_id": -1001234567890,
  "admin_username": "admin",
  "polls": []
}
```

### Конфигурация с одним опросом

```json
{
  "telegram_token": "YOUR_TOKEN",
  "chat_id": -1001234567890,
  "admin_username": "admin",
  "required_players": 18,
  "polls": [
    {
      "name": "wednesday_volleyball",
      "message": "Волейбол в среду в 18:00. Записываемся!",
      "open_day": "tue",
      "open_hour_utc": 15,
      "open_minute_utc": 0,
      "close_day": "wed",
      "close_hour_utc": 12,
      "close_minute_utc": 0,
      "subs": []
    }
  ]
}
```

### Production конфигурация с webhook

```json
{
  "telegram_token": "YOUR_TOKEN",
  "chat_id": -1001234567890,
  "admin_username": "admin",

  "webhook_host": "https://yourdomain.com",
  "webhook_path": "/webhook",
  "webhook_port": 8443,
  "ssl_cert_path": "/app/certs/fullchain.pem",
  "ssl_key_path": "/app/certs/privkey.pem",

  "required_players": 18,
  "scheduler_timezone": "UTC",
  "log_level": "INFO",

  "polls": [
    {
      "name": "wednesday_poll",
      "message": "Волейбол в среду в 18:00",
      "open_day": "tue",
      "open_hour_utc": 15,
      "open_minute_utc": 0,
      "close_day": "wed",
      "close_hour_utc": 12,
      "close_minute_utc": 0,
      "subs": []
    },
    {
      "name": "sunday_poll",
      "message": "Волейбол в воскресенье в 10:00",
      "open_day": "fri",
      "open_hour_utc": 18,
      "open_minute_utc": 0,
      "close_day": "sun",
      "close_hour_utc": 6,
      "close_minute_utc": 0,
      "subs": []
    }
  ]
}
```

## Работа со временем

### Конвертация времени из МСК в UTC

Московское время (МСК) = UTC + 3 часа

**Примеры:**

- 18:00 МСК = 15:00 UTC
- 21:00 МСК = 18:00 UTC
- 09:00 МСК = 06:00 UTC

**Калькулятор:**

```
UTC_час = МСК_час - 3
```

Если результат отрицательный, добавьте 24 и сдвиньте день назад:

```
03:00 МСК = 00:00 UTC (тот же день)
02:00 МСК = 23:00 UTC (предыдущий день)
01:00 МСК = 22:00 UTC (предыдущий день)
```

### Пример настройки опроса на среду

**Требование:** Открыть опрос во вторник в 18:00 МСК, закрыть в среду в 15:00 МСК

**Конфигурация:**

```json
{
  "name": "wednesday_game",
  "message": "Волейбол в среду в 18:00",
  "open_day": "tue",
  "open_hour_utc": 15,
  "open_minute_utc": 0,
  "close_day": "wed",
  "close_hour_utc": 12,
  "close_minute_utc": 0,
  "subs": []
}
```

## Переменные окружения

Для безопасности можно использовать переменные окружения вместо хранения токена в файле.

### Будущая поддержка (в разработке)

```bash
export VOLLEYBOT_TOKEN="your_token_here"
export VOLLEYBOT_CHAT_ID="-1001234567890"
```

**Примечание:** Текущая версия требует `config.json`. Поддержка ENV планируется в будущих релизах.

## Безопасность

### Важные правила

1. **Никогда не коммитьте `config.json` в git**
   - Файл уже в `.gitignore`
   - Проверьте перед коммитом: `git status`

2. **Используйте `config.example.json` как шаблон**
   - Для новых участников проекта
   - Для документации

3. **Храните токены в безопасности**
   - Не передавайте токен третьим лицам
   - Регулярно обновляйте токен через [@BotFather](https://t.me/BotFather)
   - При компрометации немедленно сбросьте токен

4. **Ограничьте права доступа к файлу**

   ```bash
   chmod 600 config.json
   ```

5. **Для production используйте secret management**
   - Docker secrets
   - Kubernetes secrets
   - Vault
   - AWS Secrets Manager

## Валидация конфигурации

Бот автоматически валидирует конфигурацию при запуске с помощью Pydantic.

### Проверка конфигурации

```bash
# Попробуйте запустить бота
python -m src.bot

# Или используйте скрипт
./manage.ps1 start  # Windows
./manage.sh start   # Linux/macOS
```

### Типичные ошибки валидации

**Ошибка:** `telegram_token field required`

```
Решение: Добавьте "telegram_token" в config.json
```

**Ошибка:** `chat_id field required`

```
Решение: Добавьте "chat_id" в config.json
```

**Ошибка:** `День должен быть одним из {mon, tue, wed, ...}`

```
Решение: Используйте трехбуквенные сокращения дней недели
```

**Ошибка:** `webhook_host должен начинаться с http:// или https://`

```
Решение: Добавьте протокол: "https://yourdomain.com"
```

**Ошибка:** `log_level должен быть одним из {DEBUG, INFO, ...}`

```
Решение: Используйте один из допустимых уровней (в верхнем регистре)
```

## См. также

- [README.md](../README.md) - основная документация
- [config.example.json](../config.example.json) - пример конфигурации
