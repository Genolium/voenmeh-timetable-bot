#!/bin/bash

# Скрипт для перезапуска всех сервисов Voenmeh Bot
# Использование: ./scripts/restart_services.sh [service_name]

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функция для логирования
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Проверяем, что docker-compose.yml существует
if [ ! -f "docker-compose.yml" ]; then
    error "docker-compose.yml не найден. Запустите скрипт из корневой директории проекта."
    exit 1
fi

# Проверяем, что Docker запущен
if ! docker info > /dev/null 2>&1; then
    error "Docker не запущен или недоступен."
    exit 1
fi

# Функция для перезапуска конкретного сервиса
restart_service() {
    local service=$1
    log "Перезапуск сервиса: $service"
    
    if docker-compose ps | grep -q "$service"; then
        docker-compose restart "$service"
        success "Сервис $service перезапущен"
    else
        warning "Сервис $service не найден или не запущен"
    fi
}

# Функция для проверки состояния сервиса
check_service() {
    local service=$1
    log "Проверка состояния сервиса: $service"
    
    if docker-compose ps | grep -q "$service.*Up"; then
        success "Сервис $service работает"
    else
        warning "Сервис $service не работает"
    fi
}

# Основная логика
if [ $# -eq 0 ]; then
    # Перезапуск всех сервисов
    log "Перезапуск всех сервисов Voenmeh Bot..."
    
    # Останавливаем все сервисы
    log "Остановка всех сервисов..."
    docker-compose down
    
    # Запускаем все сервисы
    log "Запуск всех сервисов..."
    docker-compose up -d
    
    # Ждем немного для запуска
    log "Ожидание запуска сервисов..."
    sleep 10
    
    # Проверяем состояние основных сервисов
    log "Проверка состояния сервисов..."
    check_service "postgres"
    check_service "redis"
    check_service "rabbitmq"
    check_service "bot"
    check_service "worker"
    
    success "Все сервисы перезапущены!"
    
elif [ "$1" = "rabbitmq" ]; then
    # Специальная обработка для RabbitMQ
    log "Перезапуск RabbitMQ с очисткой..."
    
    # Останавливаем RabbitMQ
    docker-compose stop rabbitmq
    
    # Ждем полной остановки
    sleep 5
    
    # Запускаем RabbitMQ
    docker-compose up -d rabbitmq
    
    # Ждем запуска
    log "Ожидание запуска RabbitMQ..."
    sleep 15
    
    # Проверяем состояние
    check_service "rabbitmq"
    
    # Перезапускаем воркер, если он зависит от RabbitMQ
    restart_service "worker"
    
    success "RabbitMQ перезапущен!"
    
elif [ "$1" = "worker" ]; then
    # Перезапуск воркера
    restart_service "worker"
    
elif [ "$1" = "bot" ]; then
    # Перезапуск бота
    restart_service "bot"
    
elif [ "$1" = "db" ] || [ "$1" = "postgres" ]; then
    # Перезапуск базы данных
    restart_service "db"
    
elif [ "$1" = "redis" ]; then
    # Перезапуск Redis
    restart_service "redis"
    
elif [ "$1" = "status" ]; then
    # Показать статус всех сервисов
    log "Статус всех сервисов:"
    docker-compose ps
    
elif [ "$1" = "logs" ]; then
    # Показать логи всех сервисов
    log "Логи всех сервисов (Ctrl+C для выхода):"
    docker-compose logs -f
    
elif [ "$1" = "help" ] || [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Использование: $0 [service_name]"
    echo ""
    echo "Доступные команды:"
    echo "  (без аргументов)  - Перезапустить все сервисы"
    echo "  rabbitmq          - Перезапустить RabbitMQ с очисткой"
    echo "  worker            - Перезапустить воркер"
    echo "  bot               - Перезапустить бота"
    echo "  db|postgres       - Перезапустить базу данных"
    echo "  redis             - Перезапустить Redis"
    echo "  status            - Показать статус всех сервисов"
    echo "  logs              - Показать логи всех сервисов"
    echo "  help              - Показать эту справку"
    echo ""
    echo "Примеры:"
    echo "  $0                # Перезапустить все"
    echo "  $0 rabbitmq       # Перезапустить только RabbitMQ"
    echo "  $0 status         # Показать статус"
    
else
    error "Неизвестный сервис: $1"
    echo "Используйте '$0 help' для справки"
    exit 1
fi

# Показываем статус в конце
if [ "$1" != "status" ] && [ "$1" != "logs" ] && [ "$1" != "help" ]; then
    echo ""
    log "Текущий статус сервисов:"
    docker-compose ps
fi
