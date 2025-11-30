"""Логика работы с опросами."""

import asyncio
import logging
import traceback

from aiogram import Bot
from aiogram.exceptions import TelegramMigrateToChat

from config import POLL_OPTIONS, REQUIRED_PLAYERS
from utils import save_error_dump

# Глобальное хранилище данных опросов
poll_data = {}


async def send_poll(bot: Bot, chat_id: int, question: str, poll_name: str, bot_enabled: bool):
    """
    Отправка опроса в чат.
    
    Args:
        bot: Экземпляр бота
        chat_id: ID чата для отправки
        question: Текст вопроса опроса
        poll_name: Название опроса для логирования
        bot_enabled: Флаг включения бота
        
    Returns:
        Новый chat_id (может измениться при миграции группы)
    """
    if not bot_enabled:
        logging.info(f"Бот выключен, {poll_name} не создан")
        return chat_id
    
    poll_data.clear()
    
    try:
        poll_message = await bot.send_poll(
            chat_id=chat_id,
            question=question,
            options=POLL_OPTIONS,
            is_anonymous=False
        )
    except TelegramMigrateToChat as e:
        new_chat_id = e.migrate_to_chat_id
        logging.error(f"Группа мигрирована в супергруппу. Старый ID: {chat_id}, Новый ID: {new_chat_id}")
        save_error_dump(e, poll_name, question, chat_id)
        
        try:
            error_msg = (
                f"❌ *Ошибка при создании опроса \"{poll_name}\"*\n\n"
                f"Группа была мигрирована в супергруппу.\n"
                f"Новый ID чата: `{new_chat_id}`"
            )
            await bot.send_message(chat_id=new_chat_id, text=error_msg, parse_mode='Markdown')
        except Exception as send_error:
            logging.error(f"Не удалось отправить сообщение об ошибке: {send_error}")
        
        return new_chat_id
        
    except Exception as e:
        logging.error(f"Ошибка при создании опроса \"{poll_name}\": {e}")
        logging.error(traceback.format_exc())
        save_error_dump(e, poll_name, question, chat_id)
        
        try:
            error_msg = (
                f"❌ *Ошибка при создании опроса \"{poll_name}\"*\n\n"
                f"Не удалось создать опрос. Пожалуйста, проверьте логи и файл дампа для подробностей."
            )
            await bot.send_message(chat_id=chat_id, text=error_msg, parse_mode='Markdown')
        except Exception as send_error:
            logging.error(f"Не удалось отправить сообщение об ошибке: {send_error}")
        
        return chat_id

    # Отправляем информационное сообщение
    try:
        info_message = await bot.send_message(chat_id=chat_id, text="⏳ Идёт сбор голосов...")
    except Exception as e:
        logging.error(f"Ошибка при отправке информационного сообщения: {e}")
        info_message = None

    # Закрепляем опрос
    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=poll_message.message_id)
    except Exception as e:
        logging.warning(f"Не удалось закрепить сообщение: {e}")

    # Сохраняем данные опроса
    poll_data[poll_message.poll.id] = {
        'chat_id': chat_id,
        'poll_msg_id': poll_message.message_id,
        'info_msg_id': info_message.message_id if info_message else None,
        'yes_voters': [],
        'update_task': None,
        'last_message_text': "⏳ Идёт сбор голосов..."
    }

    logging.info(f"Создан {poll_name} {poll_message.poll.id}")
    return chat_id


async def update_players_list(bot: Bot, poll_id: str):
    """Обновляет список игроков с задержкой 10 секунд."""
    await asyncio.sleep(10)
    
    if poll_id not in poll_data:
        return
    
    data = poll_data[poll_id]
    yes_voters = data['yes_voters']
    
    # Формируем текст
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
    
    if data.get('info_msg_id') is None:
        logging.debug("info_msg_id отсутствует, пропускаем обновление")
        data['update_task'] = None
        return
    
    if text == data.get('last_message_text'):
        logging.debug("Текст не изменился, пропускаем обновление")
    else:
        try:
            await bot.edit_message_text(
                chat_id=data['chat_id'],
                message_id=data['info_msg_id'],
                text=text,
                parse_mode='Markdown'
            )
            data['last_message_text'] = text
            logging.info(f"Список игроков обновлен: {len(yes_voters)} человек")
        except Exception as e:
            logging.error(f"Ошибка редактирования сообщения: {e}")
    
    data['update_task'] = None
