from datetime import timedelta
import pytz, os
from pathlib import Path

# Определяем базовую директорию проекта
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Константы приложения ---
API_URL = 'https://voenmeh.ru/wp-content/themes/Avada-Child-Theme-Voenmeh/_voenmeh_grafics/TimetableGroup50.xml'
MAP_URL = "https://voenmeh.ru/openmap/"
CACHE_LIFETIME = timedelta(hours=12) # Время жизни кэша в Redis
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
DAY_MAP = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', None]
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# --- Настройки базы данных и Redis ---
# Интервал проверки изменений в расписании на сайте (в минутах)
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", 30))
# Имена ключей в Redis
REDIS_SCHEDULE_CACHE_KEY = "timetable:schedule_cache"
REDIS_SCHEDULE_HASH_KEY = "timetable:schedule_hash"

# --- Пути к медиа- и скриншот-файлам ---
MEDIA_PATH = BASE_DIR / "bot" / "media"
SCREENSHOTS_PATH = BASE_DIR / "bot" / "screenshots"

# Основные медиа
WELCOME_IMAGE_PATH = MEDIA_PATH / "welcome.png"
NO_LESSONS_IMAGE_PATH = MEDIA_PATH / "no_lessons.png"

# Медиа для диалога поиска
SEARCH_IMAGE_PATH = MEDIA_PATH / "search_main.png"
TEACHER_IMAGE_PATH = MEDIA_PATH / "search_teacher.png"
CLASSROOM_IMAGE_PATH = MEDIA_PATH / "search_classroom.png"

# Скриншоты для туториала (About)
ABOUT_WELCOME_IMG = SCREENSHOTS_PATH / "about_welcome.png"
ABOUT_MAIN_SCREEN_IMG = SCREENSHOTS_PATH / "about_main_screen.png"
ABOUT_SEARCH_IMG = SCREENSHOTS_PATH / "about_search.png"
ABOUT_NOTIFICATIONS_IMG = SCREENSHOTS_PATH / "about_notifications.png"
ABOUT_INLINE_IMG = SCREENSHOTS_PATH / "about_inline.png"

# --- Настройки API ---
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
OPENWEATHERMAP_CITY_ID = "498817"
OPENWEATHERMAP_UNITS = "metric"

# --- Настройки администратора и обратной связи ---
admin_ids_str = os.getenv("ADMIN_ID")
ADMIN_IDS = []
if admin_ids_str:
    try:
        ADMIN_IDS = [int(admin_id.strip()) for admin_id in admin_ids_str.split(',') if admin_id.strip()]
    except ValueError:
        print("ОШИБКА: Неверный формат ADMIN_IDS. ID должны быть числами, разделенными запятой.")
        
FEEDBACK_CHAT_ID = os.getenv("FEEDBACK_CHAT_ID")

# --- Премиум/подписка для полного качества изображения ---
# Может быть ID канала (например, -1001234567890) или @username
SUBSCRIPTION_CHANNEL = os.getenv("SUBSCRIPTION_CHANNEL")
