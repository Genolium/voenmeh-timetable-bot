# Используем официальный образ Grafana как базовый
FROM grafana/grafana:11.1.0

# Копируем нашу конфигурацию provisioning внутрь образа.
# Grafana автоматически подхватит файлы из этой директории при старте.
COPY ./provisioning/ /etc/grafana/provisioning/