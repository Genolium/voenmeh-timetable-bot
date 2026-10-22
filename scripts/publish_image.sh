#!/usr/bin/env bash
# Публикация образа бота в Docker Hub.
#
# Все секреты берутся из .env (DOCKER_USERNAME / DOCKER_PASSWORD) — так же,
# как и остальная конфигурация проекта. Скрипт рассчитан на запуск с хоста,
# где развёрнут бот, либо с любого сервера, на котором есть .env.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${ENV_FILE:-.env}"
IMAGE_NAME="${IMAGE_NAME:-genolium/voenmeh-timetable-bot}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
# Список тегов через пробел; TAG — короткая запись для одного тега.
TAGS="${TAGS:-${TAG:-latest}}"

if [ ! -f "$ENV_FILE" ]; then
    echo "Файл $ENV_FILE не найден: секреты публикации должны лежать в .env" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "./$ENV_FILE"
set +a

if [ -z "${DOCKER_USERNAME:-}" ] || [ -z "${DOCKER_PASSWORD:-}" ]; then
    echo "В $ENV_FILE нет DOCKER_USERNAME/DOCKER_PASSWORD — публикация невозможна" >&2
    exit 1
fi

echo "==> docker login (${DOCKER_USERNAME})"
printf '%s' "$DOCKER_PASSWORD" | docker login --username "$DOCKER_USERNAME" --password-stdin

BUILD_ARGS=()
for tag in $TAGS; do
    BUILD_ARGS+=(--tag "${IMAGE_NAME}:${tag}")
done

echo "==> build & push ${IMAGE_NAME} [${TAGS}] (${PLATFORMS})"
docker buildx build --platform "$PLATFORMS" "${BUILD_ARGS[@]}" --push .

echo "==> опубликовано: ${TAGS}"
