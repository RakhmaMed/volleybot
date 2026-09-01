"""Тесты сохранения состояния через функциональный интерфейс БД."""

from returns.result import Success

from src.db2 import (
    DB,
    add_transaction,
    get_fund_balance,
    insert_message,
    insert_player,
    load_state,
    save_state,
    update_fund_balance,
)
from src.types import InvalidData


def test_save_state_stores_json_serialized_value(test_db: DB) -> None:
    value = {"players": [1, 2], "enabled": True}

    result = save_state(test_db, "bot_state", value)
    assert result == Success(None)
    result = load_state(test_db, "bot_state")
    assert result.unwrap() == value


def test_save_state_updates_existing_key(test_db: DB) -> None:
    assert save_state(test_db, "bot_state", {"version": 1}) == Success(None)

    result = save_state(test_db, "bot_state", {"version": 2})
    assert result == Success(None)
    result = load_state(test_db, "bot_state")
    assert result.unwrap() == {"version": 2}


def test_save_state_returns_failure_for_non_json_serializable_value(test_db: DB) -> None:
    error = save_state(test_db, "bot_state", {"invalid": object()}).failure()

    assert isinstance(error, InvalidData)
    assert error.detail == "Object of type object is not JSON serializable"
    count = test_db.conn.execute("SELECT COUNT(*) FROM kv_store").fetchone()[0]
    assert count == 1 # we always have the "fund_balance" key

def test_load_state_returns_default_for_nonexistent_key(test_db: DB) -> None:
    result = load_state(test_db, "nonexistent_key")
    assert result.unwrap() is None

def test_load_state_returns_default_for_nonexistent_key_with_default(test_db: DB) -> None:
    result = load_state(test_db, "nonexistent_key", default=42)
    assert result.unwrap() == 42

# Больше не нужен этот тест. Переписать с использованием атомарного варианта
def test_result_returning_operations_commit(test_db: DB) -> None:
    assert insert_player(test_db, 1) == Success(None)
    assert add_transaction(test_db, 1, 100, "Пополнение") == Success(None)
    assert update_fund_balance(test_db, 100) == Success(None)
    assert get_fund_balance(test_db) == Success(100)
    assert insert_message(test_db, 10, 20, 1, "Сообщение", 30) == Success(None)
    assert not test_db.conn.in_transaction

    transaction_count = test_db.conn.execute(
        "SELECT COUNT(*) FROM transactions"
    ).fetchone()[0]
    message_count = test_db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert transaction_count == 1
    assert message_count == 1
