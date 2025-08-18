#!/bin/sh

set -e

# Проверяем, существуют ли необходимые переменные окружения
if [ -z "${POSTGRES_USER}" ] || [ -z "${POSTGRES_DB}" ]; then
  echo "Ошибка: Переменные POSTGRES_USER и POSTGRES_DB должны быть установлены."
  exit 1
fi

# Пути к файлам
TEMPLATE_FILE="/pgadmin4/servers.json.template"
CONFIG_FILE="/tmp/servers.json"

# Заменяем плейсхолдеры в шаблоне на значения из переменных окружения
# и создаем итоговый файл servers.json во временной директории
sed "s/\${POSTGRES_DB}/${POSTGRES_DB}/g; s/\${POSTGRES_USER}/${POSTGRES_USER}/g" "${TEMPLATE_FILE}" > "${CONFIG_FILE}"

# Копируем файл в директорию pgAdmin с правильными правами
cp "${CONFIG_FILE}" "/var/lib/pgadmin/servers.json"
chmod 644 "/var/lib/pgadmin/servers.json"

# Также копируем в альтернативное расположение для совместимости
mkdir -p "/var/lib/pgadmin/pgadmin4"
cp "${CONFIG_FILE}" "/var/lib/pgadmin/pgadmin4/servers.json"
chmod 644 "/var/lib/pgadmin/pgadmin4/servers.json"

echo "Файл servers.json успешно сгенерирован в /var/lib/pgadmin/ и /var/lib/pgadmin/pgadmin4/"

echo "Файл servers.json успешно сгенерирован в /var/lib/pgadmin/"

# Запускаем оригинальный entrypoint pgAdmin
exec /entrypoint.sh
