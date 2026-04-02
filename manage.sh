#!/bin/bash
# Универсальный скрипт управления проектом volleybot (Linux/macOS)
# Использование: ./manage.sh [команда] [опции]

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
NC='\033[0m' # No Color

CONTAINER_NAME="volleybot"
IMAGE_NAME="volleybot:latest"
PORT="127.0.0.1:8443:8443"
DEFAULT_REMOTE_DB_PATH="/app/data/volleybot.db"

# Определяем Fly app: приоритет у переменной окружения FLY_APP, затем fly.toml
get_fly_app() {
    if [ -n "${FLY_APP:-}" ]; then
        echo "$FLY_APP"
        return
    fi

    if [ -f "fly.toml" ]; then
        local parsed_app
        parsed_app=$(sed -n "s/^app[[:space:]]*=[[:space:]]*['\"]\([^'\"]*\)['\"]/\1/p" fly.toml | head -n 1)
        if [ -n "$parsed_app" ]; then
            echo "$parsed_app"
            return
        fi
    fi

    echo "volleybot"
}

require_flyctl() {
    if ! command -v fly >/dev/null 2>&1; then
        echo -e "${RED}❌ flyctl не найден. Установите flyctl: https://fly.io/docs/flyctl/install/${NC}"
        exit 1
    fi
}

# Подбираем совместимый Python (<=3.13), чтобы зависимости имели готовые колёса
find_compatible_python() {
    if [ -n "$UV_PYTHON" ]; then
        echo "$UV_PYTHON"
        return
    fi

    local candidates=("python3.12" "python3.13" "python3.11" "python3" "python")
    local chosen=""

    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" >/dev/null 2>&1; then
            local version major minor
            version=$("$cmd" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)
            major=${version%%.*}
            minor=${version#*.}

            if [ "$major" -eq 3 ] && [ "$minor" -le 13 ]; then
                chosen="$cmd"
                break
            fi
        fi
    done

    if [ -n "$chosen" ]; then
        echo "$chosen"
    fi
}

# Функция помощи
show_help() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Volleybot - Скрипт управления${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    echo "Использование: ./manage.sh [команда] [опции]"
    echo ""
    echo -e "${YELLOW}Доступные команды:${NC}"
    echo ""
    echo -e "  ${GREEN}setup${NC}                Настроить тестовое окружение"
    echo -e "  ${GREEN}test${NC} [опции]        Запустить тесты"
    echo -e "  ${GREEN}build${NC}                Собрать Docker образ"
    echo -e "  ${GREEN}deploy${NC}               Собрать и запустить Docker контейнер"
    echo -e "  ${GREEN}logs${NC}                 Показать логи контейнера"
    echo -e "  ${GREEN}start${NC}                Запустить контейнер"
    echo -e "  ${GREEN}stop${NC}                 Остановить контейнер"
    echo -e "  ${GREEN}restart${NC}              Перезапустить контейнер"
    echo -e "  ${GREEN}status${NC}               Показать статус контейнера"
    echo -e "  ${GREEN}clean${NC}                Удалить контейнер и образ"
    echo -e "  ${GREEN}db-pull${NC} [локальный-путь] [удалённый-путь]"
    echo -e "                         Скопировать БД с Fly.io на локальный компьютер"
    echo -e "  ${GREEN}db-push${NC} [локальный-путь] [удалённый-путь]"
    echo -e "                         Загрузить локальную БД на Fly.io"
    echo -e "  ${GREEN}help${NC}                 Показать эту справку"
    echo ""
    echo -e "${YELLOW}Опции для test:${NC}"
    echo "  -c, --coverage       Запустить с покрытием кода"
    echo "  -v, --verbose        Подробный вывод"
    echo "  -f, --file <путь>    Запустить конкретный файл"
    echo ""
    echo -e "${YELLOW}Примеры:${NC}"
    echo "  ./manage.sh setup"
    echo "  ./manage.sh test"
    echo "  ./manage.sh test --coverage"
    echo "  ./manage.sh test --file tests/test_utils.py"
    echo "  ./manage.sh build"
    echo "  ./manage.sh deploy"
    echo "  ./manage.sh logs"
    echo "  ./manage.sh db-pull"
    echo "  ./manage.sh db-push"
    echo ""
}

# Скопировать БД из Fly.io на локальный компьютер
pull_db_from_fly() {
    require_flyctl

    local app_name remote_path local_path backup_path
    app_name=$(get_fly_app)
    local_path="${1:-data/volleybot.db}"
    remote_path="${2:-$DEFAULT_REMOTE_DB_PATH}"

    mkdir -p "$(dirname "$local_path")"

    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Скачивание БД с Fly.io${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo -e "${GRAY}App: ${app_name}${NC}"
    echo -e "${GRAY}Remote: ${remote_path}${NC}"
    echo -e "${GRAY}Local: ${local_path}${NC}"
    echo ""

    if [ -f "$local_path" ]; then
        backup_path="${local_path}.$(date +%Y%m%d_%H%M%S).bak"
        cp "$local_path" "$backup_path"
        echo -e "${YELLOW}Сделан локальный бэкап: ${backup_path}${NC}"
    fi

    FLY_NO_UPDATE_CHECK=1 fly ssh sftp get "$remote_path" "$local_path" -a "$app_name"

    echo ""
    echo -e "${GREEN}✓ БД скачана в ${local_path}${NC}"
}

# Загрузить локальную БД на Fly.io
push_db_to_fly() {
    require_flyctl

    local app_name remote_path local_path remote_backup_path
    app_name=$(get_fly_app)
    local_path="${1:-data/volleybot.db}"
    remote_path="${2:-$DEFAULT_REMOTE_DB_PATH}"

    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Загрузка БД на Fly.io${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo -e "${GRAY}App: ${app_name}${NC}"
    echo -e "${GRAY}Local: ${local_path}${NC}"
    echo -e "${GRAY}Remote: ${remote_path}${NC}"
    echo ""

    if [ ! -f "$local_path" ]; then
        echo -e "${RED}❌ Локальный файл БД не найден: ${local_path}${NC}"
        exit 1
    fi

    remote_backup_path="${remote_path}.$(date +%Y%m%d_%H%M%S).bak"
    fly ssh console -a "$app_name" -C "sh -lc 'if [ -f \"$remote_path\" ]; then cp \"$remote_path\" \"$remote_backup_path\" && rm -f \"$remote_path\"; fi'"
    echo -e "${YELLOW}Резервная копия на сервере (если файл существовал): ${remote_backup_path}${NC}"

    FLY_NO_UPDATE_CHECK=1 fly ssh sftp put "$local_path" "$remote_path" -a "$app_name"

    echo ""
    echo -e "${GREEN}✓ БД загружена на Fly.io: ${remote_path}${NC}"
}

# Настройка тестового окружения
setup_environment() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Настройка тестового окружения${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    # Проверка наличия uv
    echo -e "${YELLOW}[1/4] Проверка uv...${NC}"
    if ! command -v uv &> /dev/null; then
        echo -e "${YELLOW}❌ uv не найден. Устанавливаю uv...${NC}"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"

        if ! command -v uv &> /dev/null; then
            echo -e "${RED}❌ Не удалось установить uv. Установите вручную:${NC}"
            echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
        fi
    fi
    echo -e "${GREEN}✓ uv найден${NC}"

    # Выбор совместимой версии Python
    PYTHON_BIN=$(find_compatible_python)
    if [ -z "$PYTHON_BIN" ]; then
        echo -e "${RED}❌ Не найден совместимый Python (нужен 3.12 или 3.13).${NC}"
        echo -e "${YELLOW}Установите Python 3.12/3.13 или задайте переменную UV_PYTHON с путём до интерпретатора.${NC}"
        exit 1
    fi
    PYTHON_VERSION=$("$PYTHON_BIN" - <<'PY'
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
)
    echo -e "${GRAY}Используем Python: ${PYTHON_BIN} (версия ${PYTHON_VERSION})${NC}"

    # Создание виртуального окружения
    echo ""
    echo -e "${YELLOW}[2/4] Создание виртуального окружения...${NC}"
    UV_PYTHON="$PYTHON_BIN" uv venv
    echo -e "${GREEN}✓ Виртуальное окружение создано${NC}"

    # Активация виртуального окружения
    echo ""
    echo -e "${YELLOW}[3/4] Активация виртуального окружения...${NC}"
    source .venv/bin/activate
    echo -e "${GREEN}✓ Виртуальное окружение активировано${NC}"

    # Установка зависимостей
    echo ""
    echo -e "${YELLOW}[4/4] Установка зависимостей...${NC}"
    UV_PYTHON=".venv/bin/python" uv pip install -r requirements.txt
    echo -e "${GREEN}✓ Зависимости установлены${NC}"

    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${GREEN}✓ Тестовое окружение готово!${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    echo -e "${NC}Для запуска тестов выполните:${NC}"
    echo -e "${GRAY}  source .venv/bin/activate${NC}"
    echo -e "${GRAY}  pytest${NC}"
    echo ""
    echo -e "${NC}Или используйте:${NC}"
    echo -e "${GRAY}  ./manage.sh test${NC}"
    echo ""
}

# Запуск тестов
run_tests() {
    COVERAGE=false
    VERBOSE=false
    FILE=""

    # Парсинг аргументов
    while [[ $# -gt 0 ]]; do
        case $1 in
            --coverage|-c)
                COVERAGE=true
                shift
                ;;
            --verbose|-v)
                VERBOSE=true
                shift
                ;;
            --file|-f)
                FILE="$2"
                shift 2
                ;;
            *)
                echo -e "${RED}Неизвестный аргумент: $1${NC}"
                echo "Использование: ./manage.sh test [--coverage] [--verbose] [--file <путь>]"
                exit 1
                ;;
        esac
    done

    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Запуск тестов${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    # Проверка наличия виртуального окружения
    if [ ! -d ".venv" ]; then
        echo -e "${RED}❌ Виртуальное окружение не найдено.${NC}"
        echo -e "${YELLOW}Запустите: ./manage.sh setup${NC}"
        exit 1
    fi

    # Активация виртуального окружения
    source .venv/bin/activate

    # Формирование команды pytest
    PYTEST_ARGS=()

    if [ "$VERBOSE" = true ]; then
        PYTEST_ARGS+=("-v")
    fi

    if [ "$COVERAGE" = true ]; then
        PYTEST_ARGS+=("--cov=." "--cov-report=html" "--cov-report=term-missing")
    fi

    if [ -n "$FILE" ]; then
        PYTEST_ARGS+=("$FILE")
    fi

    # Запуск тестов
    if [ ${#PYTEST_ARGS[@]} -gt 0 ]; then
        pytest "${PYTEST_ARGS[@]}"
    else
        pytest
    fi

    if [ "$COVERAGE" = true ]; then
        echo ""
        echo -e "${GREEN}📊 Отчёт о покрытии сохранён в htmlcov/index.html${NC}"
    fi
}

# Сборка Docker образа
build_image() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Сборка Docker образа${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    # Проверка наличия Docker
    echo -e "${YELLOW}[1/3] Проверка Docker...${NC}"
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}✗ Docker не найден. Установите Docker.${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Docker найден${NC}"

    # Проверка доступности Docker daemon
    echo ""
    echo -e "${YELLOW}[2/3] Проверка Docker daemon...${NC}"
    if ! docker info &> /dev/null; then
        echo -e "${RED}✗ Docker daemon не запущен. Запустите Docker.${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Docker daemon запущен${NC}"

    # Сборка образа
    echo ""
    echo -e "${YELLOW}[3/3] Сборка образа...${NC}"
    if docker build -t $IMAGE_NAME .; then
        echo ""
        echo -e "${CYAN}========================================${NC}"
        echo -e "${GREEN}✓ Образ успешно собран: $IMAGE_NAME${NC}"
        echo -e "${CYAN}========================================${NC}"
        echo ""
    else
        echo -e "${RED}✗ Ошибка при сборке образа${NC}"
        exit 1
    fi
}

# Деплой Docker контейнера
deploy_container() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Пересборка и перезапуск бота${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    # Проверка наличия Docker
    echo -e "${YELLOW}[1/6] Проверка Docker...${NC}"
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}✗ Docker не найден. Установите Docker.${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Docker найден${NC}"

    # Проверка доступности Docker daemon
    echo ""
    echo -e "${YELLOW}[2/6] Проверка Docker daemon...${NC}"
    if ! docker info &> /dev/null; then
        echo -e "${RED}✗ Docker daemon не запущен. Запустите Docker.${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Docker daemon запущен${NC}"

    # Остановка и удаление старого контейнера
    echo ""
    echo -e "${YELLOW}[3/6] Остановка контейнера...${NC}"
    if [ "$(docker ps -aq -f name=$CONTAINER_NAME)" ]; then
        docker stop $CONTAINER_NAME 2>/dev/null || true
        docker rm $CONTAINER_NAME 2>/dev/null || true
        echo -e "${GREEN}✓ Контейнер остановлен и удален${NC}"
    else
        echo -e "${GRAY}  Контейнер не найден, пропускаем${NC}"
    fi

    # Пересборка образа
    echo ""
    echo -e "${YELLOW}[4/6] Пересборка образа...${NC}"
    if docker build -t $IMAGE_NAME .; then
        echo -e "${GREEN}✓ Образ успешно собран${NC}"
    else
        echo -e "${RED}✗ Ошибка при сборке образа${NC}"
        exit 1
    fi

    # Запуск контейнера
    echo ""
    echo -e "${YELLOW}[5/6] Запуск контейнера...${NC}"

    # Создаем директорию для базы данных на хосте, чтобы Docker не создал ее от root
    mkdir -p "$(pwd)/data"

    # Запуск бота. Порт $PORT (127.0.0.1:8443:8443) ограничивает доступ только локальным хостом.
    # SSL теперь терминируется на Nginx, поэтому сертификаты внутрь контейнера не пробрасываем.
    docker run -d \
        --name $CONTAINER_NAME \
        --restart unless-stopped \
        -p $PORT \
        -v "$(pwd)/.env:/app/.env:ro" \
        -v "$(pwd)/data:/app/data" \
        $IMAGE_NAME

    echo -e "${GREEN}✓ Контейнер запущен${NC}"

    # Показ логов
    echo ""
    echo -e "${YELLOW}[6/6] Логи контейнера:${NC}"
    echo -e "${GRAY}----------------------------------------${NC}"
    sleep 2
    docker logs --tail 20 $CONTAINER_NAME

    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${GREEN}✓ Развертывание завершено!${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    show_docker_commands
}

# Проверка существования контейнера
container_exists() {
    [ "$(docker ps -aq -f name=$CONTAINER_NAME)" ]
}

# Показать логи
show_logs() {
    if ! container_exists; then
        echo -e "${RED}❌ Контейнер '$CONTAINER_NAME' не найден.${NC}"
        echo -e "${YELLOW}Запустите: ./manage.sh deploy${NC}"
        exit 1
    fi

    echo -e "${CYAN}Логи контейнера $CONTAINER_NAME:${NC}"
    echo ""
    docker logs -f $CONTAINER_NAME
}

# Запустить контейнер
start_container() {
    if ! container_exists; then
        echo -e "${RED}❌ Контейнер '$CONTAINER_NAME' не найден.${NC}"
        echo -e "${YELLOW}Запустите: ./manage.sh deploy${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Запуск контейнера...${NC}"
    if docker start $CONTAINER_NAME; then
        echo -e "${GREEN}✓ Контейнер запущен${NC}"
        show_docker_commands
    else
        echo -e "${RED}✗ Ошибка при запуске контейнера${NC}"
        exit 1
    fi
}

# Остановить контейнер
stop_container() {
    if ! container_exists; then
        echo -e "${RED}❌ Контейнер '$CONTAINER_NAME' не найден.${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Остановка контейнера...${NC}"
    if docker stop $CONTAINER_NAME; then
        echo -e "${GREEN}✓ Контейнер остановлен${NC}"
    else
        echo -e "${RED}✗ Ошибка при остановке контейнера${NC}"
        exit 1
    fi
}

# Перезапустить контейнер
restart_container() {
    if ! container_exists; then
        echo -e "${RED}❌ Контейнер '$CONTAINER_NAME' не найден.${NC}"
        echo -e "${YELLOW}Запустите: ./manage.sh deploy${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Перезапуск контейнера...${NC}"
    if docker restart $CONTAINER_NAME; then
        echo -e "${GREEN}✓ Контейнер перезапущен${NC}"
        sleep 2
        docker logs --tail 20 $CONTAINER_NAME
    else
        echo -e "${RED}✗ Ошибка при перезапуске контейнера${NC}"
        exit 1
    fi
}

# Показать статус
show_status() {
    echo -e "${CYAN}Статус контейнера:${NC}"
    echo ""
    STATUS=$(docker ps -a --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}")
    if [ -n "$STATUS" ] && echo "$STATUS" | grep -q "$CONTAINER_NAME"; then
        echo "$STATUS"
    else
        echo -e "${YELLOW}Контейнер '$CONTAINER_NAME' не найден.${NC}"
        echo -e "${GRAY}Запустите: ./manage.sh deploy${NC}"
    fi
}

# Очистка
clean_docker() {
    echo -e "${YELLOW}Удаление контейнера и образа...${NC}"
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
    docker rmi $IMAGE_NAME 2>/dev/null || true
    echo -e "${GREEN}✓ Контейнер и образ удалены${NC}"
}

# Показать Docker команды
show_docker_commands() {
    echo -e "${NC}Полезные команды:${NC}"
    echo -e "${GRAY}  ./manage.sh logs        # Просмотр логов в реальном времени${NC}"
    echo -e "${GRAY}  ./manage.sh stop        # Остановить контейнер${NC}"
    echo -e "${GRAY}  ./manage.sh start       # Запустить контейнер${NC}"
    echo -e "${GRAY}  ./manage.sh restart     # Перезапустить контейнер${NC}"
    echo -e "${GRAY}  ./manage.sh status      # Показать статус${NC}"
    echo ""
}

# Основная логика
case "${1:-help}" in
    setup)
        setup_environment
        ;;
    test)
        shift
        run_tests "$@"
        ;;
    build)
        build_image
        ;;
    deploy)
        deploy_container
        ;;
    logs)
        show_logs
        ;;
    start)
        start_container
        ;;
    stop)
        stop_container
        ;;
    restart)
        restart_container
        ;;
    status)
        show_status
        ;;
    clean)
        clean_docker
        ;;
    db-pull)
        shift
        pull_db_from_fly "$@"
        ;;
    db-push)
        shift
        push_db_to_fly "$@"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "${RED}Неизвестная команда: $1${NC}"
        echo ""
        show_help
        exit 1
        ;;
esac
