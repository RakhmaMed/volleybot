import datetime
from datetime import timezone
import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.utils import exceptions
import asyncio
import json
import ssl
import traceback
import os
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(level=logging.INFO)

with open("config.json") as f:
    config = json.load(f)

TOKEN = config["telegram_token"]
CHAT_ID = config["chat_id"]
ADMIN_USERNAME = config.get("admin_username", "TwinkleDev55")  # Username администратора
POLLS_SCHEDULE = config.get("polls", [])  # Расписание опросов из JSON

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
REQUIRED_PLAYERS = 18
POLL_OPTIONS = ["Да", "Нет"]

bot = Bot(token=TOKEN)
dp = Dispatcher(bot=bot)

poll_data = {}
bot_enabled = True  # Состояние бота (включен/выключен)
scheduler = AsyncIOScheduler(timezone='UTC')  # Планировщик задач 

chat_id = CHAT_ID

def save_error_dump(error: Exception, poll_name: str, question: str):
    """
    Сохраняет дамп ошибки в файл рядом с исходником
    
    Args:
        error: Исключение, которое произошло
        poll_name: Название опроса
        question: Текст вопроса опроса
    """
    try:
        error_data = {
            'timestamp': datetime.datetime.now(timezone.utc).isoformat(),
            'poll_name': poll_name,
            'question': question,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': traceback.format_exc(),
            'chat_id': chat_id
        }
        
        # Получаем путь к директории, где находится bot.py
        script_dir = os.path.dirname(os.path.abspath(__file__))
        error_file = os.path.join(script_dir, 'error_dump.json')
        
        # Читаем существующие ошибки, если файл есть
        existing_errors = []
        if os.path.exists(error_file):
            try:
                with open(error_file, 'r', encoding='utf-8') as f:
                    existing_errors = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing_errors = []
        
        # Добавляем новую ошибку
        existing_errors.append(error_data)
        
        # Сохраняем обратно (оставляем последние 50 ошибок)
        with open(error_file, 'w', encoding='utf-8') as f:
            json.dump(existing_errors[-50:], f, ensure_ascii=False, indent=2)
        
        logging.info(f"Дамп ошибки сохранен в {error_file}")
    except Exception as e:
        logging.error(f"Не удалось сохранить дамп ошибки: {e}")

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

@dp.message_handler(commands=['chatid'])
async def chatid_handler(message: types.Message):
    """Команда для получения ID чата"""
    chat = message.chat
    chat_info = f"📋 *Информация о чате:*\n\n"
    chat_info += f"ID чата: `{chat.id}`\n"
    chat_info += f"Тип: {chat.type}\n"
    
    if chat.title:
        chat_info += f"Название: {chat.title}\n"
    if chat.username:
        chat_info += f"Username: @{chat.username}\n"
    
    await message.reply(chat_info, parse_mode='Markdown')
    logging.info(f"Запрос ID чата от пользователя @{message.from_user.username} (ID: {message.from_user.id}). Chat ID: {chat.id}")

async def send_poll(question: str, poll_name: str = "опрос"):
    """
    Отправка опроса в чат
    
    Args:
        question: Текст вопроса опроса
        poll_name: Название опроса для логирования
    """
    global poll_data, chat_id
    if not bot_enabled:
        logging.info(f"Бот выключен, {poll_name} не создан")
        return
    
    poll_data.clear()
    
    try:
        poll_message = await bot.send_poll(
            chat_id=chat_id,
            question=question,
            options=POLL_OPTIONS,
            is_anonymous=False
        )
    except exceptions.MigrateToChat as e:
        # Обработка миграции группы в супергруппу
        new_chat_id = e.migrate_to_chat_id
        logging.error(f"Группа мигрирована в супергруппу. Старый ID: {chat_id}, Новый ID: {new_chat_id}")
        
        # Обновляем chat_id
        chat_id = new_chat_id
        
        # Сохраняем дамп ошибки
        save_error_dump(e, poll_name, question)
        
        # Пытаемся отправить сообщение об ошибке в новый чат
        try:
            error_msg = (
                f"❌ *Ошибка при создании опроса \"{poll_name}\"*\n\n"
                f"Группа была мигрирована в супергруппу.\n"
                f"Новый ID чата: `{new_chat_id}`"
            )
            await bot.send_message(chat_id=new_chat_id, text=error_msg, parse_mode='Markdown')
        except Exception as send_error:
            logging.error(f"Не удалось отправить сообщение об ошибке: {send_error}")
        
        return
    except Exception as e:
        # Обработка всех остальных ошибок
        logging.error(f"Ошибка при создании опроса \"{poll_name}\": {e}")
        logging.error(traceback.format_exc())
        
        # Сохраняем дамп ошибки
        save_error_dump(e, poll_name, question)
        
        # Пытаемся отправить сообщение об ошибке в чат
        try:
            error_msg = (
                f"❌ *Ошибка при создании опроса \"{poll_name}\"*\n\n"
                f"Не удалось создать опрос. Пожалуйста, проверьте логи и файл дампа для подробностей."
            )
            await bot.send_message(chat_id=chat_id, text=error_msg, parse_mode='Markdown')
        except Exception as send_error:
            logging.error(f"Не удалось отправить сообщение об ошибке: {send_error}")
        
        return

    # Если опрос успешно создан, продолжаем
    try:
        info_message = await bot.send_message(chat_id=chat_id, text="⏳ Идёт сбор голосов...")
    except Exception as e:
        logging.error(f"Ошибка при отправке информационного сообщения: {e}")
        # Если не удалось отправить info_message, продолжаем без него
        info_message = None

    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=poll_message.message_id)
    except Exception as e:
        logging.warning(f"Не удалось закрепить сообщение: {e}")

    if info_message:
        poll_data[poll_message.poll.id] = {
            'chat_id': chat_id,
            'poll_msg_id': poll_message.message_id,
            'info_msg_id': info_message.message_id,
            'yes_voters': [],
            'update_task': None,  # Задача отложенного обновления
            'last_message_text': "⏳ Идёт сбор голосов..."  # Последний отправленный текст
        }
    else:
        # Если info_message не был создан, создаем структуру без него
        poll_data[poll_message.poll.id] = {
            'chat_id': chat_id,
            'poll_msg_id': poll_message.message_id,
            'info_msg_id': None,
            'yes_voters': [],
            'update_task': None,
            'last_message_text': "⏳ Идёт сбор голосов..."
        }

    logging.info(f"Создан {poll_name} {poll_message.poll.id}")

async def update_players_list(poll_id: str):
    """Обновляет список игроков с задержкой 10 секунд"""
    await asyncio.sleep(10)  # Задержка 10 секунд
    
    if poll_id not in poll_data:
        return
    
    data = poll_data[poll_id]
    yes_voters = data['yes_voters']
    
    # Формируем текст в зависимости от количества игроков
    if len(yes_voters) == 0:
        text = "⏳ Идёт сбор голосов..."
    elif len(yes_voters) < REQUIRED_PLAYERS:
        text = f"⏳ *Идёт сбор голосов:* {len(yes_voters)}/{REQUIRED_PLAYERS}\n\n"
        text += "*Проголосовали:*\n"
        text += '\n'.join(f"{i + 1}) {p['name']}" for i, p in enumerate(yes_voters))
    else:
        main_players = yes_voters[:REQUIRED_PLAYERS]
        reserves = yes_voters[REQUIRED_PLAYERS:]
        
        text = "✅ *Список игроков:*\n"
        text += '\n'.join(f"{i + 1}) {p['name']}" for i, p in enumerate(main_players))
        
        if reserves:
            text += "\n\n🕗 *Запасные игроки:*\n"
            text += '\n'.join(f"{i + 1}) {p['name']}" for i, p in enumerate(reserves))
    
    # Проверяем, есть ли info_msg_id для обновления
    if data.get('info_msg_id') is None:
        logging.debug(f"info_msg_id отсутствует, пропускаем обновление")
        data['update_task'] = None
        return
    
    # Проверяем, изменился ли текст
    if text == data.get('last_message_text'):
        logging.debug(f"Текст не изменился, пропускаем обновление")
    else:
        try:
            await bot.edit_message_text(
                chat_id=data['chat_id'],
                message_id=data['info_msg_id'],
                text=text,
                parse_mode='Markdown'
            )
            data['last_message_text'] = text  # Сохраняем отправленный текст
            logging.info(f"Список игроков обновлен: {len(yes_voters)} человек")
        except Exception as e:
            logging.error(f"Ошибка редактирования сообщения: {e}")
    
    # Очищаем задачу из данных
    data['update_task'] = None

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
    
    # Отменяем предыдущую задачу обновления, если она есть
    if 'update_task' in data and data['update_task'] is not None:
        data['update_task'].cancel()
        logging.debug("Предыдущая задача обновления отменена")
    
    # Создаём новую задачу обновления с задержкой 10 секунд
    data['update_task'] = asyncio.create_task(update_players_list(poll_id))
    logging.debug("Создана новая задача отложенного обновления (10 сек)")

def create_poll_job(message: str, poll_name: str):
    """
    Создаёт асинхронную задачу для отправки опроса.
    Эта функция нужна для корректной работы с APScheduler.
    """
    async def job():
        await send_poll(message, poll_name)
    return job

def setup_scheduler():
    """Настройка планировщика задач из конфигурации"""
    if not POLLS_SCHEDULE:
        logging.warning("Расписание опросов не найдено в config.json")
        return
    
    logging.info("Настройка планировщика:")
    
    for idx, poll_config in enumerate(POLLS_SCHEDULE):
        poll_name = poll_config.get("name", f"Опрос #{idx + 1}")
        message = poll_config.get("message", "")
        day_of_week = poll_config.get("day_of_week", "*")
        hour_utc = poll_config.get("hour_utc", 0)
        minute_utc = poll_config.get("minute_utc", 0)
        
        if not message:
            logging.warning(f"Пропущен опрос '{poll_name}': отсутствует текст сообщения")
            continue
        
        # Создаём уникальный ID для задачи
        job_id = f"poll_{idx}"
        
        # Настраиваем триггер
        trigger_kwargs = {
            'hour': hour_utc,
            'minute': minute_utc,
            'timezone': 'UTC'
        }
        
        # Добавляем день недели если указан (не "*")
        if day_of_week != "*":
            trigger_kwargs['day_of_week'] = day_of_week
        
        # Создаём задачу с правильным замыканием
        poll_job = create_poll_job(message, poll_name)
        
        # Добавляем задачу в планировщик
        scheduler.add_job(
            poll_job,
            trigger=CronTrigger(**trigger_kwargs),
            id=job_id,
            name=poll_name,
            replace_existing=True
        )
        
        # Логируем информацию о добавленной задаче
        if day_of_week == "*":
            logging.info(f"  - Ежедневно {hour_utc:02d}:{minute_utc:02d} UTC - {poll_name}")
        else:
            logging.info(f"  - {day_of_week.upper()} {hour_utc:02d}:{minute_utc:02d} UTC - {poll_name}")

async def on_startup(dp: Dispatcher):
    """Выполняется при запуске бота"""
    # Настраиваем и запускаем планировщик
    setup_scheduler()
    scheduler.start()
    logging.info("Планировщик запущен")
    
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
    
    # Останавливаем планировщик
    if scheduler.running:
        scheduler.shutdown()
        logging.info("Планировщик остановлен")
    
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
        executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
