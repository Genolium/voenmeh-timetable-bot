# Отчёт по аудиту кода, безопасности и оптимизации

Аудит выполнен по ветке `main` (`31ff56d`) репозитория `Genolium/voenmeh-timetable-bot` с полным пофайловым разбором всех модулей `main.py`, `core/`, `bot/`, `templates/` и `scripts/`.

---

## 1. Сводка автоматизированного анализа

| Инструмент | До правок | После правок | Комментарий |
|---|---|---|---|
| `pip-audit -r requirements.lock` | 0 уязвимостей | 0 уязвимостей | Зависимости чисты |
| `bandit -r bot/ core/ main.py -lll` | 3 High (`B324`), 0 Medium | **0 High, 0 Medium** | Во всех `hashlib.md5` добавлен `usedforsecurity=False` |
| `flake8` (критические `E9,F63,F7,F82`) | 0 | **0** | Синтаксис и имена чисты |
| `black --check` / `isort` | pass | **pass** | Единый стиль кода |
| `scripts/check_mypy_ratchet.py` | 263 / 265 | **260 / 265** | Ошибок меньше бейзлайна |

---

## 2. Критические и высокие проблемы — ИСПРАВЛЕНО

### 2.1. Инъекции, RCE, Path Traversal и XSS/HTML

1. **[Critical — RCE] `core/db/backups.py` (`_sanitize_dump_file`, `maybe_decompress_gzip`, `create_db_backup`, `restore_db_backup`)**
   - **Проблема:** `psql -f` исполнял произвольные shell/файловые мета-команды (`\!`, `\i`, `\o`, `\copy ... FROM PROGRAM`), при этом `_sanitize_dump_file` удалял только `\restrict`/`\unrestrict`; `maybe_decompress_gzip` не ограничивал размер распаковки (gzip-бомба); `create_db_backup` не валидировал `filename_prefix`, использовал предсказуемые имена с секундной точностью и передавал весь `os.environ` (включая `BOT_TOKEN` и ключи API) в дочерние процессы `pg_dump`/`psql`.
   - **Исправление:**
     - Заблокированы опасные `psql` мета-команды вне блоков `COPY ... FROM stdin`, добавлена поддержка UTF-8 BOM (`utf-8-sig`) и корректная обработка `UnicodeDecodeError`.
     - Введена потоковая распаковка в `maybe_decompress_gzip` с лимитом `MAX_DECOMPRESSED_BYTES = 256 MiB` и удалением битых/частичных файлов при ошибке.
     - Добавлена валидация `filename_prefix` (`^[A-Za-z0-9_-]{1,64}$`), уникальный суффикс `uuid4`, `chmod 0o600` на POSIX, минимальный allowlist окружения (`_prepare_connection_env`, `_prepare_migrate_env`), `field(repr=False)` для пароля и обрезка вывода ошибок до 1000 символов.

2. **[Critical — Path Traversal & Temp Leak] `bot/dialogs/admin_menu.py` (`on_backup_upload_received`, `on_backup_restore_typed`)**
   - **Проблема:** Недоверенный `document.file_name` подставлялся напрямую в `tmp_dir / file_name` (позволял выйти за пределы `tmp_dir` через `../`), `file_size is None` обходил лимит 20 МБ, а после распаковки `.gz` временный `.sql` не удалялся и оставался в `/tmp/voenmeh_restore_*` в открытом виде; отсутствовала блокировка от конкурентного восстановления БД.
   - **Исправление:** Сохранение только под фиксированным именем (`backup.sql` / `backup.sql.gz`), проверка фактического размера после скачивания, полная очистка каталога через `shutil.rmtree` (`_cleanup_restore_dir`), `asyncio.Lock` (`_restore_lock`), перенос распаковки в `asyncio.to_thread` и `html.escape` текста исключений.

3. **[Critical — Path Traversal] `bot/tasks.py` (`send_week_original_if_subscribed_task`), `core/image_cache_manager.py`, `core/image_service.py`, `bot/dialogs/schedule_view.py`**
   - **Проблема:** `cache_key` интерполировался напрямую в `MEDIA_PATH / "generated" / f"{cache_key}.png"` без проверки выхода за пределы каталога; `ImageService` и `invalidate_generated_images_for_groups` по-разному нормализовали группы с `/`, из-за чего кэш таких групп не инвалидировался; невалидные темы создавали лишние файлы на диске.
   - **Исправление:** Введены единые функции `sanitize_group_key()` и `sanitize_cache_key()`, строгая проверка `Path.resolve().is_relative_to(...)`, нормализация темы через `get_theme_meta()`, уникальные имена `.tmp` файлов при записи, `glob.escape` и пакетное удаление ключей Redis.

4. **[Critical — CRLF в Linux-скриптах & Опасный SIGKILL] `scripts/deploy.sh`, `scripts/publish_image.sh`, `scripts/restart_services.sh`, `scripts/system_monitor.py`**
   - **Проблема:** Все три `.sh`-скрипта имели Windows CRLF (`\r\n`), ломающий запуск на Linux; `scripts/system_monitor.py:kill_hung_processes()` отправлял `SIGKILL` **любому** Python-процессу на хосте (включая самого бота, воркер и системные демоны) при uptime > 30 мин и разовом пике CPU > 80%; `check_redis_health()` тёк соединениями при ошибке `ping()`, а `aiohttp` вызывался с `timeout=10` (некорректно для `aiohttp >= 3.10`).
   - **Исправление:** Скрипты переведены в LF, добавлен `.gitattributes` (`*.sh text eol=lf`); `kill_hung_processes` переведён в безопасный режим предупреждений без автоматического `SIGKILL`; исправлены парсинг AMQP URL (`urlparse` + `unquote`), `ClientTimeout(total=10)` и `finally: await redis.aclose()`.

5. **[High — Утечка секрета в логи & Падения] `core/weather_api.py`**
   - **Проблема:** При `ClientResponseError` или неожиданных исключениях в лог попадал URL с открытым `appid=<OPENWEATHERMAP_API_KEY>`; при `start_hour >= 24` возникал `ValueError`; при пустом `"weather": []` — `IndexError`; для `lang="zh"` передавался некорректный код вместо `zh_cn`.
   - **Исправление:** Добавлена функция `_scrub_appid()` для маскирования ключа во всех логах, исправлен `start_hour_clamped`, добавлена безопасная обработка пустых/битых полей OWM и маппинг `zh -> zh_cn`.

6. **[High — Обход авторизации и подмена адресата] `bot/dialogs/admin_menu.py` (`on_cancel_generation`), `main.py`, `bot/handlers/feedback_reply_handler.py`, `bot/dialogs/schedule_view.py`, `core/subscription_filter.py`**
   - **Проблема:**
     - `on_cancel_generation` регистрировался глобально без проверки `ADMIN_IDS`.
     - `handle_feedback_reply` не проверял `ADMIN_IDS`, выполнял regex `ID:\s*(\d+)` ДО проверки скрытой пересылки (позволяя пользователю со скрытой пересылкой подменить `user_id` в тексте сообщения) и падал с `MultipleResultsFound` при >=2 неотвеченных тикетах.
     - `on_send_original_file` в `schedule_view.py` имел fail-open (`except Exception: pass`) при сбое проверки подписки.
     - `core/subscription_filter.py` считал любой статус `"restricted"` подписанным без проверки `is_member`, а в `remove_required_channel` падал с переполнением 32-bit `Integer` на длинных числовых ID.
   - **Исправление:** Добавлены проверки `ADMIN_IDS` (в `main.py`, `admin_menu.py`, `feedback_reply_handler.py`), переставлена проверка скрытой пересылки до regex и добавлен `.limit(1)`, `on_send_original_file` переведён в fail-closed, проверен флаг `is_member` для `restricted` и добавлена защита 32-bit `Integer`.

7. **[High — HTML-инъекции / 500-ошибки / CSP] `core/alert_webhook.py`, `bot/text_formatters.py`, `bot/scheduler.py`, `bot/handlers/group_chat_router.py`, `core/summary_advisor.py`, `templates/schedule_template.html`**
   - **Проблема:**
     - `alert_webhook.py`: `hmac.compare_digest(str, str)` бросал `TypeError` (HTTP 500) на non-ASCII токенах в `Authorization`; сообщения алертов с `<` / `>` / `&` (например, PromQL `metric < threshold`) отправлялись в бот с `DefaultBotProperties(parse_mode="HTML")` без экранирования и молча терялись; `AppRunner` не возвращался и не закрывался; `/readiness` не имел таймаутов; отсутствовал лимит тела запроса.
     - Неэкранированные поля в HTML: `time_str`/`week_name`/`day_name` (`text_formatters.py`), `old_group`/`available_groups` (`scheduler.py`), `suggestions` (`group_chat_router.py`), `description`/`emoji` погоды (`summary_advisor.py`).
     - `templates/schedule_template.html` не содержал `<meta http-equiv="Content-Security-Policy">`.
   - **Исправление:** Перевод `hmac.compare_digest` на UTF-8 байты, `html.escape` во всех перечисленных местах, валидация схемы JSON и `client_max_size=64 KiB`, таймаут 3 с на `/readiness`, возврат и `cleanup()` `AppRunner` в `main.py`, добавление CSP и исправление `rootPx` в `fitRow()`.

### 2.2. Гонки, утечки ресурсов, часовые пояса, кодировки и логические баги — ИСПРАВЛЕНО

- **`bot/tasks.py`**: Исправлен критический баг с отступом `run_async(_inner())` вне `with _generation_semaphore:` (семафор ранее не ограничивал параллелизм Playwright!); закрытие `udm.close()` (БД + Redis) в `finally:` для `_mark_user_as_blocked` и `generate_week_image_task`.
- **`bot/middlewares/network_retry_middleware.py`**: Отключены повторные попытки для неидемпотентных методов доставки (`sendMessage`, `sendPhoto`, `copyMessage` и др.) при `asyncio.TimeoutError`, что устраняет дублирование сообщений при таймауте чтения.
- **`bot/middlewares/activity_logging_middleware.py`**: Ограничена кардинальность Prometheus-лейбла `BOT_COMMANDS` (allowlist известных команд + `"other"`), что предотвращает OOM DoS.
- **`bot/middlewares/custom_emoji_middleware.py`**: Исправлен `_is_html_parse_mode(None, bot)` — явный `parse_mode=None` больше не приводит к подстановке `<tg-emoji>` в plain-text сообщения.
- **`bot/handlers/inline_handlers.py`**: Исправлен поиск ключевых слов дней на целые токены (без ложных срабатываний на подстроки), установлен `is_personal=True` для локализованных результатов (устранено кросс-языковое отравление кэша Telegram).
- **`bot/utils/worker_loop.py`**: Отмена зависших future при таймауте (`future.cancel()`), защита от deadlock при рекурсивном вызове из того же потока, синхронизированный `stop_worker_loop()`, ожидание готовности loop через `threading.Event`, удаление китайских строк из логов.
- **`core/ics_exporter.py`**: Исправлен `DTSTAMP` (теперь UTC `timezone.utc`, а не MSK+`Z`), исправлены ключи `teachers` / `groups` (ранее экспорт для студентов терял преподавателей, а для преподавателей — списки групп), очистка `\r` и управляющих ASCII-символов в `_escape_ics_text`, ограничение длины имени файла.
- **`core/parser.py`**: Атомарная запись `fallback_schedule.json` через `os.replace()`; обновление `_LAST_ETAG`/`_LAST_MODIFIED` только после успешного парсинга и проверки актуальности года; перенос `"utf-8-sig"` на первое место декодирования (исправлен баг с UTF-8 BOM).
- **`core/manager.py`**: Устойчивость `TimetableManager.create()` к повреждённому кэшу в Redis (автоматическая очистка и переход к fallback вместо падения); **оптимизация** поиска преподавателей с O(T) пересборки словаря на каждый запрос до кэшированного O(1) (`_get_normalized_teachers_map`).
- **`core/user_data.py`**: Добавлен метод `close()` (закрывает Redis + `engine.dispose()`, вызывается из `main.py` и `bot/tasks.py`); при `set_user_blocked()` теперь инвалидируется и кэш рассылок.
- **`core/user_schedule_rules.py`**: Исправлено регистронезависимое переключение `toggle_hide_subject` (совместимо и с PostgreSQL, и с SQLite для кириллицы, устраняет `MultipleResultsFound`).
- **`core/summary_advisor.py`**: Корректный расчёт `max_gap_minutes` и `last_end` при перекрывающихся парах подгрупп, ограничение `end_hour <= 23`.
- **`core/analytics.py`**: Добавлен `try ... finally: plt.close(fig)` (устранена утечка фигур `matplotlib`), указан `tz=MOSCOW_TZ` в `mdates.DateFormatter`, ограничен `days <= 60` в `get_top_features`.
- **`core/alert_sender.py`**: Таймаут `ClientTimeout(total=10)`, сброс `self.session = None` в `close()`, маппинг severity `"error"`, лимиты длины сообщений и логирование сбоев без раскрытия токенов.
- **`core/group_chat_manager.py`**: Сохранение всех настроек чата при миграции супергруппы (`migrate_chat`) в случае, если целевая запись уже существует.
- **`core/text_utils.py`**: **Оптимизация** — переход на `str.maketrans` / `.translate()` и предкомпилированные `re.compile` в `normalize_group_name` и `transliterate_failed_search`.
- **`bot/dialogs/admin_menu.py`**: Замена `date.today()` на `datetime.now(MOSCOW_TZ).date()` в недельном тесте напоминаний; экранирование имени группы.
- **`bot/scheduler.py`**: Защита `handle_graduated_groups` от пустого списка групп (предотвращает массовый сброс профилей при битом снапшоте).
- **`scripts/check_mypy_ratchet.py`**: Проверка `result.returncode not in (0, 1)` и привязка пути к `mypy_baseline` относительно корня репозитория.
- **`scripts/generate_themes_showcase.py`**: Удалён жёстко зашитый локальный путь разработчика, добавлен `tempfile.TemporaryDirectory()` и `try ... finally: await browser.close()`.

---

## 3. Архитектурные рекомендации (бэклог дальнейших улучшений)

Ниже перечислены найденные в ходе аудита архитектурные задачи, требующие отдельного планирования/миграций БД или изменения публичных контрактов тестов:

1. **БД и часовые пояса (`core/db/models.py`, `core/time_utils.py`):**
   - Постепенная миграция колонок `TIMESTAMP` → `TIMESTAMP(timezone=True)` (UTC), устранение дублирующих индексов (`idx_required_channels_channel_id`, `idx_failed_msg_status`), добавление составных/частичных индексов под запросы рассылок (`morning_time`, `evening_time`, `schedule_diff_notify`), исправление аннотаций `Mapped[str | None]` / `Mapped[date]`.
   - Замена `pytz` на стандартный `zoneinfo.ZoneInfo("Europe/Moscow")`.
2. **Устранение узкого места БД на каждый апдейт (`bot/middlewares/user_data_middleware.py`, `core/user_data.py`):**
   - Троттлинг обновлений `last_active_date` через Redis `SET NX EX 300` и переход `register_user` на PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`.
   - Консолидация 9 параллельных запросов в `gather_stats()` в 1 агрегированный SQL-запрос (`COUNT(*) FILTER (...)`).
3. **Атомарный Rate Limiter (`bot/middlewares/rate_limit_middleware.py`):**
   - Перенос `LRANGE`+`RPUSH`+`EXPIRE`+`LTRIM` в единый Lua-скрипт на Redis `ZSET` и замена дробного лимита `inline_query: 0.5` на пару `(max_requests, window_seconds)`.
4. **Изоляция Chromium (`core/image_generator.py`):**
   - Замена `--disable-web-security` + `file://` на перехватчик `page.route()` с виртуальным origin для локальных шрифтов/фонов, а также refcount-дренирование активных страниц перед плановой ротацией браузера.
5. **Гейткипер подписок и аналитика (`bot/middlewares/subscription_filter_middleware.py`, `bot/middlewares/event_utils.py`, `core/metrics.py`):**
   - Закрытие обхода обязательной подписки через `inline_query` (с возвратом подсказки подписки через `switch_pm` / `InlineQueryResultArticle`).
   - Исправление `event_utils.extract_text()` / `extract_callback_data()` (сейчас `extract_text` возвращает текст сообщения бота при `CallbackQuery`, из-за чего в `ActivityLoggingMiddleware` ветка `button_click` перекрывается `text_message`) и удаление `user_group` из Prometheus-лейблов `USER_ACTIVITY_DAILY`.
6. **Семестровый календарь и diff-движок (`core/semester_calendar.py`, `core/schedule_diff.py`):**
   - Явная проверка `target < semester_start` (чтобы дни до старта семестра на той же неделе не считались активной Неделей 1) и добавление окончания осеннего семестра.
   - Реализация сопоставления `TIME_CHANGED` и `SUBJECT_CHANGED` в `ScheduleDiffDetector`.
7. **Деплой-скрипты (`scripts/deploy.sh`, `scripts/publish_image.sh`, `scripts/graceful_shutdown.py`):**
   - Безопасный парсинг `.env` без `source` (`. "./$ENV_FILE"`), `docker logout` в `trap EXIT`, фильтрация контейнеров по `com.docker.compose.project` вместо подстрок `"bot"`/`"worker"`.
