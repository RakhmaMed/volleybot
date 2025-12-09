"""Логика работы с опросами."""

import asyncio
import logging
import traceback
from asyncio import Task
from collections.abc import Callable
from typing import Any, TypedDict

from aiogram import Bot
from aiogram.exceptions import TelegramMigrateToChat

from .config import POLL_OPTIONS, REQUIRED_PLAYERS
from .utils import save_error_dump, escape_html


class VoterInfoRequired(TypedDict):
    """Обязательные поля информации о проголосовавшем."""
    id: int
    name: str


class VoterInfo(VoterInfoRequired, total=False):
    """Информация о проголосовавшем."""
    update_id: int


class PollDataItem(TypedDict, total=False):
    """Данные опроса."""
    chat_id: int
    poll_msg_id: int
    info_msg_id: int | None
    yes_voters: list[VoterInfo]
    update_task: Task[None] | None
    last_message_text: str
    subs: list[int]


# Глобальное хранилище данных опросов
poll_data: dict[str, PollDataItem] = {}


def sort_voters_by_update_id(voters: list[VoterInfo]) -> list[VoterInfo]:
    """Возвращает список голосовавших, отсортированный по update_id (порядок событий)."""
    return sorted(voters, key=lambda v: (v.get('update_id', 0), v['id']))


async def send_poll(
    bot: Bot,
    chat_id: int,
    question: str,
    poll_name: str,
    bot_enabled: bool,
    subs: list[int] | None = None
) -> int:
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
        new_chat_id: int = e.migrate_to_chat_id
        logging.error(f"Группа мигрирована в супергруппу. Старый ID: {chat_id}, Новый ID: {new_chat_id}")
        save_error_dump(e, poll_name, question, chat_id)
        
        try:
            error_msg: str = (
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
    info_message = None
    try:
        info_message = await bot.send_message(chat_id=chat_id, text="⏳ Идёт сбор голосов...")
    except Exception as e:
        logging.error(f"Ошибка при отправке информационного сообщения: {e}")

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
        'last_message_text': "⏳ Идёт сбор голосов...",
        'subs': subs or []
    }

    logging.info(f"Создан {poll_name} {poll_message.poll.id}")
    return chat_id


async def update_players_list(bot: Bot, poll_id: str) -> None:
    """Обновляет список игроков с задержкой 10 секунд."""
    await asyncio.sleep(10)
    
    if poll_id not in poll_data:
        return
    
    data = poll_data[poll_id]
    yes_voters: list[VoterInfo] = sort_voters_by_update_id(data['yes_voters'])
    data['yes_voters'] = yes_voters
    
    # Формируем текст (HTML-разметка)
    text: str
    if len(yes_voters) == 0:
        text = "⏳ Идёт сбор голосов..."
    elif len(yes_voters) < REQUIRED_PLAYERS:
        text = (
            f"⏳ <b>Идёт сбор голосов:</b> "
            f"{len(yes_voters)}/{REQUIRED_PLAYERS}\n\n"
            "<b>Проголосовали:</b>\n"
        )
        text += '\n'.join(
            f"{i + 1}) {escape_html(p['name'])}"
            for i, p in enumerate(yes_voters)
        )
    else:
        main_players: list[VoterInfo] = yes_voters[:REQUIRED_PLAYERS]
        reserves: list[VoterInfo] = yes_voters[REQUIRED_PLAYERS:]
        
        text = "✅ <b>Список игроков:</b>\n"
        text += '\n'.join(
            f"{i + 1}) {escape_html(p['name'])}"
            for i, p in enumerate(main_players)
        )
        
        if reserves:
            text += "\n\n🕗 <b>Запасные игроки:</b>\n"
            text += '\n'.join(
                f"{i + 1}) {escape_html(p['name'])}"
                for i, p in enumerate(reserves)
            )

    # Добавляем легенду
    text += "\n\n⭐️ — оплативший за месяц\n🏐 — донат на мяч"
    
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
                parse_mode='HTML'
            )
            data['last_message_text'] = text
            logging.info(f"Список игроков обновлен: {len(yes_voters)} человек")
        except Exception as e:
            logging.error(f"Ошибка редактирования сообщения: {e}")
    
    data['update_task'] = None


async def close_poll(
    bot: Bot,
    poll_name: str,
    get_chat_id: Callable[[], int]
) -> None:
    """
    Закрытие активного опроса и публикация финального списка.
    
    Args:
        bot: Экземпляр бота
        poll_name: Название опроса для логирования
        get_chat_id: Функция получения текущего chat_id
    """
    chat_id: int = get_chat_id()
    
    if not poll_data:
        logging.info(f"Нет активных опросов для закрытия ({poll_name})")
        return
    
    # Берём первый (и обычно единственный) активный опрос
    poll_id: str = list(poll_data.keys())[0]
    data: PollDataItem = poll_data[poll_id]
    
    # Останавливаем опрос
    try:
        await bot.stop_poll(chat_id=data['chat_id'], message_id=data['poll_msg_id'])
        logging.info(f"Опрос '{poll_name}' остановлен")
    except Exception as e:
        logging.error(f"Ошибка при остановке опроса '{poll_name}': {e}")
    
    # Формируем финальный список
    yes_voters: list[VoterInfo] = data.get('yes_voters', [])
    
    final_text: str
    if len(yes_voters) == 0:
        final_text = "📊 <b>Голосование завершено</b>\n\nНикто не записался."
    elif len(yes_voters) < REQUIRED_PLAYERS:
        final_text = (
            f"📊 <b>Голосование завершено:</b> "
            f"{len(yes_voters)}/{REQUIRED_PLAYERS}\n\n"
            "<b>Записались:</b>\n"
        )
        final_text += '\n'.join(
            f"{i + 1}) {escape_html(p['name'])}"
            for i, p in enumerate(yes_voters)
        )
        final_text += "\n\n⚠️ <b>Не хватает игроков!</b>"
    else:
        main_players: list[VoterInfo] = yes_voters[:REQUIRED_PLAYERS]
        reserves: list[VoterInfo] = yes_voters[REQUIRED_PLAYERS:]
        
        final_text = (
            "📊 <b>Голосование завершено</b> ✅\n\n"
            f"<b>Основной состав ({len(main_players)}):</b>\n"
        )
        final_text += '\n'.join(
            f"{i + 1}) {escape_html(p['name'])}"
            for i, p in enumerate(main_players)
        )
        
        if reserves:
            final_text += (
                f"\n\n🕗 <b>Запасные ({len(reserves)}):</b>\n"
            )
            final_text += '\n'.join(
                f"{i + 1}) {escape_html(p['name'])}"
                for i, p in enumerate(reserves)
            )
    
    # Добавляем легенду
    final_text += "\n\n⭐️ — оплативший за месяц\n🏐 — донат на мяч"
    
    # Обновляем информационное сообщение с финальным списком
    if data.get('info_msg_id'):
        try:
            await bot.edit_message_text(
                chat_id=data['chat_id'],
                message_id=data['info_msg_id'],
                text=final_text,
                parse_mode='HTML'
            )
            logging.info(f"Финальный список опубликован для '{poll_name}'")
        except Exception as e:
            logging.error(f"Ошибка обновления финального сообщения: {e}")
    
    # Очищаем данные опроса
    del poll_data[poll_id]
    logging.info(f"Опрос '{poll_name}' закрыт, данные очищены")
