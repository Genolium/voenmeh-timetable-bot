# Справочник модулей (Module Reference)

Полный каталог всех модулей репозитория `voenmeh-timetable-bot` с описанием ответственности, ключевых сущностей и зависимостей.

---

## 1. Точка входа

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`main.py`](../main.py) | Точка входа приложения: логирование, OpenTelemetry, инициализация `TimetableManager` / `UserDataManager` / `SemesterSettingsManager`, регистрация middleware, диалогов, роутеров, планировщика, серверов метрик и вебхуков, graceful shutdown. | `setup_logging`, `configure_bot_profile`, `AdminDialogGuardMiddleware`, `set_bot_commands`, `start_command_handler`, `override_command_handler`, `error_handler`, `main` |

---

## 2. Ядро (`core/`)

### 2.1. Конфигурация, локализация и текстовые утилиты

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`core/config.py`](../core/config.py) | Конфигурация на базе `pydantic-settings` (`.env`), часовой пояс `MOSCOW_TZ`, константы URL/Redis/путей, парсинг `ADMIN_IDS`, фабрика `get_redis_client()`. | `Settings`, `settings`, `ADMIN_IDS`, `MOSCOW_TZ`, `DAY_MAP`, `MEDIA_PATH`, `news_channel_url`, `get_redis_client` |
| [`core/time_utils.py`](../core/time_utils.py) | Единые хелперы времени: naive `Europe/Moscow` для хранения в БД и aware UTC для протоколов/метрик. | `now_msk_naive`, `to_msk_naive`, `utc_now_aware` |
| [`core/text_utils.py`](../core/text_utils.py) | Нормализация кодов групп (латиница → кириллица, фильтрация допустимых символов) и транслитерация ошибочного латинского ввода фамилий. | `TRANSLIT_TABLE`, `NAME_TRANSLIT_TABLE`, `normalize_group_name`, `transliterate_failed_search` |
| [`core/transliteration.py`](../core/transliteration.py) | Транслитерация кириллицы в латиницу (`cyrtranslit` + pure-Python fallback), перевод расписания и названий дней недели (`ru`/`en`/`zh`). | `transliterate`, `transliterate_schedule_data`, `get_day_name` |
| [`core/i18n.py`](../core/i18n.py) | Загрузка и форматирование локалей (`locales/ru.json`, `en.json`, `zh.json`) с fallback на язык по умолчанию. | `I18n`, `i18n` |
| [`core/bot_identity.py`](../core/bot_identity.py) | Кэш актуального `@username` бота в памяти процесса (из `bot.get_me()`) с fallback. | `set_bot_username`, `get_bot_username`, `bot_mention` |
| [`core/custom_emojis.py`](../core/custom_emojis.py) | Реестр Telegram Custom Emoji ID, безопасная подстановка `<tg-emoji>` в HTML-сообщения и обогащение inline-кнопок (`icon_custom_emoji_id`, `style`). | `EMOJI_TO_CUSTOM_ID`, `replace_emojis_html`, `resolve_button_style`, `transform_inline_button`, `transform_inline_keyboard` |

### 2.2. База данных, миграции и резервные копии (`core/db/`)

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`core/db/__init__.py`](../core/db/__init__.py) | Реэкспорт `Base` и всех 9 ORM-моделей. | `__all__` |
| [`core/db/models.py`](../core/db/models.py) | Декларативные ORM-модели SQLAlchemy 2.0 и композитные индексы БД. | `Base`, `User`, `SemesterSettings`, `Feedback`, `ScheduleChangeLog`, `FailedMessage`, `SubscriptionFilterSetting`, `RequiredChannel`, `UserLessonRule`, `GroupChat` |
| [`core/db/migrate.py`](../core/db/migrate.py) | Программный запуск `alembic upgrade head` и проверка актуальности ревизии схемы на старте. | `run_migrations`, `assert_schema_current` |
| [`core/db/backups.py`](../core/db/backups.py) | Создание SQL-дампов (`pg_dump`), восстановление (`psql` + миграции), санитизация дампов (защита от `psql` meta-команд), gzip-сжатие и защищённая от gzip-бомб распаковка. | `BackupError`, `DatabaseConnectionInfo`, `create_db_backup`, `restore_db_backup`, `compress_file_gzip`, `maybe_decompress_gzip` |

### 2.3. Доменная логика расписания, календаря и экспорта

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`core/parser.py`](../core/parser.py) | Асинхронный парсер расписания с 3-уровневым fallback (`voenmeh.su` JSON → `voenmeh.ru` XML через `defusedxml` → локальный `data/fallback_schedule.json`), построением индексов преподавателей/аудиторий и условными GET-запросами. | `fetch_and_parse_all_schedules`, `fetch_and_parse_from_voenmeh_su`, `load_fallback_schedule`, `save_fallback_schedule`, `NOT_MODIFIED` |
| [`core/manager.py`](../core/manager.py) | Центральный менеджер расписания (кэш Redis с gzip-сжатием, резервные копии, поиск групп/преподавателей/аудиторий точным и fuzzy-методом RapidFuzz, кэшированная O(1) нормализация ФИО, интеграция коррекций). | `TimetableManager`, `refresh_schedule_cache_ttl` |
| [`core/schedule_diff.py`](../core/schedule_diff.py) | Выявление изменений между версиями расписания по группам/дням и форматирование HTML-уведомлений с учётом i18n. | `ChangeType`, `LessonChange`, `GroupScheduleDiff`, `ScheduleDiffDetector`, `ScheduleDiffFormatter` |
| [`core/schedule_overrides.py`](../core/schedule_overrides.py) | Хранение временных административных коррекций расписания в Redis (TTL 30 дней). | `set_group_override`, `get_group_override`, `clear_group_override` |
| [`core/semester_calendar.py`](../core/semester_calendar.py) | Чистый расчёт учебного семестра (`fall`/`spring`), номера недели и чётности (`odd`/`even`). | `SemesterInfo`, `get_semester_info`, `SEMESTER_LENGTH_WEEKS` |
| [`core/semester_settings.py`](../core/semester_settings.py) | CRUD-менеджер дат начала семестров в PostgreSQL. | `SemesterSettingsManager` |
| [`core/user_schedule_rules.py`](../core/user_schedule_rules.py) | Персональные правила расписания (скрытие предметов, заметки, свои пары), детерминированный хэш правил для кэша изображений, CRUD в PostgreSQL + Redis. | `apply_rules_to_lessons`, `apply_rules_to_weeks`, `rules_fingerprint`, `UserScheduleRulesManager` |
| [`core/summary_advisor.py`](../core/summary_advisor.py) | Анализ учебной нагрузки дня (окно пар, перерывы, плотность) и формирование сезонных погодных рекомендаций (`ru`/`en`/`zh`). | `analyze_lessons_window`, `build_schedule_briefing`, `build_weather_summary_block` |
| [`core/ics_exporter.py`](../core/ics_exporter.py) | Экспорт 2-недельного расписания группы или преподавателя в формат iCalendar (RFC 5545 `.ics`) с `VTIMEZONE` `Europe/Moscow` и `RRULE`. | `collect_target_weeks`, `generate_schedule_ics`, `build_ics_filename` |
| [`core/weather_api.py`](../core/weather_api.py) | Асинхронный клиент OpenWeatherMap (5-day/3h forecast) с кэшированием, ретраями, маскированием API-ключа в логах и расчётом погодного окна под пары. | `WeatherAPI`, `_scrub_appid` |

### 2.4. Пользователи, аналитика, обратная связь, подписки и алерты

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`core/user_data.py`](../core/user_data.py) | Основной слой работы с таблицей `users` (PostgreSQL + `@cached` в Redis), выборки аудиторий для рассылок, аналитика DAU/WAU/MAU, история поиска в Redis. | `UserDataManager`, `cached` |
| [`core/analytics.py`](../core/analytics.py) | Счётчики популярности функций в Redis и генерация PNG-графиков динамики активности (`matplotlib` Agg). | `track_feature_click`, `get_top_features`, `generate_dynamics_chart` |
| [`core/admin_reports.py`](../core/admin_reports.py) | Формирование и отправка ежедневных/еженедельных/ежемесячных отчётов администраторам (БД + Redis + Prometheus + SQL-бэкап). | `AdminReportsGenerator`, `send_daily_reports`, `send_weekly_reports`, `send_monthly_reports` |
| [`core/audit_manager.py`](../core/audit_manager.py) | Запись и чтение истории изменений расписания (`ScheduleChangeLog`). | `AuditManager` |
| [`core/dlq_manager.py`](../core/dlq_manager.py) | Управление Dead-Letter Queue (PostgreSQL `FailedMessage` + ограниченный список в Redis). | `DLQManager` |
| [`core/feedback_manager.py`](../core/feedback_manager.py) | CRUD и статистика обращений пользователей (`Feedback`). | `FeedbackManager` |
| [`core/group_chat_manager.py`](../core/group_chat_manager.py) | Управление привязками и настройками рассылок групповых чатов (`GroupChat`), миграция супергрупп. | `GroupChatManager`, `ALLOWED_GROUP_SETTINGS` |
| [`core/subscription_filter.py`](../core/subscription_filter.py) | Проверка обязательной подписки на каналы (`RequiredChannel` + Redis кэш + fallback на `SUBSCRIPTION_CHANNEL`). | `SubscriptionFilterManager`, `is_user_subscribed` |
| [`core/metrics.py`](../core/metrics.py) | Реестр всех Prometheus-метрик приложения и сбор системных метрик (`psutil`). | `EVENTS_PROCESSED`, `HANDLER_DURATION`, `ERRORS_TOTAL`, `RETRIES_TOTAL`, `collect_system_metrics` |
| [`core/telemetry.py`](../core/telemetry.py) | Инициализация и корректное завершение OpenTelemetry (OTLP HTTP: traces, metrics, logs). | `TelemetryHandles`, `setup_telemetry`, `shutdown_telemetry` |
| [`core/alert_sender.py`](../core/alert_sender.py) | Мультиканальный отправитель алертов (Slack, Discord, Telegram, HTTP webhook) с фильтрацией по severity. | `AlertSender` |
| [`core/alert_webhook.py`](../core/alert_webhook.py) | `aiohttp.web`-сервер `/health`, `/readiness` и `/alerts` (Alertmanager → Telegram-админы). | `format_alertmanager_message`, `create_alert_app`, `run_alert_webhook_server` |
| [`core/business_alerts.py`](../core/business_alerts.py) | Фоновый мониторинг KPI-метрик Prometheus с cooldown и отправкой алертов через `AlertSender`. | `BusinessMetricsMonitor`, `start_business_monitoring` |

### 2.5. Рендеринг и кэширование изображений

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`core/image_generator.py`](../core/image_generator.py) | Рендеринг HTML из `templates/schedule_template.html` и съёмка PNG-скриншотов через пул Playwright Chromium. | `generate_schedule_image`, `shutdown_image_generator`, `resolve_week_slug` |
| [`core/image_service.py`](../core/image_service.py) | Оркестрация выдачи/генерации картинок расписания (кэш, Redis-блокировка, локальный или Dramatiq-рендер, сжатие, отправка/редактирование в Telegram). | `ImageService` |
| [`core/image_cache_manager.py`](../core/image_cache_manager.py) | Файловый и Redis-кэш PNG-изображений, нормализация ключей с защитой от path traversal, контроль дисковой квоты и инвалидация. | `ImageCacheManager`, `sanitize_group_key`, `sanitize_cache_key`, `invalidate_generated_images_for_groups`, `invalidate_all_generated_images` |
| [`core/render_config.py`](../core/render_config.py) | Фиксированные параметры вьюпорта рендеринга (`3000×2250`). | `VIEWPORT_WIDTH`, `VIEWPORT_HEIGHT` |
| [`core/themes.py`](../core/themes.py) | Единый реестр всех 17 визуальных тем расписания и allowlist-валидация. | `ThemeMeta`, `THEMES`, `THEME_BY_ID`, `is_valid_theme`, `get_theme_meta` |
| [`templates/schedule_template.html`](../templates/schedule_template.html) | Jinja2 HTML/CSS/JS-шаблон карточки расписания на неделю (17 тем, CSP, автоподгонка шрифтов). | `window.__fitScheduleTexts` |

---

## 3. Слой Telegram-бота (`bot/`)

### 3.1. Планировщик, фоновые задачи, форматирование и утилиты

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`bot/scheduler.py`](../bot/scheduler.py) | Настройка `AsyncIOScheduler` и реализация всех периодических задач (сводки, напоминания, мониторинг обновлений, диффы, выпустившиеся группы, бэкапы). | `TimetableManagerRef`, `setup_scheduler`, `evening_broadcast`, `morning_summary_broadcast`, `minutely_reminders_checker`, `monitor_schedule_changes`, `handle_graduated_groups` |
| [`bot/tasks.py`](../bot/tasks.py) | Настройка Dramatiq `RabbitmqBroker` и фоновые акторы доставки сообщений/медиа/напоминаний/генерации картинок с DLQ и rate limiting. | `send_message_task`, `copy_message_task`, `send_media_task`, `send_lesson_reminder_task`, `generate_week_image_task`, `check_theme_subscription_task`, `send_week_original_if_subscribed_task` |
| [`bot/text_formatters.py`](../bot/text_formatters.py) | HTML-форматирование дневного/недельного расписания (группы, преподаватели, аудитории), динамических заголовков с прогресс-баром, сводок и напоминаний. | `esc`, `format_schedule_text`, `format_teacher_schedule_text`, `format_classroom_schedule_text`, `format_full_week_text`, `generate_dynamic_header`, `generate_reminder_text` |
| [`bot/utils/image_compression.py`](../bot/utils/image_compression.py) | Проверка габаритов/размера PNG под лимиты Telegram и JPEG-сжатие через Pillow. | `compress_image_for_telegram`, `get_telegram_safe_image_path` |
| [`bot/utils/worker_loop.py`](../bot/utils/worker_loop.py) | Постоянный фоновый поток `AsyncWorkerLoop` с `asyncio` event loop для синхронных акторов Dramatiq. | `get_worker_loop`, `stop_worker_loop`, `run_async` |

### 3.2. Хэндлеры (`bot/handlers/`)

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`bot/handlers/feedback_reply_handler.py`](../bot/handlers/feedback_reply_handler.py) | Ответы администраторов на обращения пользователей из чата `FEEDBACK_CHAT_ID` с фиксацией статуса в БД. | `feedback_reply_router`, `handle_feedback_reply` |
| [`bot/handlers/group_chat_router.py`](../bot/handlers/group_chat_router.py) | Режим рассылок в групповых чатах: приветствие, миграция супергрупп, команда `/group`, inline-переключатели настроек. | `group_chat_router`, `on_bot_group_membership_changed`, `on_group_migrated`, `on_group_command`, `on_group_config_callback` |
| [`bot/handlers/inline_handlers.py`](../bot/handlers/inline_handlers.py) | Inline-режим (`@bot <группа|преподаватель> <день>`) с парсингом дней на RU/EN/ZH и fuzzy-подсказками. | `parse_day_from_query`, `inline_query_handler` |

### 3.3. Диалоги (`bot/dialogs/`)

| Модуль | Назначение | Ключевые символы |
|---|---|---|
| [`bot/dialogs/states.py`](../bot/dialogs/states.py) | FSM-группы состояний всех диалогов. | `MainMenu`, `Schedule`, `SettingsMenu`, `FindMenu`, `About`, `Feedback`, `Admin` |
| [`bot/dialogs/constants.py`](../bot/dialogs/constants.py) | Перечисления ключей `dialog_data` и идентификаторов виджетов. | `DialogDataKeys`, `WidgetIds` |
| [`bot/dialogs/main_menu.py`](../bot/dialogs/main_menu.py) | Онбординг: выбор языка, роли (студент/преподаватель), ввод группы/ФИО с fuzzy-подсказками. | `dialog` (`main_menu_dialog`) |
| [`bot/dialogs/schedule_view.py`](../bot/dialogs/schedule_view.py) | Основной экран расписания: навигация по дням, картинка на неделю, выдача оригинала (по подписке), текстовая неделя, `.ics`-экспорт, персональные правила (`customize`). | `schedule_dialog`, `on_send_original_file_callback`, `on_check_subscription_callback`, `on_inline_back` |
| [`bot/dialogs/settings_menu.py`](../bot/dialogs/settings_menu.py) | Настройки уведомлений, времени утренней/вечерней сводки и напоминаний, языка, роли и визуальной темы. | `settings_dialog` |
| [`bot/dialogs/find_menu.py`](../bot/dialogs/find_menu.py) | Поиск по группам, преподавателям, аудиториям и история поиска. | `find_dialog` |
| [`bot/dialogs/about_menu.py`](../bot/dialogs/about_menu.py) | 5-страничный интерактивный туториал со скриншотами (`/about`). | `about_dialog` |
| [`bot/dialogs/feedback_menu.py`](../bot/dialogs/feedback_menu.py) | Приём текстовых и медиа-обращений (`/feedback`) с сохранением в БД и пересылкой в `FEEDBACK_CHAT_ID`. | `feedback_dialog` |
| [`bot/dialogs/admin_menu.py`](../bot/dialogs/admin_menu.py) | Админ-панель (`/admin`): массовые и сегментированные рассылки, статистика и графики, бэкап/восстановление БД, коррекции расписания, семестры, подписки, кэш, пользователи. | `admin_dialog`, `on_cancel_generation` |

### 3.4. Middleware (`bot/middlewares/`)

| Модуль | Назначение |
|---|---|
| [`event_utils.py`](../bot/middlewares/event_utils.py) | Извлечение `from_user`, `message`, `text`, `callback_data` и типа события из `Update`. |
| [`rate_limit_middleware.py`](../bot/middlewares/rate_limit_middleware.py) | Персональный sliding-window анти-флуд в Redis. |
| [`manager_middleware.py`](../bot/middlewares/manager_middleware.py) | Внедрение актуального `TimetableManager`. |
| [`user_data_middleware.py`](../bot/middlewares/user_data_middleware.py) | Внедрение `UserDataManager` и регистрация/обновление пользователя. |
| [`session_middleware.py`](../bot/middlewares/session_middleware.py) | Внедрение `async_sessionmaker` (`session_factory`). |
| [`activity_logging_middleware.py`](../bot/middlewares/activity_logging_middleware.py) | Структурированное логирование активности и сбор Prometheus-метрик использования. |
| [`logging_middleware.py`](../bot/middlewares/logging_middleware.py) | OpenTelemetry-спаны `aiogram.event` и гистограммы времени выполнения хэндлеров. |
| [`i18n_middleware.py`](../bot/middlewares/i18n_middleware.py) | Определение локали пользователя и внедрение `i18n` / `_`. |
| [`subscription_filter_middleware.py`](../bot/middlewares/subscription_filter_middleware.py) | Gatekeeper обязательной подписки на каналы. |
| [`custom_emoji_middleware.py`](../bot/middlewares/custom_emoji_middleware.py) | Request-middleware подстановки `<tg-emoji>` и стилей кнопок Bot API 9.4. |
| [`network_retry_middleware.py`](../bot/middlewares/network_retry_middleware.py) | IPv4-сессия `FastConnectAiohttpSession` и безопасные сетевые ретраи `NetworkRetryRequestMiddleware`. |

---

## 4. Скрипты эксплуатации и разработки (`scripts/` и корень)

| Файл | Назначение |
|---|---|
| [`scripts/deploy.sh`](../scripts/deploy.sh) | Деплой на хосте: `git pull` (опционально), `docker compose build`, применение миграций, запуск и проверка контейнеров. |
| [`scripts/publish_image.sh`](../scripts/publish_image.sh) | Сборка и публикация multi-arch Docker-образа (`amd64`/`arm64`) в Docker Hub. |
| [`scripts/restart_services.sh`](../scripts/restart_services.sh) | Утилита точечного или полного перезапуска сервисов Docker Compose. |
| [`scripts/check_mypy_ratchet.py`](../scripts/check_mypy_ratchet.py) | CI-проверка, гарантирующая, что число ошибок `mypy` не растёт выше `mypy_baseline`. |
| [`scripts/system_monitor.py`](../scripts/system_monitor.py) | Асинхронный мониторинг CPU/RAM/диска, здоровья Redis и очередей RabbitMQ. |
| [`scripts/graceful_shutdown.py`](../scripts/graceful_shutdown.py) | Обработчик `SIGTERM`/`SIGINT` для остановки Docker-контейнеров. |
| [`scripts/optimize_backgrounds.py`](../scripts/optimize_backgrounds.py) | Оптимизация фоновых изображений тем в `assets/`. |
| [`scripts/generate_themes_showcase.py`](../scripts/generate_themes_showcase.py) | Генерация витрины всех тем (`bot/media/themes_showcase.jpg`). |
| [`generate_all_images.py`](../generate_all_images.py) / [`generate_test_image.py`](../generate_test_image.py) / [`test_themes.py`](../test_themes.py) | Локальные утилиты пакетной/тестовой генерации карточек расписания. |
