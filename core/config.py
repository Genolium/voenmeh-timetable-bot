from datetime import timedelta
import pytz, os
from pathlib import Path

# Определяем базовую директорию проекта
BASE_DIR = Path(__file__).resolve().parent.parent

# --- КОНСТАНТЫ ПРИЛОЖЕНИЯ ---

# URL для загрузки полного расписания с сайта Военмеха
API_URL = 'https://voenmeh.ru/wp-content/themes/Avada-Child-Theme-Voenmeh/_voenmeh_grafics/TimetableGroup50.xml'

# URL официальной карты корпусов Военмеха
MAP_URL = "https://voenmeh.ru/openmap/"

# --- Настройки кэширования расписания ---
# Имя файла для кэширования данных расписания (будет храниться в /app/data/ в Docker)
CACHE_FILENAME = 'data/full_schedule_cache.json'
# Срок жизни кэша (12 часов)
CACHE_LIFETIME = timedelta(hours=12)

# --- Настройки базы данных пользователей ---
# Имя файла базы данных SQLite для хранения данных пользователей (будет храниться в /app/data/ в Docker)
DATABASE_FILENAME = 'data/users.db'

# --- Настройки мониторинга изменений расписания ---
# Интервал проверки изменений в расписании на сайте (в минутах)
CHECK_INTERVAL_MINUTES = 30
# Имя файла для хранения хеша последней версии XML расписания (будет храниться в /app/data/ в Docker)
SCHEDULE_HASH_FILE = 'data/schedule_hash.txt' 

# --- Общие настройки ---
# User-Agent для HTTP-запросов (имитация браузера)
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

# Маппинг номеров дней недели (datetime.weekday() -> 0=Пн, 6=Вс) на названия на русском
DAY_MAP = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', None]

# Московский часовой пояс для корректной работы планировщика
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# --- Пути к медиафайлам (относительно BASE_DIR) ---
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