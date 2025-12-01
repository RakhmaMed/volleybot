"""Тесты для модуля utils."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from aiogram.types import User

from utils import is_admin, save_error_dump


class TestIsAdmin:
    """Тесты для функции is_admin."""
    
    def test_is_admin_with_correct_username(self):
        """Тест проверки администратора с правильным username."""
        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username="TwinkleDev55"
        )
        assert is_admin(user) is True
    
    def test_is_admin_with_username_with_at(self):
        """Тест проверки администратора с @ в username."""
        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username="@TwinkleDev55"
        )
        assert is_admin(user) is True
    
    def test_is_admin_with_wrong_username(self):
        """Тест проверки администратора с неправильным username."""
        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username="wrong_user"
        )
        assert is_admin(user) is False
    
    def test_is_admin_without_username(self):
        """Тест проверки администратора без username."""
        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username=None
        )
        assert is_admin(user) is False
    
    def test_is_admin_case_sensitive(self):
        """Тест проверки администратора с учётом регистра."""
        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username="twinkledev55"  # lowercase
        )
        assert is_admin(user) is False


class TestSaveErrorDump:
    """Тесты для функции save_error_dump."""
    
    def test_save_error_dump_creates_file(self, tmp_path: Path):
        """Тест создания файла дампа ошибки."""
        error_file = tmp_path / "error_dump.json"
        test_error = ValueError("Test error message")
        
        with patch('utils.os.path.dirname', return_value=str(tmp_path)):
            with patch('utils.os.path.join', return_value=str(error_file)):
                save_error_dump(test_error, "test_poll", "Test question", -1001234567890)
        
        assert error_file.exists()
        
        with open(error_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]['error_type'] == 'ValueError'
        assert data[0]['error_message'] == 'Test error message'
        assert data[0]['poll_name'] == 'test_poll'
        assert data[0]['question'] == 'Test question'
        assert data[0]['chat_id'] == -1001234567890
        assert 'timestamp' in data[0]
        assert 'traceback' in data[0]
    
    def test_save_error_dump_appends_to_existing_file(self, tmp_path: Path):
        """Тест добавления ошибки в существующий файл."""
        error_file = tmp_path / "error_dump.json"
        
        # Создаём существующий файл с одной ошибкой
        existing_data = [{
            'timestamp': '2024-01-01T00:00:00',
            'error_type': 'OldError',
            'error_message': 'Old error'
        }]
        with open(error_file, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f)
        
        test_error = ValueError("New error")
        
        with patch('utils.os.path.dirname', return_value=str(tmp_path)):
            with patch('utils.os.path.join', return_value=str(error_file)):
                save_error_dump(test_error, "new_poll", "New question", -1001234567890)
        
        with open(error_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert len(data) == 2
        assert data[0]['error_type'] == 'OldError'
        assert data[1]['error_type'] == 'ValueError'
    
    def test_save_error_dump_limits_to_50_entries(self, tmp_path: Path):
        """Тест ограничения количества записей до 50."""
        error_file = tmp_path / "error_dump.json"
        
        # Создаём файл с 60 ошибками
        existing_data = [{'error_type': f'Error{i}'} for i in range(60)]
        with open(error_file, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f)
        
        test_error = ValueError("New error")
        
        with patch('utils.os.path.dirname', return_value=str(tmp_path)):
            with patch('utils.os.path.join', return_value=str(error_file)):
                save_error_dump(test_error, "test_poll", "Test question", -1001234567890)
        
        with open(error_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Должно остаться только последние 50 записей
        assert len(data) == 50
        assert data[-1]['error_type'] == 'ValueError'
    
    def test_save_error_dump_handles_corrupted_file(self, tmp_path: Path):
        """Тест обработки повреждённого файла."""
        error_file = tmp_path / "error_dump.json"
        
        # Создаём повреждённый JSON файл
        with open(error_file, 'w', encoding='utf-8') as f:
            f.write("invalid json content {")
        
        test_error = ValueError("Test error")
        
        with patch('utils.os.path.dirname', return_value=str(tmp_path)):
            with patch('utils.os.path.join', return_value=str(error_file)):
                save_error_dump(test_error, "test_poll", "Test question", -1001234567890)
        
        # Файл должен быть перезаписан с новой ошибкой
        with open(error_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert len(data) == 1
        assert data[0]['error_type'] == 'ValueError'

