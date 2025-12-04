#!/bin/bash
# Скрипт для автоматического обновления SSL сертификатов Let's Encrypt
# Использование: ./update_certs.sh [domain] [project_path]

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Параметры по умолчанию
DOMAIN="${1:-}"
PROJECT_PATH="${2:-$(pwd)}"
CONTAINER_NAME="volleybot"
CERTS_DIR="${PROJECT_PATH}/certs"
LETSENCRYPT_DIR="/etc/letsencrypt/live"

# Функция помощи
show_help() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Скрипт обновления SSL сертификатов${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    echo "Использование: ./update_certs.sh [domain] [project_path]"
    echo ""
    echo "Параметры:"
    echo "  domain        Домен для обновления сертификатов (обязательно)"
    echo "  project_path  Путь к проекту (по умолчанию: текущая директория)"
    echo ""
    echo "Примеры:"
    echo "  ./update_certs.sh example.com"
    echo "  ./update_certs.sh example.com /path/to/volleybot"
    echo ""
}

# Проверка параметров
if [ -z "$DOMAIN" ]; then
    echo -e "${RED}❌ Ошибка: не указан домен${NC}"
    echo ""
    show_help
    exit 1
fi

# Проверка существования директории проекта
if [ ! -d "$PROJECT_PATH" ]; then
    echo -e "${RED}❌ Ошибка: директория проекта не найдена: $PROJECT_PATH${NC}"
    exit 1
fi

# Проверка существования Let's Encrypt сертификатов
CERT_PATH="${LETSENCRYPT_DIR}/${DOMAIN}"
if [ ! -d "$CERT_PATH" ]; then
    echo -e "${RED}❌ Ошибка: сертификаты для домена '$DOMAIN' не найдены${NC}"
    echo -e "${YELLOW}Путь: $CERT_PATH${NC}"
    echo ""
    echo -e "${YELLOW}Сначала получите сертификаты:${NC}"
    echo "  sudo certbot certonly --standalone -d $DOMAIN"
    exit 1
fi

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Обновление SSL сертификатов${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""
echo -e "${YELLOW}Домен:${NC} $DOMAIN"
echo -e "${YELLOW}Проект:${NC} $PROJECT_PATH"
echo ""

# Создание директории для сертификатов, если её нет
if [ ! -d "$CERTS_DIR" ]; then
    echo -e "${YELLOW}[1/4] Создание директории для сертификатов...${NC}"
    mkdir -p "$CERTS_DIR"
    echo -e "${GREEN}✓ Директория создана: $CERTS_DIR${NC}"
else
    echo -e "${GREEN}✓ Директория существует: $CERTS_DIR${NC}"
fi

# Копирование сертификатов
echo ""
echo -e "${YELLOW}[2/4] Копирование сертификатов...${NC}"

# Проверка существования файлов сертификатов
if [ ! -f "${CERT_PATH}/fullchain.pem" ] || [ ! -f "${CERT_PATH}/privkey.pem" ]; then
    echo -e "${RED}❌ Ошибка: файлы сертификатов не найдены${NC}"
    echo -e "${YELLOW}Ожидаемые файлы:${NC}"
    echo "  ${CERT_PATH}/fullchain.pem"
    echo "  ${CERT_PATH}/privkey.pem"
    exit 1
fi

# Копирование с использованием sudo (если необходимо)
if [ -r "${CERT_PATH}/fullchain.pem" ] && [ -r "${CERT_PATH}/privkey.pem" ]; then
    cp "${CERT_PATH}/fullchain.pem" "${CERTS_DIR}/fullchain.pem"
    cp "${CERT_PATH}/privkey.pem" "${CERTS_DIR}/privkey.pem"
else
    echo -e "${YELLOW}Требуются права sudo для копирования сертификатов...${NC}"
    sudo cp "${CERT_PATH}/fullchain.pem" "${CERTS_DIR}/fullchain.pem"
    sudo cp "${CERT_PATH}/privkey.pem" "${CERTS_DIR}/privkey.pem"
    sudo chown "$USER:$USER" "${CERTS_DIR}/fullchain.pem" "${CERTS_DIR}/privkey.pem"
fi

# Установка прав доступа
chmod 644 "${CERTS_DIR}/fullchain.pem"
chmod 600 "${CERTS_DIR}/privkey.pem"

echo -e "${GREEN}✓ Сертификаты скопированы${NC}"

# Проверка существования контейнера
echo ""
echo -e "${YELLOW}[3/4] Проверка контейнера...${NC}"
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${GREEN}✓ Контейнер найден: $CONTAINER_NAME${NC}"
    
    # Перезапуск контейнера
    echo ""
    echo -e "${YELLOW}[4/4] Перезапуск контейнера...${NC}"
    if docker restart "$CONTAINER_NAME" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Контейнер перезапущен${NC}"
        
        # Небольшая задержка для проверки статуса
        sleep 2
        
        # Проверка статуса контейнера
        if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            echo -e "${GREEN}✓ Контейнер работает${NC}"
        else
            echo -e "${YELLOW}⚠ Контейнер не запущен. Проверьте логи:${NC}"
            echo "  docker logs $CONTAINER_NAME"
        fi
    else
        echo -e "${YELLOW}⚠ Не удалось перезапустить контейнер автоматически${NC}"
        echo -e "${YELLOW}Перезапустите вручную:${NC}"
        echo "  docker restart $CONTAINER_NAME"
    fi
else
    echo -e "${YELLOW}⚠ Контейнер '$CONTAINER_NAME' не найден${NC}"
    echo -e "${YELLOW}Сертификаты обновлены, но контейнер не был перезапущен${NC}"
    echo -e "${YELLOW}Запустите контейнер вручную:${NC}"
    echo "  ./manage.sh deploy"
fi

echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${GREEN}✓ Обновление сертификатов завершено!${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""




