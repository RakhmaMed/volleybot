import datetime
from datetime import timezone
import logging
from aiogram import Bot, Dispatcher, executor, types
import asyncio
import json
import ssl
from aiohttp import web

logging.basicConfig(level=logging.INFO)

weekday_map = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье"
}

with open("config.json") as f:
    config = json.load(f)

TOKEN = config["telegram_token"]
CHAT_ID = config["chat_id"]
ADMIN_USERNAME = config.get("admin_username", "TwinkleDev55")  # Username администратора

# Webhook настройки
WEBHOOK_HOST = config.get("webhook_host", "")  # Например: https://yourdomain.com
WEBHOOK_PATH = config.get("webhook_path", "/webhook")  # Путь для webhook
WEBHOOK_PORT = config.get("webhook_port", 8443)  # Порт для webhook

# Формируем полный URL webhook
# Если порт не стандартный (443), добавляем его в URL
if WEBHOOK_HOST:
    from urllib.parse import urlparse
    parsed = urlparse(WEBHOOK_HOST)
    # Если порт не указан и не стандартный HTTPS порт, добавляем его
    if not parsed.port and WEBHOOK_PORT != 443:
        # Добавляем порт к hostname
        host_with_port = f"{parsed.scheme}://{parsed.netloc}:{WEBHOOK_PORT}"
        WEBHOOK_URL = f"{host_with_port}{WEBHOOK_PATH}"
    else:
        WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
else:
    WEBHOOK_URL = ""

# Пути к SSL сертификатам
WEBHOOK_SSL_CERT = config.get("ssl_cert_path", "/app/certs/fullchain.pem")
WEBHOOK_SSL_PRIV = config.get("ssl_key_path", "/app/certs/privkey.pem")
MSG1 = "Волейбол завтра (среда) в 18:00"
MSG2 = "Волейбол завтра (пятница) в 19:00"
TWO_DAY = 60 * 60 * 24 * 2 - 120
THREE_DAY = 60 * 60 * 24 * 5 - 120
REQUIRED_PLAYERS = 18
POLL_OPTIONS = ["Да", "Нет"]

bot = Bot(token=TOKEN)
dp = Dispatcher(bot=bot)

poll_data = {}
bot_enabled = True  # Состояние бота (включен/выключен) 

chat_id = CHAT_ID
# Время создания опросов в UTC (Московское время = UTC+3)
# Вторник 15:00 UTC = 18:00 MSK (опрос для среды)
# Четверг 16:00 UTC = 19:00 MSK (опрос для пятницы)
TUESDAY_HOUR_UTC = 15  # 18:00 MSK
THURSDAY_HOUR_UTC = 16  # 19:00 MSK
MINUTE = 0

def is_admin(user: types.User) -> bool:
    """Проверяет, является ли пользователь администратором"""
    username = user.username
    if not username:
        return False
    # Убираем @ если есть в начале
    admin_username_clean = ADMIN_USERNAME.replace("@", "")
    username_clean = username.replace("@", "")
    return username_clean == admin_username_clean

@dp.message_handler(commands=['start'])
async def start_bot_handler(message: types.Message):
    """Команда для включения бота (только для администратора)"""
    global bot_enabled
    
    user = message.from_user
    if not is_admin(user):
        await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
        logging.warning(f"Попытка использования /start от неавторизованного пользователя: @{user.username} (ID: {user.id})")
        return
    
    if bot_enabled:
        await message.reply("✅ Бот уже включен и работает.")
        logging.info(f"Бот уже включен. Команда от администратора @{user.username}")
    else:
        bot_enabled = True
        await message.reply("✅ Бот включен. Опросы будут создаваться по расписанию.")
        logging.info(f"Бот включен администратором @{user.username} (ID: {user.id})")

@dp.message_handler(commands=['stop'])
async def stop_bot_handler(message: types.Message):
    """Команда для выключения бота (только для администратора)"""
    global bot_enabled
    
    user = message.from_user
    if not is_admin(user):
        await message.reply("Ты кто? Я тебя не знаю. Кыш-кыш-кыш")
        logging.warning(f"Попытка использования /stop от неавторизованного пользователя: @{user.username} (ID: {user.id})")
        return
    
    if not bot_enabled:
        await message.reply("⚠️ Бот уже выключен.")
        logging.info(f"Бот уже выключен. Команда от администратора @{user.username}")
    else:
        bot_enabled = False
        await message.reply("⏸️ Бот выключен. Опросы не будут создаваться до включения.")
        logging.info(f"Бот выключен администратором @{user.username} (ID: {user.id})")

async def send_poll():
    global poll_data 
    now = datetime.datetime.now(timezone.utc)
    msg = MSG1 if now.weekday() == 1 else MSG2  # Вторник -> MSG1 (среда), Четверг -> MSG2 (пятница)
    poll_data.clear()

    poll_message = await bot.send_poll(
        chat_id=chat_id,
        question=msg,
        options=POLL_OPTIONS,
        is_anonymous=False
    )

    info_message = await bot.send_message(chat_id=chat_id, text="⏳ Идёт сбор голосов...")

    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=poll_message.message_id)
    except Exception as e:
        logging.warning(f"Не удалось закрепить сообщение: {e}")

    poll_data[poll_message.poll.id] = {
        'chat_id': chat_id,
        'poll_msg_id': poll_message.message_id,
        'info_msg_id': info_message.message_id,
        'yes_voters': []
    }

    logging.info(f"Создан опрос {poll_message.poll.id}")

@dp.poll_answer_handler()
async def handle_poll_answer(poll_answer: types.PollAnswer):
    global poll_data
    poll_id = poll_answer.poll_id
    user = poll_answer.user
    selected = poll_answer.option_ids
    logging.info(f"Получен новый ответ от пользователя {user.username} (ID: {user.id}), голос: {selected}")
    if poll_id not in poll_data:
        return

    data = poll_data[poll_id]
    yes_voters = data['yes_voters']

    # Удаляем пользователя, если был
    yes_voters = [v for v in yes_voters if v['id'] != user.id]

    if 0 in selected:  # Да
        name = f"@{user.username}" if user.username else user.full_name
        yes_voters.append({'id': user.id, 'name': name})

    data['yes_voters'] = yes_voters
    logging.info(f"Обновленный список голосующих: {yes_voters}")
    if len(yes_voters) >= REQUIRED_PLAYERS:
        main_players = yes_voters[:REQUIRED_PLAYERS]
        reserves = yes_voters[REQUIRED_PLAYERS:]

        text = "✅ *Список игроков:*\n"
        text += '\n'.join(f"{i + 1}) {p['name']}" for i, p in enumerate(main_players))

        if reserves:
            text += "\n\n🕗 *Запасные игроки:*\n"
            text += '\n'.join(f"{i + 1}) {p['name']}" for i, p in enumerate(reserves))

        try:
            await bot.edit_message_text(
                chat_id=data['chat_id'],
                message_id=data['info_msg_id'],
                text=text
            )
        except Exception as e:
            logging.warning(f"Ошибка редактирования сообщения: {e}")

async def scheduler():
    global bot_enabled
    while True:
        now = datetime.datetime.now(timezone.utc)
        weekday = now.weekday()
        hour_utc = now.hour
        minute_utc = now.minute
        
        status = "🟢 ВКЛ" if bot_enabled else "🔴 ВЫКЛ"
        logging.info(f"Время UTC: {hour_utc:02d}:{minute_utc:02d}, {weekday_map[weekday]}, Бот: {status}")
        
        if chat_id is not None and bot_enabled:
            # Вторник 15:00 UTC (18:00 MSK) - опрос для среды
            if weekday == 1 and hour_utc == TUESDAY_HOUR_UTC and minute_utc == MINUTE:
                logging.warning(f"Создание опроса для среды (вторник 15:00 UTC / 18:00 MSK)")
                await send_poll()
                await asyncio.sleep(TWO_DAY)
            # Четверг 16:00 UTC (19:00 MSK) - опрос для пятницы
            elif weekday == 3 and hour_utc == THURSDAY_HOUR_UTC and minute_utc == MINUTE:
                logging.warning(f"Создание опроса для пятницы (четверг 16:00 UTC / 19:00 MSK)")
                await send_poll()
                await asyncio.sleep(THREE_DAY)
        elif not bot_enabled:
            # Если бот выключен, просто ждем
            pass

        await asyncio.sleep(50)

async def on_startup(dp: Dispatcher):
    """Выполняется при запуске бота"""
    loop = asyncio.get_event_loop()
    loop.create_task(scheduler())
    
    # Устанавливаем webhook
    if WEBHOOK_HOST:
        try:
            await bot.set_webhook(WEBHOOK_URL)
            logging.info(f"Webhook установлен: {WEBHOOK_URL}")
        except Exception as e:
            logging.error(f"Ошибка установки webhook: {e}")
    else:
        logging.warning("WEBHOOK_HOST не указан в config.json, используется polling")

async def on_shutdown(dp: Dispatcher):
    """Выполняется при остановке бота"""
    logging.info("Остановка бота...")
    if WEBHOOK_HOST:
        await bot.delete_webhook()
        logging.info("Webhook удален")
    await bot.session.close()

if __name__ == "__main__":
    if WEBHOOK_HOST:
        # Режим webhook
        logging.info("Запуск бота в режиме webhook")
        
        # Настройка SSL
        ssl_context = None
        try:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(WEBHOOK_SSL_CERT, WEBHOOK_SSL_PRIV)
            logging.info("SSL сертификаты загружены")
        except FileNotFoundError as e:
            logging.error(f"Не удалось загрузить SSL сертификаты: {e}")
            logging.error("Убедитесь, что сертификаты находятся в правильных путях")
            exit(1)
        except Exception as e:
            logging.error(f"Ошибка при загрузке SSL сертификатов: {e}")
            exit(1)
        
        # Запуск webhook сервера
        executor.start_webhook(
            dispatcher=dp,
            webhook_path=WEBHOOK_PATH,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            skip_updates=True,
            host="0.0.0.0",
            port=WEBHOOK_PORT,
            ssl_context=ssl_context
        )
    else:
        # Режим polling (fallback)
        logging.info("Запуск бота в режиме polling (WEBHOOK_HOST не указан)")
        loop = asyncio.get_event_loop()
        loop.create_task(scheduler())
        executor.start_polling(dp, skip_updates=True)
