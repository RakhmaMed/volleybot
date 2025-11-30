#!/bin/bash
# Bash скрипт для пересборки и перезапуска Docker контейнера бота
# Использование: ./redeploy.sh

set -e

CONTAINER_NAME="bot_itv"
IMAGE_NAME="bot_itv:latest"
PORT="8443:8443"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
NC='\033[0m' # No Color

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Пересборка и перезапуск бота${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""

# Проверка наличия Docker
echo -e "${YELLOW}[1/5] Проверка Docker...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker не найден. Установите Docker.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker найден${NC}"

# Остановка и удаление старого контейнера
echo ""
echo -e "${YELLOW}[2/5] Остановка контейнера...${NC}"
if [ "$(docker ps -aq -f name=$CONTAINER_NAME)" ]; then
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
    echo -e "${GREEN}✓ Контейнер остановлен и удален${NC}"
else
    echo -e "${GRAY}  Контейнер не найден, пропускаем${NC}"
fi

# Пересборка образа
echo ""
echo -e "${YELLOW}[3/5] Пересборка образа...${NC}"
if docker build -t $IMAGE_NAME .; then
    echo -e "${GREEN}✓ Образ успешно собран${NC}"
else
    echo -e "${RED}✗ Ошибка при сборке образа${NC}"
    exit 1
fi

# Запуск контейнера
echo ""
echo -e "${YELLOW}[4/5] Запуск контейнера...${NC}"

# Проверка наличия директории certs
if [ -d "./certs" ]; then
    # Запуск с webhook (с сертификатами)
    docker run -d \
        --name $CONTAINER_NAME \
        --restart unless-stopped \
        -p $PORT \
        -v "$(pwd)/certs:/app/certs:ro" \
        -v "$(pwd)/config.json:/app/config.json:ro" \
        $IMAGE_NAME
    echo -e "${GREEN}✓ Контейнер запущен в режиме webhook${NC}"
else
    # Запуск без webhook (polling mode)
    docker run -d \
        --name $CONTAINER_NAME \
        --restart unless-stopped \
        -v "$(pwd)/config.json:/app/config.json:ro" \
        $IMAGE_NAME
    echo -e "${GREEN}✓ Контейнер запущен в режиме polling${NC}"
    echo -e "${GRAY}  (директория certs не найдена)${NC}"
fi

# Показ логов
echo ""
echo -e "${YELLOW}[5/5] Логи контейнера:${NC}"
echo -e "${GRAY}----------------------------------------${NC}"
sleep 2
docker logs --tail 20 $CONTAINER_NAME

echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${GREEN}✓ Развертывание завершено!${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""
echo -e "${NC}Полезные команды:${NC}"
echo -e "${GRAY}  docker logs -f $CONTAINER_NAME    # Просмотр логов в реальном времени${NC}"
echo -e "${GRAY}  docker stop $CONTAINER_NAME       # Остановить контейнер${NC}"
echo -e "${GRAY}  docker start $CONTAINER_NAME      # Запустить контейнер${NC}"
echo -e "${GRAY}  docker restart $CONTAINER_NAME    # Перезапустить контейнер${NC}"
echo ""

