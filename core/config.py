from datetime import timedelta
import pytz

# URL для загрузки полного расписания
API_URL = 'https://voenmeh.ru/wp-content/themes/Avada-Child-Theme-Voenmeh/_voenmeh_grafics/TimetableGroup50.xml'

# Имя файла для кэширования данных расписания
CACHE_FILENAME = 'full_schedule_cache.json'

# Имя файла базы данных SQLite для хранения данных пользователей
DATABASE_FILENAME = 'users.db'

# Срок жизни кэша (12 часов)
CACHE_LIFETIME = timedelta(hours=12)

# User-Agent для HTTP-запросов
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

# Маппинг номеров дней недели (datetime.weekday()) на названия
DAY_MAP = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', None]

# Московский часовой пояс
MOSCOW_TZ = pytz.timezone('Europe/Moscow')