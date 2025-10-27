# Используем официальный образ Prometheus как базовый
FROM prom/prometheus:v2.53.0

# Копируем наш файл конфигурации в директорию внутри образа,
# где Prometheus ожидает его найти.
COPY ./prometheus.yml /etc/prometheus/prometheus.yml
COPY ./alerting_rules.yml /etc/prometheus/alerting_rules.yml
CMD ["--config.file=/etc/prometheus/prometheus.yml"]
