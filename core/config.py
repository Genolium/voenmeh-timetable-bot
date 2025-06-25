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
DATABASE_FILENAME = Path('/app/data/users.db')
# Интервал проверки изменений в расписании на сайте (в минутах)
CHECK_INTERVAL_MINUTES = 30
# Имена ключей в Redis
REDIS_SCHEDULE_CACHE_KEY = "timetable:schedule_cache"
REDIS_SCHEDULE_HASH_KEY = "timetable:schedule_hash"

# --- Пути к медиафайлам ---
MEDIA_PATH = BASE_DIR / "bot" / "media"
WELCOME_IMAGE_PATH = MEDIA_PATH / "welcome.png"
NO_LESSONS_IMAGE_PATH = MEDIA_PATH / "no_lessons.png"
SEARCH_IMAGE_PATH = MEDIA_PATH / "search_main.png"
TEACHER_IMAGE_PATH = MEDIA_PATH / "search_teacher.png"
CLASSROOM_IMAGE_PATH = MEDIA_PATH / "search_classroom.png"


OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
# ID города Санкт-Петербург (можно найти на https://openweathermap.org/find?q=Saint+Petersburg)
OPENWEATHERMAP_CITY_ID = "498817" # Saint Petersburg, Russia
# Единицы измерения: metric (Цельсий), imperial (Фаренгейт), standard (Кельвин)
OPENWEATHERMAP_UNITS = "metric"

ADMIN_ID = os.getenv("ADMIN_ID")
FEEDBACK_CHAT_ID = os.getenv("FEEDBACK_CHAT_ID")