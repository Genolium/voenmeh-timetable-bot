from datetime import timedelta
import pytz
from pathlib import Path

API_URL = 'https://voenmeh.ru/wp-content/themes/Avada-Child-Theme-Voenmeh/_voenmeh_grafics/TimetableGroup50.xml'
MAP_URL = "https://voenmeh.ru/openmap/"
CACHE_LIFETIME = timedelta(hours=12)
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
DAY_MAP = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', None]
MOSCOW_TZ = pytz.timezone('Europe/Moscow')


CACHE_FILENAME = 'full_schedule_cache.json'
DATABASE_FILENAME = 'users.db'

BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_PATH = BASE_DIR / "bot" / "media"
WELCOME_IMAGE_PATH = MEDIA_PATH / "welcome.png"
NO_LESSONS_IMAGE_PATH = MEDIA_PATH / "no_lessons.png" 
SEARCH_IMAGE_PATH = MEDIA_PATH / "search_main.png"
TEACHER_IMAGE_PATH = MEDIA_PATH / "search_teacher.png"
CLASSROOM_IMAGE_PATH = MEDIA_PATH / "search_classroom.png"