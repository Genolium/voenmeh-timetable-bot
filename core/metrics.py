from prometheus_client import Counter, Gauge, Histogram

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