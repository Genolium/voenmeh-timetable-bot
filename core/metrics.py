from prometheus_client import Counter, Gauge, Histogram, Summary

# Счетчик обработанных событий (сообщений, колбэков и т.д.)
# 'event_type' - это метка (label), по которой можно будет фильтровать (например, 'Message', 'CallbackQuery')
EVENTS_PROCESSED = Counter(
    'bot_events_processed_total',
    'Total count of events processed by the bot',
    ['event_type']
)

# Датчик (gauge) для отслеживания текущего количества пользователей в базе данных.
# Значение может увеличиваться или уменьшаться.
USERS_TOTAL = Gauge(
    'bot_users_total',
    'Total number of users in the database'
)

# Датчик для количества пользователей с активными подписками на рассылки.
SUBSCRIBED_USERS = Gauge(
    'bot_subscribed_users',
    'Number of users with at least one active subscription'
)

# Гистограмма для измерения времени ответа хэндлеров.
# Позволяет собирать статистику по распределению времени выполнения (среднее, перцентили).
# 'handler_name' - метка для идентификации конкретного хэндлера.
HANDLER_DURATION = Histogram(
    'bot_handler_duration_seconds',
    'Duration of handler processing',
    ['handler_name']
)

# Счетчик для задач, отправленных в очередь Dramatiq.
# 'actor_name' - метка для идентификации конкретного актора Dramatiq.
TASKS_SENT_TO_QUEUE = Counter(
    'bot_tasks_sent_total',
    'Total count of tasks sent to the queue',
    ['actor_name']
)

# Общие ошибки по источникам (handler, parser, weather, db, tasks)
ERRORS_TOTAL = Counter(
    'bot_errors_total',
    'Total count of errors by source',
    ['source']
)

# Ретраи по компонентам (weather, parser, tasks)
RETRIES_TOTAL = Counter(
    'bot_retries_total',
    'Total retry attempts by component',
    ['component']
)

# Момент последнего успешного обновления расписания (unix timestamp)
LAST_SCHEDULE_UPDATE_TS = Gauge(
    'bot_last_schedule_update_timestamp',
    'Unix timestamp of last successful schedule update'
)

# ===== НОВЫЕ МЕТРИКИ ДЛЯ КЭШИРОВАНИЯ ИЗОБРАЖЕНИЙ =====

# Счетчик попаданий в кэш изображений
IMAGE_CACHE_HITS = Counter(
    'bot_image_cache_hits_total',
    'Total count of image cache hits',
    ['cache_type']  # week_schedule, daily_schedule, etc.
)

# Счетчик промахов кэша изображений
IMAGE_CACHE_MISSES = Counter(
    'bot_image_cache_misses_total',
    'Total count of image cache misses',
    ['cache_type']  # redis, expired, file_missing, error
)

# Размер кэша изображений
IMAGE_CACHE_SIZE = Gauge(
    'bot_image_cache_size',
    'Current size of image cache',
    ['cache_type']  # files, size_mb
)

# Операции с кэшем изображений
IMAGE_CACHE_OPERATIONS = Counter(
    'bot_image_cache_operations_total',
    'Total count of cache operations',
    ['operation']  # store, delete, cleanup
)

# Время генерации расписания
SCHEDULE_GENERATION_TIME = Histogram(
    'bot_schedule_generation_duration_seconds',
    'Time taken to generate schedule images',
    ['schedule_type']  # week, daily
)

# ===== БИЗНЕС-МЕТРИКИ =====

# Активность пользователей по дням
USER_ACTIVITY_DAILY = Counter(
    'bot_user_activity_daily_total',
    'Daily user activity count',
    ['action_type', 'user_group']  # view_schedule, search, settings, etc.
)

# Популярность групп
GROUP_POPULARITY = Counter(
    'bot_group_popularity_total',
    'Group popularity based on schedule views',
    ['group_name']
)

# Время сессии пользователя
USER_SESSION_DURATION = Histogram(
    'bot_user_session_duration_seconds',
    'User session duration',
    ['user_type']  # new_user, returning_user, active_user
)

# Конверсия пользователей (от первого использования до регулярного)
USER_CONVERSION = Counter(
    'bot_user_conversion_total',
    'User conversion events',
    ['conversion_stage']  # first_use, daily_use, weekly_use, monthly_use
)

# Качество поиска
SEARCH_QUALITY = Summary(
    'bot_search_quality_score',
    'Search result quality score',
    ['search_type']  # teacher, classroom, subject
)

# ===== МЕТРИКИ ПРОИЗВОДИТЕЛЬНОСТИ =====

# Время ответа API
API_RESPONSE_TIME = Histogram(
    'bot_api_response_time_seconds',
    'API response time',
    ['api_endpoint', 'method']
)

# Использование памяти
MEMORY_USAGE = Gauge(
    'bot_memory_usage_bytes',
    'Current memory usage',
    ['memory_type']  # rss, vms, shared
)

# Количество активных соединений
ACTIVE_CONNECTIONS = Gauge(
    'bot_active_connections',
    'Number of active connections',
    ['connection_type']  # database, redis, external_api
)

# ===== МЕТРИКИ ОШИБОК И СТАБИЛЬНОСТИ =====

# Время безотказной работы
UPTIME_SECONDS = Gauge(
    'bot_uptime_seconds',
    'Bot uptime in seconds'
)

# Количество перезапусков
RESTART_COUNT = Counter(
    'bot_restart_count_total',
    'Total number of bot restarts'
)

# Время последнего обновления данных
LAST_DATA_UPDATE = Gauge(
    'bot_last_data_update_timestamp',
    'Timestamp of last data update',
    ['data_type']  # schedule, weather, notifications
)

# Статус внешних сервисов
EXTERNAL_SERVICE_STATUS = Gauge(
    'bot_external_service_status',
    'Status of external services',
    ['service_name']  # weather_api, schedule_parser, notification_service
)

# ===== МЕТРИКИ УВЕДОМЛЕНИЙ =====

# Доставка уведомлений
NOTIFICATION_DELIVERY = Counter(
    'bot_notification_delivery_total',
    'Notification delivery statistics',
    ['delivery_status', 'notification_type']  # success, failed, schedule, weather, reminder
)

# Время доставки уведомлений
NOTIFICATION_DELIVERY_TIME = Histogram(
    'bot_notification_delivery_time_seconds',
    'Time taken to deliver notifications',
    ['notification_type']
)

# ===== МЕТРИКИ ПАРСЕРА =====

# Статистика парсинга
PARSER_STATS = Counter(
    'bot_parser_operations_total',
    'Parser operation statistics',
    ['operation', 'status']  # parse_schedule, update_data, success, failed
)

# Время парсинга
PARSER_DURATION = Histogram(
    'bot_parser_duration_seconds',
    'Time taken for parsing operations',
    ['operation_type']
)

# ===== МЕТРИКИ БАЗЫ ДАННЫХ =====

# Операции с БД
DATABASE_OPERATIONS = Counter(
    'bot_database_operations_total',
    'Database operation statistics',
    ['operation', 'table']  # select, insert, update, delete, users, schedules
)

# Время выполнения запросов
DATABASE_QUERY_TIME = Histogram(
    'bot_database_query_duration_seconds',
    'Database query execution time',
    ['query_type', 'table']
)

# Размер БД
DATABASE_SIZE = Gauge(
    'bot_database_size_bytes',
    'Database size in bytes',
    ['table_name']
)