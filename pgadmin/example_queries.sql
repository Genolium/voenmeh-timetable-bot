-- Примеры SQL-запросов для работы с базой данных Voenmeh Bot
-- Выполняйте эти запросы в pgAdmin Query Tool

-- 1. Просмотр всех пользователей
SELECT *
FROM users
ORDER BY registration_date DESC;

-- 2. Статистика по группам (топ-10 самых популярных)
SELECT 
    group,
    COUNT(*) as user_count
FROM users 
WHERE group_name IS NOT NULL
GROUP BY group_name 
ORDER BY user_count DESC 
LIMIT 10;

-- 3. Пользователи, зарегистрированные за последние 7 дней
SELECT 
    user_id,
    username,
    group_name,
    registration_date
FROM users 
WHERE registration_date >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY registration_date DESC;

-- 4. Активные пользователи (активность за последние 30 дней)
SELECT 
    user_id,
    username,
    group_name,
    last_active_date
FROM users 
WHERE last_active_date >= CURRENT_DATE - INTERVAL '30 days'
ORDER BY last_active_date DESC;

-- 5. Пользователи с отключенными уведомлениями
SELECT 
    user_id,
    username,
    group_name,
    evening_notify,
    morning_summary,
    lesson_reminders
FROM users 
WHERE evening_notify = false 
   OR morning_summary = false 
   OR lesson_reminders = false;

-- 6. Настройки семестров
SELECT 
    id,
    fall_semester_start,
    spring_semester_start,
    updated_at,
    updated_by
FROM semester_settings;

-- 7. Общая статистика
SELECT 
    COUNT(*) as total_users,
    COUNT(CASE WHEN group_name IS NOT NULL THEN 1 END) as users_with_group,
    COUNT(CASE WHEN last_active_date >= CURRENT_DATE - INTERVAL '7 days' THEN 1 END) as active_last_7_days,
    COUNT(CASE WHEN last_active_date >= CURRENT_DATE - INTERVAL '30 days' THEN 1 END) as active_last_30_days,
    AVG(reminder_time_minutes) as avg_reminder_time
FROM users;

-- 8. Пользователи с кастомным временем напоминаний (не 60 минут)
SELECT 
    user_id,
    username,
    group_name,
    reminder_time_minutes
FROM users 
WHERE reminder_time_minutes != 60
ORDER BY reminder_time_minutes;

-- 9. Создание индекса для оптимизации поиска по группе (если не существует)
-- CREATE INDEX IF NOT EXISTS idx_users_group_name ON users(group_name);

-- 10. Очистка неактивных пользователей (осторожно!)
-- DELETE FROM users WHERE last_active_date < CURRENT_DATE - INTERVAL '365 days';

-- 11. Обновление времени напоминаний для всех пользователей
-- UPDATE users SET reminder_time_minutes = 60 WHERE reminder_time_minutes IS NULL;

-- 12. Экспорт пользователей в CSV (выполнить в Query Tool)
-- COPY (
--     SELECT user_id, username, group_name, registration_date, last_active_date
--     FROM users
--     ORDER BY registration_date DESC
-- ) TO '/tmp/users_export.csv' WITH CSV HEADER;
