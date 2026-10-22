import gzip
import json
import pickle
import time
from datetime import date, datetime, timedelta

from rapidfuzz import fuzz, process
from redis.asyncio.client import Redis

from core.config import CACHE_LIFETIME, DAY_MAP, MOSCOW_TZ, REDIS_SCHEDULE_CACHE_KEY
from core.semester_calendar import get_semester_info


class TimetableManager:
    """
    Управляет полным расписанием, включая индексы для быстрого поиска.
    Работает ИСКЛЮЧИТЕЛЬНО с Redis для кэширования.
    """

    def __init__(self, all_schedules_data: dict, redis_client: Redis):
        if not all_schedules_data:
            raise ValueError("Данные расписания не могут быть пустыми.")

        self.redis = redis_client
        self.metadata = all_schedules_data.get("__metadata__", {})
        self._schedules = {k: v for k, v in all_schedules_data.items() if not k.startswith("__")}
        self._teachers_index = all_schedules_data.get("__teachers_index__", {})
        self._classrooms_index = all_schedules_data.get("__classrooms_index__", {})
        self._current_xml_hash = all_schedules_data.get("__current_xml_hash__", "")
        self.semester_start_date = None
        self._use_compression = True  # Включаем сжатие для оптимизации
        self._semester_settings_cache: tuple[float, tuple | None] | None = None

        if "period" in self.metadata:
            try:
                period = self.metadata["period"]
                self.semester_start_date = date(
                    int(period["StartYear"]),
                    int(period["StartMonth"]),
                    int(period["StartDay"]),
                )
            except (KeyError, ValueError):
                print("Предупреждение: не удалось разобрать дату начала семестра.")

    @classmethod
    async def create(cls, redis_client: Redis):
        """
        Асинхронный конструктор. Единственный правильный способ создать экземпляр.
        Загружает данные из кэша Redis или с сервера.
        """
        print("Инициализация TimetableManager...")

        async with redis_client.lock("timetable_init_lock", timeout=300, blocking_timeout=120):
            cached_data = await redis_client.get(REDIS_SCHEDULE_CACHE_KEY)

            if cached_data:
                print("Найден кэш расписания в Redis.")
                # Создаем временный экземпляр для разжатия данных
                temp_instance = cls.__new__(cls)
                temp_instance._use_compression = True
                data = temp_instance._decompress_data(cached_data)

                # Обновляем fallback файл актуальными данными из кэша
                try:
                    from core.parser import save_fallback_schedule

                    save_fallback_schedule(data)
                    print("Fallback schedule updated with cached data")
                except Exception as e:
                    print(f"Warning: Failed to update fallback schedule: {e}")

                return cls(data, redis_client)
            else:
                print("Кэш в Redis не найден. Загрузка с сервера...")
                from core.parser import fetch_and_parse_all_schedules

                new_data = await fetch_and_parse_all_schedules()
                if new_data:
                    temp_instance = cls(new_data, redis_client)
                    await temp_instance.save_to_cache()
                    print("Новое расписание загружено и сохранено в кэш Redis.")
                    return temp_instance
                else:
                    # Пытаемся восстановить из последней резервной копии
                    print("Сервер недоступен. Пытаемся восстановить из резервной копии...")
                    backup_data = await cls._restore_from_backup(redis_client)
                    if backup_data:
                        temp_instance = cls(backup_data, redis_client)
                        await temp_instance.save_to_cache()  # Восстанавливаем основной кэш

                        # Обновляем fallback файл данными из резервной копии
                        try:
                            from core.parser import save_fallback_schedule

                            save_fallback_schedule(backup_data)
                            print("Fallback schedule updated with backup data")
                        except Exception as e:
                            print(f"Warning: Failed to update fallback schedule from backup: {e}")

                        print("Расписание восстановлено из резервной копии!")
                        return temp_instance
                    else:
                        print(
                            "Критическая ошибка: нет доступных резервных копий расписания. Попытка загрузить fallback данные..."
                        )
                        # Пытаемся загрузить fallback данные
                        from core.parser import load_fallback_schedule

                        fallback_data = load_fallback_schedule()
                        if fallback_data:
                            print("Используем fallback данные расписания.")
                            temp_instance = cls(fallback_data, redis_client)
                            await temp_instance.save_to_cache()

                            # Обновляем fallback файл (на случай если данные были изменены)
                            try:
                                from core.parser import save_fallback_schedule

                                save_fallback_schedule(fallback_data)
                                print("Fallback schedule file verified and updated")
                            except Exception as e:
                                print(f"Warning: Failed to update fallback schedule file: {e}")

                            print("Fallback расписание загружено и сохранено в кэш Redis.")
                            return temp_instance
                        else:
                            print("Критическая ошибка: fallback данные недоступны.")
                            return None

    @classmethod
    async def _restore_from_backup(cls, redis_client: Redis):
        """
        Восстанавливает расписание из последней резервной копии в Redis.

        Returns:
            dict: Данные расписания или None если резервных копий нет
        """
        try:
            # Ищем все резервные копии
            backup_pattern = "timetable:backup:*"
            backup_keys = await redis_client.keys(backup_pattern)

            if not backup_keys:
                print("Резервные копии в Redis не найдены")
                return None

            # Сортируем по дате (последние сначала) и берем самую свежую
            backup_keys.sort(reverse=True)
            latest_backup_key = backup_keys[0]

            print(f"Восстанавливаем из резервной копии: {latest_backup_key}")
            backup_data_raw = await redis_client.get(latest_backup_key)

            if backup_data_raw:
                # Создаем временный экземпляр для разжатия данных
                temp_instance = cls.__new__(cls)
                temp_instance._use_compression = True
                backup_data = temp_instance._decompress_data(backup_data_raw)

                print(
                    f"Резервная копия успешно загружена. Групп: {len([k for k in backup_data.keys() if not k.startswith('__')])}"
                )
                return backup_data
            else:
                print("Резервная копия пуста")
                return None

        except Exception as e:
            print(f"Ошибка при восстановлении из резервной копии: {e}")
            return None

    async def save_to_cache(self):
        """Сохраняет текущее состояние менеджера в кэш Redis."""
        print(f"Сохранение расписания в кэш Redis по ключу: {REDIS_SCHEDULE_CACHE_KEY}")
        data_to_save = {
            "__metadata__": self.metadata,
            "__teachers_index__": self._teachers_index,
            "__classrooms_index__": self._classrooms_index,
            "__current_xml_hash__": self._current_xml_hash,
            **self._schedules,
        }
        # Используем сжатие для экономии места в Redis
        compressed_data = self._compress_data(data_to_save)
        await self.redis.set(REDIS_SCHEDULE_CACHE_KEY, compressed_data, ex=CACHE_LIFETIME)
        await self.cache_indexes()

    async def cache_indexes(self):
        """Сохраняет сжатые индексы преподавателей и аудиторий в отдельные быстрые ключи Redis."""
        try:
            compressed_teachers = self._compress_data(self._teachers_index)
            compressed_classrooms = self._compress_data(self._classrooms_index)
            await self.redis.set("timetable:index:teachers", compressed_teachers, ex=CACHE_LIFETIME)
            await self.redis.set("timetable:index:classrooms", compressed_classrooms, ex=CACHE_LIFETIME)
        except Exception as e:
            print(f"Ошибка кэширования индексов: {e}")

    async def load_indexes_from_cache(self) -> bool:
        """Загружает быстрые индексы напрямую из Redis."""
        try:
            raw_teachers = await self.redis.get("timetable:index:teachers")
            raw_classrooms = await self.redis.get("timetable:index:classrooms")
            if raw_teachers and raw_classrooms:
                self._teachers_index = self._decompress_data(raw_teachers)
                self._classrooms_index = self._decompress_data(raw_classrooms)
                return True
        except Exception as e:
            print(f"Ошибка загрузки индексов из кэша: {e}")
        return False

    async def get_academic_week_type(self, target_date: date) -> tuple[str, str]:
        """
        Определяет тип недели на основе академического календаря.

        Единственный источник истины — core.semester_calendar. Настройки из БД
        хранят месяц/день и автоматически переносятся на нужный учебный год.
        """
        info = await self.get_semester_info_for(target_date)
        week_name = "Нечетная" if info.week_key == "odd" else "Четная"
        return (info.week_key, week_name)

    async def get_semester_info_for(self, target_date: date):
        """Возвращает SemesterInfo для даты (для текста и картинок)."""
        settings = None
        if hasattr(self, "_semester_settings_manager"):
            now = time.monotonic()
            cached = getattr(self, "_semester_settings_cache", None)
            if cached and now - cached[0] < 60:
                settings = cached[1]
            else:
                try:
                    settings = await self._semester_settings_manager.get_semester_settings()
                    self._semester_settings_cache = (now, settings)
                except Exception:
                    settings = None
        fall_semester_start, spring_semester_start = settings if settings else (None, None)
        return get_semester_info(target_date, fall_semester_start, spring_semester_start)

    async def get_schedule_for_day(self, group_number: str, target_date: date = None) -> dict | None:
        """Возвращает расписание для группы на конкретный день."""
        target_date = target_date or datetime.now(MOSCOW_TZ).date()
        group_schedule = self._schedules.get(group_number.upper())
        if not group_schedule:
            # Проверяем, есть ли похожие группы
            all_groups = [g for g in self._schedules.keys() if not g.startswith("__")]
            similar_groups = []

            # Ищем группы с похожими названиями
            for group in all_groups:
                if group_number.upper() in group or group in group_number.upper():
                    similar_groups.append(group)

            error_message = f"Группа '{group_number}' не найдена."
            if similar_groups:
                error_message += f" Возможно, вы имели в виду: {', '.join(similar_groups[:3])}"
            else:
                error_message += " Возможно, группа выпустилась или была переименована."

            return {"error": error_message}

        # Используем новую академическую логику определения недели
        info = await self.get_semester_info_for(target_date)
        week_key = info.week_key
        week_name = "Нечетная" if week_key == "odd" else "Четная"
        day_name = DAY_MAP[target_date.weekday()]

        lessons = group_schedule.get(week_key, {}).get(day_name, []) if day_name and info.is_active else []

        try:
            from core.schedule_overrides import get_group_override

            override = await get_group_override(self.redis, group_number.upper(), target_date)
            if override is not None:
                lessons = override
        except Exception:
            pass

        return {
            "group": group_number.upper(),
            "date": target_date,
            "day_name": day_name or "Воскресенье",
            "week_name": week_name,
            "week_key": week_key,
            "week_number": info.week_number,
            "lessons": sorted(
                lessons,
                key=lambda x: x.get("start_time_raw", ""),
            ),
        }

    def find_groups(self, query: str) -> list[str]:
        """Находит группы по подстроке, исключая служебные ключи."""
        normalized = (query or "").strip().upper()
        if not normalized:
            return []
        return sorted(group for group in self._schedules if not group.startswith("__") and normalized in group.upper())

    def find_groups_fuzzy(self, query: str, limit: int = 20, score_cutoff: int = 55) -> list[str]:
        """Нечёткий поиск групп для опечаток и неполного ввода."""
        normalized = (query or "").strip().upper()
        if len(normalized) < 2:
            return []
        candidates = [group for group in self._schedules if not group.startswith("__")]
        results = process.extract(normalized, candidates, scorer=fuzz.WRatio, score_cutoff=score_cutoff, limit=limit)
        return [group for group, _score, _idx in results]

    def find_teachers(self, query: str) -> list[str]:
        """Находит фамилии преподавателей, содержащие поисковый запрос (регистронезависимо)."""
        if len(query) < 3:
            return []
        query_lower = query.lower()
        return sorted([name for name in self._teachers_index if query_lower in name.lower()])

    def find_teachers_fuzzy(self, query: str, limit: int = 5, score_cutoff: int = 70) -> list[str]:
        """Нечёткий поиск преподавателей по близости к запросу (RapidFuzz)."""
        if len(query.strip()) < 2:
            return []
        candidates = list(self._teachers_index.keys())
        results = process.extract(
            query,
            candidates,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff,
            limit=limit,
        )
        return [name for name, score, _ in results]

    def resolve_canonical_teacher(self, raw_name: str) -> str | None:
        """Возвращает каноническое имя преподавателя по «сырым» данным пользователя.

        Последовательность:
        1) точное совпадение
        2) нормализация (без регистра/точек/пробелов)
        3) fuzzy поиск (мягкий порог)
        """
        if not raw_name:
            return None
        # 1) exact
        if raw_name in self._teachers_index:
            return raw_name

        # 2) normalization
        def _normalize(n: str) -> str:
            try:
                return "".join(ch for ch in n.replace(" ", "").replace("\u00a0", "").replace(".", "")).upper()
            except Exception:
                return (n or "").upper()

        normalized_to_canonical = {_normalize(k): k for k in self._teachers_index.keys()}
        norm_key = _normalize(raw_name)
        if norm_key in normalized_to_canonical:
            return normalized_to_canonical[norm_key]

        # 3) fuzzy fallback
        fit = self.find_teachers_fuzzy(raw_name, limit=1, score_cutoff=55)
        return fit[0] if fit else None

    async def get_teacher_schedule(self, teacher_name: str, target_date: date) -> dict | None:
        """Возвращает расписание преподавателя на конкретный день.

        ВАЖНО: Здесь используем ТОЛЬКО точное совпадение. Нечёткий поиск выполняется
        на этапе регистрации и мы сохраняем каноническое имя преподавателя в БД.
        Это исключает риск того, что в заголовке будет один преподаватель,
        а расписание отобразится другого.
        """
        # 1) Точное совпадение (быстро)
        if teacher_name in self._teachers_index:
            exact_match = teacher_name
        else:
            # 2) Нормализация: убираем точки/пробелы, сравниваем без регистра
            def _normalize(n: str) -> str:
                try:
                    return "".join(ch for ch in n.replace(" ", "").replace("\u00a0", "").replace(".", "")).upper()
                except Exception:
                    return (n or "").upper()

            normalized_to_canonical: dict[str, str] = {_normalize(k): k for k in self._teachers_index.keys()}
            norm_key = _normalize(teacher_name)
            if norm_key in normalized_to_canonical:
                exact_match = normalized_to_canonical[norm_key]
            else:
                # 3) Fallback: ne чёткий поиск по исходным ключам (регистрация могла сохранить вариант с опечаткой)
                fuzzy_results = self.find_teachers_fuzzy(teacher_name, limit=1, score_cutoff=50)
                if fuzzy_results:
                    exact_match = fuzzy_results[0]
                else:
                    return {"error": f"Преподаватель '{teacher_name}' не найден в индексе."}

        # Используем новую академическую логику определения недели
        info = await self.get_semester_info_for(target_date)
        week_key_num = info.week_key
        week_name = "Нечетная" if week_key_num == "odd" else "Четная"
        day_name = DAY_MAP[target_date.weekday()]

        lessons_for_day = []
        if day_name and info.is_active:
            all_teacher_lessons = self._teachers_index.get(exact_match, [])
            for lesson in all_teacher_lessons:
                if lesson.get("day") == day_name:
                    lesson_week_code = lesson.get("week_code", "0")
                    is_every_week = lesson_week_code == "0"
                    is_odd_match = week_key_num == "odd" and lesson_week_code == "1"
                    is_even_match = week_key_num == "even" and lesson_week_code == "2"

                    if is_every_week or is_odd_match or is_even_match:
                        lessons_for_day.append(lesson)

        return {
            "teacher": exact_match,
            "date": target_date,
            "day_name": day_name or "Воскресенье",
            "week_name": week_name,
            "week_key": week_key_num,
            "week_number": info.week_number,
            "lessons": sorted(
                lessons_for_day,
                key=lambda x: x.get("start_time_raw", ""),
            ),
        }

    def find_classrooms(self, query: str) -> list[str]:
        """Находит аудитории, номер которых начинается с поискового запроса."""
        if not query:
            return []
        return sorted([number for number in self._classrooms_index if number.startswith(query)])

    def find_classrooms_fuzzy(self, query: str, limit: int = 5, score_cutoff: int = 75) -> list[str]:
        """Нечёткий поиск аудитории (по номеру/строке), полезно для опечаток."""
        if len(query.strip()) < 2:
            return []
        candidates = list(self._classrooms_index.keys())
        results = process.extract(
            query,
            candidates,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff,
            limit=limit,
        )
        return [room for room, score, _ in results]

    async def get_classroom_schedule(self, classroom_number: str, target_date: date) -> dict | None:
        """Возвращает расписание аудитории на конкретный день."""
        if classroom_number not in self._classrooms_index:
            return {"error": f"Аудитория '{classroom_number}' не найдена в индексе."}

        # Используем новую академическую логику определения недели
        info = await self.get_semester_info_for(target_date)
        week_key_num = info.week_key
        week_name = "Нечетная" if week_key_num == "odd" else "Четная"
        day_name = DAY_MAP[target_date.weekday()]

        lessons_for_day = []
        if day_name and info.is_active:
            all_classroom_lessons = self._classrooms_index.get(classroom_number, [])
            for lesson in all_classroom_lessons:
                if lesson.get("day") == day_name:
                    lesson_week_code = lesson.get("week_code", "0")
                    is_every_week = lesson_week_code == "0"
                    is_odd_match = week_key_num == "odd" and lesson_week_code == "1"
                    is_even_match = week_key_num == "even" and lesson_week_code == "2"

                    if is_every_week or is_odd_match or is_even_match:
                        lessons_for_day.append(lesson)

        return {
            "classroom": classroom_number,
            "date": target_date,
            "day_name": day_name or "Воскресенье",
            "week_name": week_name,
            "week_key": week_key_num,
            "week_number": info.week_number,
            "lessons": sorted(
                lessons_for_day,
                key=lambda x: x.get("start_time_raw", ""),
            ),
        }

    def get_current_xml_hash(self) -> str:
        """Возвращает хеш текущей версии XML расписания."""
        return self._current_xml_hash

    def set_semester_settings_manager(self, settings_manager):
        """Устанавливает менеджер настроек семестров."""
        self._semester_settings_manager = settings_manager

    async def get_semester_settings_manager(self):
        """Получает менеджер настроек семестров."""
        return self._semester_settings_manager

    def _compress_data(self, data: dict) -> bytes:
        """Сжимает данные для сохранения в Redis."""
        if not self._use_compression:
            return json.dumps(data, ensure_ascii=False).encode("utf-8")

        # JSON безопаснее pickle и не выполняет код при чтении из Redis.
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return gzip.compress(serialized, compresslevel=6)

    def _decompress_data(self, compressed_data: bytes) -> dict:
        """Разжимает данные из Redis."""
        if not self._use_compression:
            return json.loads(compressed_data.decode("utf-8"))

        try:
            decompressed = gzip.decompress(compressed_data)
        except gzip.BadGzipFile:
            # Обратная совместимость с несжатым JSON.
            return json.loads(compressed_data.decode("utf-8"))

        try:
            return json.loads(decompressed.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Читаем старые кэши, записанные pickle, но новые записи — только JSON.
            return pickle.loads(decompressed)
