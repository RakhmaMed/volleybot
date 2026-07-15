import sqlite3
import pytest
from src.db import insert_message, get_messages, _connect

def test_insert_and_get_messages():
    # Insert a message
    insert_message(
        message_id=300,
        chat_id=-1003503678114,
        user_id=5013132836,
        text="Привет",
        date=1719999999
    )

    # Retrieve messages for the chat
    messages = get_messages(chat_id=-1003503678114)
    assert len(messages) == 1
    msg = messages[0]
    assert msg["message_id"] == 300
    assert msg["chat_id"] == -1003503678114
    assert msg["user_id"] == 5013132836
    assert msg["text"] == "Привет"
    assert msg["date"] == 1719999999

def test_insert_duplicate_message_id_raises_or_logs():
    # Insert a message in chat A
    insert_message(
        message_id=300,
        chat_id=-1001,
        user_id=5013132836,
        text="Привет A",
        date=1719999999
    )

    # Attempt to insert same message_id in chat B (fails due to message_id UNIQUE / PRIMARY KEY constraint)
    # Since insert_message catches sqlite3.Error, it shouldn't raise, but it won't insert either.
    insert_message(
        message_id=300,
        chat_id=-1002,
        user_id=5013132836,
        text="Привет B",
        date=1719999999
    )

    messages_a = get_messages(chat_id=-1001)
    messages_b = get_messages(chat_id=-1002)

    assert len(messages_a) == 1
    assert messages_a[0]["text"] == "Привет A"
    assert len(messages_b) == 0  # Second insert fails because of duplicate PRIMARY KEY message_id
