#!/bin/bash
# Скрипт с примерами команд для тестирования бота

set -e

echo "🧪 Примеры команд для тестирования VolleyBot"
echo "=============================================="
echo ""

# Цвета для вывода
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_command() {
    echo -e "${BLUE}$1${NC}"
    echo -e "${GREEN}$2${NC}"
    echo ""
}

echo "📋 ОСНОВНЫЕ КОМАНДЫ"
echo "-------------------"

print_command "1. Запустить тестовый бот (с временной БД):" \
    "   python test_bot.py"

print_command "2. Получить ID чата для тестирования:" \
    "   python get_chat_id.py"

print_command "3. Запустить автоматические тесты:" \
    "   ./manage.sh test"

print_command "4. Запустить тесты с покрытием кода:" \
    "   pytest tests/ --cov=src --cov-report=html"

echo ""
echo "🔧 ОТЛАДКА"
echo "----------"

print_command "5. Запустить бот с DEBUG логами:" \
    "   LOG_LEVEL=DEBUG python -m src.bot"

print_command "6. Проверить синтаксис Python файлов:" \
    "   python -m py_compile src/*.py"

print_command "7. Запустить конкретный тест:" \
    "   pytest tests/test_handlers.py::test_start_handler -v"

echo ""
echo "🚀 ПРОИЗВОДСТВО"
echo "---------------"

print_command "8. Запустить бота в режиме polling:" \
    "   python -m src.bot"

print_command "9. Запустить через Docker:" \
    "   docker-compose up -d"

print_command "10. Посмотреть логи Docker:" \
    "    docker-compose logs -f bot"

echo ""
echo "💾 РАБОТА С БД"
echo "--------------"

print_command "11. Открыть БД в SQLite:" \
    "    sqlite3 data/volleybot.db"

print_command "12. Создать бэкап БД:" \
    "    cp data/volleybot.db data/volleybot_backup_\$(date +%Y%m%d_%H%M%S).db"

print_command "13. Просмотреть схему БД:" \
    "    sqlite3 data/volleybot.db '.schema'"

echo ""
echo "🎯 БЫСТРЫЕ СЦЕНАРИИ ТЕСТИРОВАНИЯ"
echo "---------------------------------"

echo -e "${YELLOW}Сценарий 1: Тест новой функции${NC}"
echo "  1. python test_bot.py"
echo "  2. В Telegram: /start"
echo "  3. В Telegram: /open 1"
echo "  4. Тестируйте функцию"
echo "  5. Ctrl+C для остановки"
echo ""

echo -e "${YELLOW}Сценарий 2: Тест с несколькими пользователями${NC}"
echo "  1. Создайте тестовую группу в Telegram"
echo "  2. python get_chat_id.py"
echo "  3. Отправьте сообщение в группе для получения Chat ID"
echo "  4. python test_bot.py"
echo "  5. Пригласите друзей в группу"
echo "  6. Все могут тестировать бота одновременно"
echo ""

echo -e "${YELLOW}Сценарий 3: Тест inline-кнопок${NC}"
echo "  1. python test_bot.py"
echo "  2. В Telegram: /pay"
echo "  3. Нажмите на кнопку"
echo "  4. Проверьте логи на наличие 'callback_query'"
echo ""

echo ""
echo "📖 ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ"
echo "----------------------------"
echo "  • Полная документация: README.md"
echo "  • Шпаргалка по тестированию: TESTING_QUICKSTART.md"
echo "  • Детальное руководство: docs/TESTING.md"
echo "  • Конфигурация: docs/CONFIGURATION.md"
echo ""

# Если запущен с параметром, выполняем соответствующую команду
if [ -n "$1" ]; then
    case $1 in
        1)
            python test_bot.py
            ;;
        2)
            python get_chat_id.py
            ;;
        3)
            ./manage.sh test
            ;;
        4)
            pytest tests/ --cov=src --cov-report=html
            ;;
        5)
            LOG_LEVEL=DEBUG python -m src.bot
            ;;
        6)
            python -m py_compile src/*.py
            ;;
        7)
            echo "Укажите путь к тесту:"
            read -r test_path
            pytest "$test_path" -v
            ;;
        8)
            python -m src.bot
            ;;
        9)
            docker-compose up -d
            ;;
        10)
            docker-compose logs -f bot
            ;;
        11)
            sqlite3 data/volleybot.db
            ;;
        12)
            backup_name="data/volleybot_backup_$(date +%Y%m%d_%H%M%S).db"
            cp data/volleybot.db "$backup_name"
            echo "✅ Бэкап создан: $backup_name"
            ;;
        13)
            sqlite3 data/volleybot.db '.schema'
            ;;
        *)
            echo "❌ Неизвестная команда: $1"
            echo "Используйте номер команды от 1 до 13"
            exit 1
            ;;
    esac
else
    echo "💡 Подсказка: Запустите скрипт с номером команды для быстрого выполнения"
    echo "   Пример: ./test_commands.sh 1"
fi
