#!/usr/bin/env bash
# Деплой бота на хосте: всё берётся из .env.
#
# Шаги: (опционально) обновление рабочей копии -> сборка образов ->
# применение миграций -> запуск сервисов -> проверка, что бот и воркер живы.
#
# Использование:
#   ./scripts/deploy.sh                      # выкатить текущую рабочую копию
#   DEPLOY_GIT_PULL=1 ./scripts/deploy.sh    # сначала git pull (ветка из .env)
#   DEPLOY_GIT_REF=<sha> DEPLOY_GIT_PULL=1 ./scripts/deploy.sh   # выкатить конкретный коммит
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${ENV_FILE:-.env}"
COMPOSE="${COMPOSE:-docker compose}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"

log() { printf '\033[0;34m[%s]\033[0m %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*"; }
ok() { printf '\033[0;32m[OK]\033[0m %s\n' "$*"; }
fail() {
    printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2
    exit 1
}

[ -f "$ENV_FILE" ] || fail "Файл $ENV_FILE не найден: конфигурация и секреты должны лежать в .env"

set -a
# shellcheck disable=SC1090
. "./$ENV_FILE"
set +a

DEPLOY_GIT_PULL="${DEPLOY_GIT_PULL:-0}"
DEPLOY_GIT_REF="${DEPLOY_GIT_REF:-}"

command -v docker >/dev/null 2>&1 || fail "docker не найден в PATH"
# shellcheck disable=SC2086
$COMPOSE version >/dev/null 2>&1 || fail "docker compose недоступен"

if [ "$DEPLOY_GIT_PULL" = "1" ]; then
    command -v git >/dev/null 2>&1 || fail "DEPLOY_GIT_PULL=1, но git не найден"
    log "Обновление рабочей копии (git fetch origin)"
    git -C "$REPO_ROOT" fetch --prune origin
    if [ -n "$DEPLOY_GIT_REF" ]; then
        log "Переключение на $DEPLOY_GIT_REF"
        git -C "$REPO_ROOT" checkout --force "$DEPLOY_GIT_REF"
    else
        log "git pull --ff-only"
        git -C "$REPO_ROOT" pull --ff-only
    fi
    ok "Код обновлён: $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
fi

log "Сборка образов"
# shellcheck disable=SC2086
$COMPOSE build

log "Применение миграций и запуск сервисов"
# shellcheck disable=SC2086
$COMPOSE up -d --remove-orphans

log "Ожидание миграций (до ${HEALTH_TIMEOUT}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
while :; do
    state="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' voenmeh_migrate 2>/dev/null || echo 'missing')"
    case "$state" in
        "exited 0")
            ok "Миграции применены"
            break
            ;;
        "exited "* | "dead "*)
            # shellcheck disable=SC2086
            $COMPOSE logs --tail=40 migrate >&2 || true
            fail "Сервис migrate завершился с ошибкой ($state)"
            ;;
    esac
    if [ "$(date +%s)" -ge "$deadline" ]; then
        # shellcheck disable=SC2086
        $COMPOSE logs --tail=40 migrate >&2 || true
        fail "Миграции не завершились за ${HEALTH_TIMEOUT}s (состояние: $state)"
    fi
    sleep 3
done

log "Проверка контейнеров"
for container in voenmeh_bot voenmeh_worker; do
    deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
    while ! docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null | grep -q true; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            # shellcheck disable=SC2086
            $COMPOSE logs --tail=60 bot worker >&2 || true
            fail "Контейнер $container не запустился"
        fi
        sleep 3
    done
    ok "$container — running"
done

log "Статус сервисов"
# shellcheck disable=SC2086
$COMPOSE ps

ok "Деплой завершён (${ENV_FILE})"
