# Универсальный скрипт управления проектом volleybot (Windows PowerShell)
# Использование: .\manage.ps1 [команда] [опции]

param(
    [Parameter(Position=0)]
    [string]$Command = "help",

    [switch]$Coverage,
    [switch]$VerboseOutput,
    [string]$File = ""
)

$CONTAINER_NAME = "volleybot"
$IMAGE_NAME = "volleybot:latest"
$PORT = "127.0.0.1:8443:8443"

# Подбираем Python 3.14, оставляя 3.13 как совместимый запасной вариант
function Get-CompatiblePython {
    if ($env:UV_PYTHON) {
        return $env:UV_PYTHON
    }

    $candidates = @(
        @{ Cmd = @("py", "-3.14") },
        @{ Cmd = @("python3.14") },
        @{ Cmd = @("python3") },
        @{ Cmd = @("py", "-3.13") },
        @{ Cmd = @("python3.13") },
        @{ Cmd = @("python") }
    )

    foreach ($candidate in $candidates) {
        $cmd = $candidate.Cmd
        $exe = $cmd[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
            continue
        }

        $args = @()
        if ($cmd.Count -gt 1) {
            $args = $cmd[1..($cmd.Count - 1)]
        }

        try {
            $versionOut = & $exe @args "-c" "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
            if ($LASTEXITCODE -ne 0) { continue }
            $parts = $versionOut.Trim().Split(".")
            if ($parts[0] -eq "3" -and [int]$parts[1] -ge 13 -and [int]$parts[1] -le 14) {
                $pathOut = & $exe @args "-c" "import sys; print(sys.executable)"
                if ($LASTEXITCODE -eq 0 -and $pathOut) {
                    return $pathOut.Trim()
                }
            }
        } catch {
            continue
        }
    }

    return $null
}

# Функция помощи
function Show-Help {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Volleybot - Скрипт управления" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Использование: .\manage.ps1 [команда] [опции]" -ForegroundColor White
    Write-Host ""
    Write-Host "Доступные команды:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  setup" -ForegroundColor Green -NoNewline
    Write-Host "                Настроить тестовое окружение"
    Write-Host "  test" -ForegroundColor Green -NoNewline
    Write-Host " [опции]        Запустить тесты"
    Write-Host "  build" -ForegroundColor Green -NoNewline
    Write-Host "                Собрать Docker образ"
    Write-Host "  deploy" -ForegroundColor Green -NoNewline
    Write-Host "               Собрать и запустить Docker контейнер"
    Write-Host "  logs" -ForegroundColor Green -NoNewline
    Write-Host "                 Показать логи контейнера"
    Write-Host "  start" -ForegroundColor Green -NoNewline
    Write-Host "                Запустить контейнер"
    Write-Host "  stop" -ForegroundColor Green -NoNewline
    Write-Host "                 Остановить контейнер"
    Write-Host "  restart" -ForegroundColor Green -NoNewline
    Write-Host "              Перезапустить контейнер"
    Write-Host "  status" -ForegroundColor Green -NoNewline
    Write-Host "               Показать статус контейнера"
    Write-Host "  clean" -ForegroundColor Green -NoNewline
    Write-Host "                Удалить контейнер и образ"
    Write-Host "  help" -ForegroundColor Green -NoNewline
    Write-Host "                 Показать эту справку"
    Write-Host ""
    Write-Host "Опции для test:" -ForegroundColor Yellow
    Write-Host "  -Coverage        Запустить с покрытием кода"
    Write-Host "  -VerboseOutput   Подробный вывод"
    Write-Host "  -File <путь>     Запустить конкретный файл"
    Write-Host ""
    Write-Host "Примеры:" -ForegroundColor Yellow
    Write-Host "  .\manage.ps1 setup"
    Write-Host "  .\manage.ps1 test"
    Write-Host "  .\manage.ps1 test -Coverage"
    Write-Host "  .\manage.ps1 test -File tests\test_utils.py"
    Write-Host "  .\manage.ps1 build"
    Write-Host "  .\manage.ps1 deploy"
    Write-Host "  .\manage.ps1 logs"
    Write-Host ""
}

# Настройка тестового окружения
function Setup-Environment {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Настройка тестового окружения" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""

    # Проверка наличия uv
    Write-Host "[1/4] Проверка uv..." -ForegroundColor Yellow
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "❌ uv не найден. Устанавливаю uv..." -ForegroundColor Yellow
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

        # Обновляем PATH для текущей сессии
        $env:PATH = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Host "❌ Не удалось установить uv. Установите вручную:" -ForegroundColor Red
            Write-Host "   powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`"" -ForegroundColor Yellow
            exit 1
        }
    }
    Write-Host "✓ uv найден" -ForegroundColor Green

    # Выбор совместимой версии Python
    $pythonPath = Get-CompatiblePython
    if (-not $pythonPath) {
        Write-Host "❌ Не найден совместимый Python (нужен 3.14 или 3.13)." -ForegroundColor Red
        Write-Host "Установите Python 3.14/3.13 или задайте переменную окружения UV_PYTHON с путём до интерпретатора." -ForegroundColor Yellow
        exit 1
    }
    $pythonVersion = & $pythonPath "-c" "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    Write-Host "Используем Python: $pythonPath (версия $pythonVersion)" -ForegroundColor Gray
    $oldUvPython = $env:UV_PYTHON
    $env:UV_PYTHON = $pythonPath

    # Создание виртуального окружения
    Write-Host ""
    Write-Host "[2/4] Создание виртуального окружения..." -ForegroundColor Yellow
    uv venv --python $pythonPath --clear
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Ошибка при создании виртуального окружения" -ForegroundColor Red
        exit 1
    }
    Write-Host "✓ Виртуальное окружение создано" -ForegroundColor Green

    # Активация виртуального окружения
    Write-Host ""
    Write-Host "[3/4] Активация виртуального окружения..." -ForegroundColor Yellow
    & .\.venv\Scripts\Activate.ps1
    Write-Host "✓ Виртуальное окружение активировано" -ForegroundColor Green

    # Установка зависимостей
    Write-Host ""
    Write-Host "[4/4] Установка зависимостей..." -ForegroundColor Yellow
    uv pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Ошибка при установке зависимостей" -ForegroundColor Red
        exit 1
    }
    Write-Host "✓ Зависимости установлены" -ForegroundColor Green

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "✓ Тестовое окружение готово!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Для запуска тестов выполните:" -ForegroundColor White
    Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Gray
    Write-Host "  pytest" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Или используйте:" -ForegroundColor White
    Write-Host "  .\manage.ps1 test" -ForegroundColor Gray
    Write-Host ""
    $env:UV_PYTHON = $oldUvPython
}

# Запуск тестов
function Run-Tests {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Запуск тестов" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""

    # Проверка наличия виртуального окружения
    if (-not (Test-Path ".venv")) {
        Write-Host "❌ Виртуальное окружение не найдено." -ForegroundColor Red
        Write-Host "Запустите: .\manage.ps1 setup" -ForegroundColor Yellow
        exit 1
    }

    # Активация виртуального окружения
    & .\.venv\Scripts\Activate.ps1

    # Формирование команды pytest
    $pytestArgs = @()

    if ($VerboseOutput) {
        $pytestArgs += "-v"
    }

    if ($Coverage) {
        $pytestArgs += "--cov=."
        $pytestArgs += "--cov-report=html"
        $pytestArgs += "--cov-report=term-missing"
    }

    if ($File) {
        $pytestArgs += $File
    }

    # Запуск тестов
    if ($pytestArgs.Count -gt 0) {
        pytest $pytestArgs
    } else {
        pytest
    }

    if ($Coverage) {
        Write-Host ""
        Write-Host "📊 Отчёт о покрытии сохранён в htmlcov\index.html" -ForegroundColor Green
    }
}

# Сборка Docker образа
function Build-Image {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Сборка Docker образа" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""

    # Проверка наличия Docker
    Write-Host "[1/3] Проверка Docker..." -ForegroundColor Yellow
    try {
        docker --version | Out-Null
        Write-Host "✓ Docker найден" -ForegroundColor Green
    } catch {
        Write-Host "✗ Docker не найден. Установите Docker Desktop." -ForegroundColor Red
        exit 1
    }

    # Проверка доступности Docker daemon
    Write-Host ""
    Write-Host "[2/3] Проверка Docker daemon..." -ForegroundColor Yellow
    try {
        docker info | Out-Null
        Write-Host "✓ Docker daemon запущен" -ForegroundColor Green
    } catch {
        Write-Host "✗ Docker daemon не запущен. Запустите Docker Desktop." -ForegroundColor Red
        exit 1
    }

    # Сборка образа
    Write-Host ""
    Write-Host "[3/3] Сборка образа..." -ForegroundColor Yellow
    docker build -t $IMAGE_NAME .
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "✓ Образ успешно собран: $IMAGE_NAME" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host ""
    } else {
        Write-Host "✗ Ошибка при сборке образа" -ForegroundColor Red
        exit 1
    }
}

# Деплой Docker контейнера
function Deploy-Container {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Пересборка и перезапуск бота" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""

    # Проверка наличия Docker
    Write-Host "[1/6] Проверка Docker..." -ForegroundColor Yellow
    try {
        docker --version | Out-Null
        Write-Host "✓ Docker найден" -ForegroundColor Green
    } catch {
        Write-Host "✗ Docker не найден. Установите Docker Desktop." -ForegroundColor Red
        exit 1
    }

    # Проверка доступности Docker daemon
    Write-Host ""
    Write-Host "[2/6] Проверка Docker daemon..." -ForegroundColor Yellow
    try {
        docker info | Out-Null
        Write-Host "✓ Docker daemon запущен" -ForegroundColor Green
    } catch {
        Write-Host "✗ Docker daemon не запущен. Запустите Docker Desktop." -ForegroundColor Red
        exit 1
    }

    # Остановка и удаление старого контейнера
    Write-Host ""
    Write-Host "[3/6] Остановка контейнера..." -ForegroundColor Yellow
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
    Write-Host "[4/6] Пересборка образа..." -ForegroundColor Yellow
    docker build -t $IMAGE_NAME .
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Образ успешно собран" -ForegroundColor Green
    } else {
        Write-Host "✗ Ошибка при сборке образа" -ForegroundColor Red
        exit 1
    }

    # Запуск контейнера
    Write-Host ""
    Write-Host "[5/6] Запуск контейнера..." -ForegroundColor Yellow

    # Создаем директорию для базы данных на хосте, если она не существует
    $dataPath = Join-Path $PSScriptRoot "data"
    if (-not (Test-Path $dataPath)) {
        New-Item -Path $dataPath -ItemType Directory | Out-Null
    }

    # Запуск бота. Порт $PORT (127.0.0.1:8443:8443) ограничивает доступ только локальным хостом.
    # SSL теперь терминируется на Nginx, поэтому сертификаты внутрь контейнера не пробрасываем.
    $envPath = Join-Path $PSScriptRoot ".env"

    docker run -d `
        --name $CONTAINER_NAME `
        --restart unless-stopped `
        -p $PORT `
        -v "${envPath}:/app/.env:ro" `
        -v "${dataPath}:/app/data" `
        $IMAGE_NAME

    Write-Host "✓ Контейнер запущен" -ForegroundColor Green

    if ($LASTEXITCODE -ne 0) {
        Write-Host "✗ Ошибка при запуске контейнера" -ForegroundColor Red
        exit 1
    }

    # Показ логов
    Write-Host ""
    Write-Host "[6/6] Логи контейнера:" -ForegroundColor Yellow
    Write-Host "----------------------------------------" -ForegroundColor Gray
    Start-Sleep -Seconds 2
    docker logs --tail 20 $CONTAINER_NAME

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "✓ Развертывание завершено!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Show-DockerCommands
}

# Проверка существования контейнера
function Test-ContainerExists {
    $containerExists = docker ps -a --filter "name=$CONTAINER_NAME" --format "{{.Names}}"
    return ($containerExists -eq $CONTAINER_NAME)
}

# Показать логи
function Show-Logs {
    if (-not (Test-ContainerExists)) {
        Write-Host "❌ Контейнер '$CONTAINER_NAME' не найден." -ForegroundColor Red
        Write-Host "Запустите: .\manage.ps1 deploy" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Логи контейнера $CONTAINER_NAME" -ForegroundColor Cyan
    Write-Host ""
    docker logs -f $CONTAINER_NAME
}

# Запустить контейнер
function Start-Container {
    if (-not (Test-ContainerExists)) {
        Write-Host "❌ Контейнер '$CONTAINER_NAME' не найден." -ForegroundColor Red
        Write-Host "Запустите: .\manage.ps1 deploy" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Запуск контейнера..." -ForegroundColor Yellow
    docker start $CONTAINER_NAME
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Контейнер запущен" -ForegroundColor Green
        Show-DockerCommands
    } else {
        Write-Host "✗ Ошибка при запуске контейнера" -ForegroundColor Red
        exit 1
    }
}

# Остановить контейнер
function Stop-Container {
    if (-not (Test-ContainerExists)) {
        Write-Host "❌ Контейнер '$CONTAINER_NAME' не найден." -ForegroundColor Red
        exit 1
    }

    Write-Host "Остановка контейнера..." -ForegroundColor Yellow
    docker stop $CONTAINER_NAME
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Контейнер остановлен" -ForegroundColor Green
    } else {
        Write-Host "✗ Ошибка при остановке контейнера" -ForegroundColor Red
        exit 1
    }
}

# Перезапустить контейнер
function Restart-Container {
    if (-not (Test-ContainerExists)) {
        Write-Host "❌ Контейнер '$CONTAINER_NAME' не найден." -ForegroundColor Red
        Write-Host "Запустите: .\manage.ps1 deploy" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Перезапуск контейнера..." -ForegroundColor Yellow
    docker restart $CONTAINER_NAME
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Контейнер перезапущен" -ForegroundColor Green
        Start-Sleep -Seconds 2
        docker logs --tail 20 $CONTAINER_NAME
    } else {
        Write-Host "✗ Ошибка при перезапуске контейнера" -ForegroundColor Red
        exit 1
    }
}

# Показать статус
function Show-Status {
    Write-Host "Статус контейнера:" -ForegroundColor Cyan
    Write-Host ""
    $status = docker ps -a --filter "name=$CONTAINER_NAME" --format "table {{.Names}}`t{{.Status}}`t{{.Ports}}"
    if ($status -and $status -match $CONTAINER_NAME) {
        Write-Host $status
    } else {
        Write-Host "Контейнер '$CONTAINER_NAME' не найден." -ForegroundColor Yellow
        Write-Host "Запустите: .\manage.ps1 deploy" -ForegroundColor Gray
    }
}

# Очистка
function Clean-Docker {
    Write-Host "Удаление контейнера и образа..." -ForegroundColor Yellow
    docker stop $CONTAINER_NAME 2>$null
    docker rm $CONTAINER_NAME 2>$null
    docker rmi $IMAGE_NAME 2>$null
    Write-Host "✓ Контейнер и образ удалены" -ForegroundColor Green
}

# Показать Docker команды
function Show-DockerCommands {
    Write-Host "Полезные команды:" -ForegroundColor White
    Write-Host "  .\manage.ps1 logs        # Просмотр логов в реальном времени" -ForegroundColor Gray
    Write-Host "  .\manage.ps1 stop        # Остановить контейнер" -ForegroundColor Gray
    Write-Host "  .\manage.ps1 start       # Запустить контейнер" -ForegroundColor Gray
    Write-Host "  .\manage.ps1 restart     # Перезапустить контейнер" -ForegroundColor Gray
    Write-Host "  .\manage.ps1 status      # Показать статус" -ForegroundColor Gray
    Write-Host ""
}

# Основная логика
switch ($Command.ToLower()) {
    "setup" {
        Setup-Environment
    }
    "test" {
        Run-Tests
    }
    "build" {
        Build-Image
    }
    "deploy" {
        Deploy-Container
    }
    "logs" {
        Show-Logs
    }
    "start" {
        Start-Container
    }
    "stop" {
        Stop-Container
    }
    "restart" {
        Restart-Container
    }
    "status" {
        Show-Status
    }
    "clean" {
        Clean-Docker
    }
    "help" {
        Show-Help
    }
    default {
        Write-Host "Неизвестная команда: $Command" -ForegroundColor Red
        Write-Host ""
        Show-Help
        exit 1
    }
}
