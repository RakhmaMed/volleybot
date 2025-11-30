# PowerShell скрипт для пересборки и перезапуска Docker контейнера бота
# Использование: .\redeploy.ps1

$ErrorActionPreference = "Stop"

$CONTAINER_NAME = "bot_itv"
$IMAGE_NAME = "bot_itv:latest"
$PORT = "8443:8443"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Пересборка и перезапуск бота" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Проверка наличия Docker
Write-Host "[1/5] Проверка Docker..." -ForegroundColor Yellow
try {
    docker --version | Out-Null
    Write-Host "✓ Docker найден" -ForegroundColor Green
} catch {
    Write-Host "✗ Docker не найден. Установите Docker Desktop." -ForegroundColor Red
    exit 1
}

# Остановка и удаление старого контейнера
Write-Host ""
Write-Host "[2/5] Остановка контейнера..." -ForegroundColor Yellow
$containerExists = docker ps -a --filter "name=$CONTAINER_NAME" --format "{{.Names}}"
if ($containerExists) {
    docker stop $CONTAINER_NAME 2>$null
    docker rm $CONTAINER_NAME 2>$null
    Write-Host "✓ Контейнер остановлен и удален" -ForegroundColor Green
} else {
    Write-Host "  Контейнер не найден, пропускаем" -ForegroundColor Gray
}

# Пересборка образа
Write-Host ""
Write-Host "[3/5] Пересборка образа..." -ForegroundColor Yellow
docker build -t $IMAGE_NAME .
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Образ успешно собран" -ForegroundColor Green
} else {
    Write-Host "✗ Ошибка при сборке образа" -ForegroundColor Red
    exit 1
}

# Запуск контейнера
Write-Host ""
Write-Host "[4/5] Запуск контейнера..." -ForegroundColor Yellow

# Проверка наличия директории certs
$certsPath = Join-Path $PSScriptRoot "certs"
$configPath = Join-Path $PSScriptRoot "config.json"

if (Test-Path $certsPath) {
    # Запуск с webhook (с сертификатами)
    docker run -d `
        --name $CONTAINER_NAME `
        --restart unless-stopped `
        -p $PORT `
        -v "${certsPath}:/app/certs:ro" `
        -v "${configPath}:/app/config.json:ro" `
        $IMAGE_NAME
    Write-Host "✓ Контейнер запущен в режиме webhook" -ForegroundColor Green
} else {
    # Запуск без webhook (polling mode)
    docker run -d `
        --name $CONTAINER_NAME `
        --restart unless-stopped `
        -v "${configPath}:/app/config.json:ro" `
        $IMAGE_NAME
    Write-Host "✓ Контейнер запущен в режиме polling" -ForegroundColor Green
    Write-Host "  (директория certs не найдена)" -ForegroundColor Gray
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Ошибка при запуске контейнера" -ForegroundColor Red
    exit 1
}

# Показ логов
Write-Host ""
Write-Host "[5/5] Логи контейнера:" -ForegroundColor Yellow
Write-Host "----------------------------------------" -ForegroundColor Gray
Start-Sleep -Seconds 2
docker logs --tail 20 $CONTAINER_NAME

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "✓ Развертывание завершено!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Полезные команды:" -ForegroundColor White
Write-Host "  docker logs -f $CONTAINER_NAME    # Просмотр логов в реальном времени" -ForegroundColor Gray
Write-Host "  docker stop $CONTAINER_NAME       # Остановить контейнер" -ForegroundColor Gray
Write-Host "  docker start $CONTAINER_NAME      # Запустить контейнер" -ForegroundColor Gray
Write-Host "  docker restart $CONTAINER_NAME    # Перезапустить контейнер" -ForegroundColor Gray
Write-Host ""

