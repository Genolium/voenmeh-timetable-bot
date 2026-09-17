import asyncio
import hashlib
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

import re
from core.config import API_URL, DAY_MAP, FALLBACK_API_URL, USER_AGENT, VOENMEH_SU_API_URL
from core.metrics import ERRORS_TOTAL, RETRIES_TOTAL

# Заготовки для условного кэширования
_LAST_ETAG: str | None = None
_LAST_MODIFIED: str | None = None
_LAST_VOENMEH_SU_UPDATED_AT: str | None = None

class _NotModifiedSentinel:
    def __repr__(self) -> str:
        return "<NOT_MODIFIED>"

NOT_MODIFIED = _NotModifiedSentinel()

# Путь к fallback файлу с расписанием
FALLBACK_SCHEDULE_PATH = Path(__file__).parent.parent / "data" / "fallback_schedule.json"


def load_fallback_schedule() -> dict | None:
    """
    Загружает fallback данные расписания из локального файла.
    Если файл не существует, пытается создать начальный fallback файл.

    Returns:
        Словарь с данными расписания или None если файл недоступен и не может быть создан
    """
    try:
        if not FALLBACK_SCHEDULE_PATH.exists():
            logging.warning(f"Fallback schedule file not found: {FALLBACK_SCHEDULE_PATH}")
            # Попытка создать начальный fallback файл
            if create_initial_fallback_schedule():
                logging.info(f"Created initial fallback schedule file: {FALLBACK_SCHEDULE_PATH}")
            else:
                logging.error("Failed to create initial fallback schedule file")
                return None

        with open(FALLBACK_SCHEDULE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        logging.info(f"Loaded fallback schedule from {FALLBACK_SCHEDULE_PATH}")
        return data

    except Exception as e:
        logging.error(f"Failed to load fallback schedule: {e}")
        return None


def save_fallback_schedule(data: dict) -> bool:
    """
    Сохраняет данные расписания в fallback файл для использования в оффлайн-режиме.

    Args:
        data: Данные расписания для сохранения

    Returns:
        True если сохранение успешно, False в случае ошибки
    """
    try:
        # Создаем директорию если её нет
        FALLBACK_SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Сохраняем данные в файл
        with open(FALLBACK_SCHEDULE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logging.info(f"Updated fallback schedule file: {FALLBACK_SCHEDULE_PATH}")
        return True

    except Exception as e:
        logging.error(f"Failed to save fallback schedule: {e}")
        return False


def create_initial_fallback_schedule() -> bool:
    """
    Создает начальный fallback файл с базовыми данными, если файл не существует.

    Returns:
        True если файл создан или уже существует, False в случае ошибки
    """
    if FALLBACK_SCHEDULE_PATH.exists():
        logging.info(f"Fallback schedule file already exists: {FALLBACK_SCHEDULE_PATH}")
        return True

    # Создаем базовые fallback данные
    initial_data = {
        "__metadata__": {
            "period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"},
            "weeks": {"CurrentWeekType": "odd", "WeekStart": "2024-09-01"},
        },
        "__current_xml_hash__": "initial_fallback_2024",
        "__teachers_index__": {},
        "__classrooms_index__": {},
        # Добавляем примеры групп для основных факультетов Военмеха
        "О735Б": {
            "odd": {
                "Понедельник": [
                    {
                        "time": "09:00-10:30",
                        "subject": "Математика (fallback)",
                        "start_time_raw": "09:00",
                        "end_time_raw": "10:30",
                        "room": "101",
                        "teachers": "Иванов И.И.",
                        "week_type": "odd",
                    }
                ]
            },
            "even": {
                "Вторник": [
                    {
                        "time": "10:40-12:10",
                        "subject": "Физика (fallback)",
                        "start_time_raw": "10:40",
                        "end_time_raw": "12:10",
                        "room": "102",
                        "teachers": "Петров П.П.",
                        "week_type": "even",
                    }
                ]
            },
        },
        "О735А": {
            "odd": {
                "Среда": [
                    {
                        "time": "09:00-10:30",
                        "subject": "Химия (fallback)",
                        "start_time_raw": "09:00",
                        "end_time_raw": "10:30",
                        "room": "201",
                        "teachers": "Сидорова А.А.",
                        "week_type": "odd",
                    }
                ]
            },
            "even": {
                "Четверг": [
                    {
                        "time": "10:40-12:10",
                        "subject": "Информатика (fallback)",
                        "start_time_raw": "10:40",
                        "end_time_raw": "12:10",
                        "room": "301",
                        "teachers": "Иванов И.И.",
                        "week_type": "even",
                    }
                ]
            },
        },
    }

    try:
        return save_fallback_schedule(initial_data)
    except Exception as e:
        logging.error(f"Failed to create initial fallback schedule: {e}")
        return False


async def fetch_and_parse_from_voenmeh_su(
    session: aiohttp.ClientSession | None = None, force: bool = False
) -> dict | None:
    """
    Загружает и парсит актуальное расписание с официального API https://voenmeh.su.

    API-эндпоинты:
      - GET /api/schedule/meta — возвращает список всех групп, лекторов, период и дату обновления.
      - GET /api/schedule/lessons?name={group}&kind=group — возвращает пары группы.

    Args:
        session: Опциональная сессия aiohttp.ClientSession.
        force: Если True, игнорирует проверку неизменности updated_at и принудительно загружает данные.

    Returns:
        Словарь с расписанием в формате бота (группы, __teachers_index__,
        __classrooms_index__, __metadata__, __current_xml_hash__),
        None если данные не изменились (conditional 304) или при ошибке.
    """
    global _LAST_VOENMEH_SU_UPDATED_AT
    logging.info("Попытка загрузки расписания с основного источника voenmeh.su (force=%s)...", force)
    close_session = False
    if session is None:
        session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        close_session = True

    try:
        meta_url = f"{FALLBACK_API_URL}/api/schedule/meta"
        async with session.get(meta_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 304:
                return NOT_MODIFIED
            if resp.status != 200:
                raise RuntimeError(f"Не удалось получить meta с voenmeh.su: HTTP {resp.status}")
            meta = await resp.json()

        meta_updated_at = meta.get("updated_at")
        if not force and _LAST_VOENMEH_SU_UPDATED_AT and meta_updated_at == _LAST_VOENMEH_SU_UPDATED_AT:
            logging.info(f"Расписание на voenmeh.su не изменилось (updated_at={meta_updated_at}).")
            return NOT_MODIFIED

        groups = meta.get("groups", [])
        if not groups:
            logging.error("voenmeh.su вернул пустой список групп")
            return None

        all_schedules = {}
        teachers_index = {}
        classrooms_index = {}

        sem = asyncio.Semaphore(20)

        async def _fetch_group(grp: str):
            async with sem:
                url = f"{FALLBACK_API_URL}/api/schedule/lessons?name={grp}&kind=group"
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                        if r.status == 200:
                            return grp, await r.json()
                        logging.warning(f"Ошибка загрузки группы {grp} с voenmeh.su: HTTP {r.status}")
                except Exception as exc:
                    logging.warning(f"Исключение при загрузке группы {grp} с voenmeh.su: {exc}")
                return grp, None

        results = await asyncio.gather(*[_fetch_group(g) for g in groups])

        day_names_map = {
            1: "Понедельник",
            2: "Вторник",
            3: "Среда",
            4: "Четверг",
            5: "Пятница",
            6: "Суббота",
        }

        for grp, data in results:
            if not data or not isinstance(data, dict):
                continue
            lessons_raw = data.get("lessons", [])
            group_schedule = {"odd": {}, "even": {}}

            for item in lessons_raw:
                day_num = item.get("day")
                day_title = day_names_map.get(day_num)
                if not day_title:
                    continue

                week = item.get("week")
                time_raw = item.get("time", "N/A")
                start_time_token = time_raw.split()[0] if time_raw else "N/A"
                try:
                    start_dt = datetime.strptime(start_time_token, "%H:%M")
                    start_time_str = start_dt.strftime("%H:%M")
                    end_time_str = (start_dt + timedelta(minutes=90)).strftime("%H:%M")
                except ValueError:
                    start_time_str = start_time_token
                    end_time_str = "N/A"

                kind = item.get("kind") or ""
                subject = item.get("subject") or ""
                lecturers = [l.strip() for l in item.get("teachers", []) if l and l.strip()]
                rooms = [r.strip() for r in item.get("rooms", []) if r and r.strip()]
                classroom = ", ".join(rooms) if rooms else None

                lesson_info = {
                    "time": f"{start_time_str}-{end_time_str}",
                    "subject": subject,
                    "type": kind,
                    "teachers": ", ".join(lecturers),
                    "room": classroom or "кабинет не указан",
                    "group": grp.upper(),
                    "start_time_raw": start_time_str,
                    "end_time_raw": end_time_str,
                }

                week_code = "1" if week == "odd" else ("2" if week == "even" else "0")

                if week == "odd":
                    group_schedule["odd"].setdefault(day_title, []).append(lesson_info)
                elif week == "even":
                    group_schedule["even"].setdefault(day_title, []).append(lesson_info)
                else:
                    group_schedule["odd"].setdefault(day_title, []).append(lesson_info)
                    group_schedule["even"].setdefault(day_title, []).append(lesson_info)

                lesson_for_index = lesson_info.copy()
                lesson_for_index["day"] = day_title
                lesson_for_index["week_code"] = week_code
                lesson_for_index["groups"] = [lesson_info["group"]]

                lesson_key_components = [
                    day_title,
                    week_code,
                    lesson_info["time"],
                    lesson_info["subject"],
                    lesson_info["type"],
                    lesson_info["room"],
                    "|".join(sorted(lecturers)),
                ]
                lesson_key = "-".join(lesson_key_components)

                for lecturer in lecturers:
                    if lecturer not in teachers_index:
                        teachers_index[lecturer] = {}
                    if lesson_key in teachers_index[lecturer]:
                        if lesson_info["group"] not in teachers_index[lecturer][lesson_key]["groups"]:
                            teachers_index[lecturer][lesson_key]["groups"].append(lesson_info["group"])
                    else:
                        teachers_index[lecturer][lesson_key] = lesson_for_index.copy()

                if classroom and classroom != "кабинет не указан":
                    if classroom not in classrooms_index:
                        classrooms_index[classroom] = {}
                    if lesson_key in classrooms_index[classroom]:
                        if lesson_info["group"] not in classrooms_index[classroom][lesson_key]["groups"]:
                            classrooms_index[classroom][lesson_key]["groups"].append(lesson_info["group"])
                    else:
                        classrooms_index[classroom][lesson_key] = lesson_for_index.copy()

            all_schedules[grp.upper()] = group_schedule

        # Разбираем период учебного года для корректной работы TimetableManager
        period_str = meta.get("period", "")
        now = datetime.now()
        start_year = now.year if now.month >= 8 else now.year - 1
        start_month = 9
        start_day = 1

        if period_str:
            years_match = re.search(r"(\d{4})", period_str)
            if years_match:
                first_year = int(years_match.group(1))
                if "весен" in period_str.lower():
                    second_year_match = re.search(r"\d{4}\s*/\s*(\d{4})", period_str)
                    start_year = int(second_year_match.group(1)) if second_year_match else first_year
                    start_month = 2
                    start_day = 9
                else:
                    start_year = first_year
                    start_month = 9
                    start_day = 1

        all_schedules["__teachers_index__"] = {t: list(l.values()) for t, l in teachers_index.items()}
        all_schedules["__classrooms_index__"] = {c: list(l.values()) for c, l in classrooms_index.items()}
        all_schedules["__metadata__"] = {
            "period": {
                "Title": period_str,
                "StartYear": str(start_year),
                "StartMonth": str(start_month),
                "StartDay": str(start_day),
            },
            "weeks": {"FirstWeek": "odd"},
            "source": "voenmeh.su",
        }
        hash_seed = f"{meta.get('updated_at', '')}_{period_str}_{len(all_schedules)}"
        all_schedules["__current_xml_hash__"] = hashlib.md5(hash_seed.encode("utf-8")).hexdigest()

        _LAST_VOENMEH_SU_UPDATED_AT = meta_updated_at

        logging.info(
            f"Успешно спарсено {len([k for k in all_schedules if not k.startswith('__')])} групп с voenmeh.su"
        )
        return all_schedules

    except Exception as e:
        ERRORS_TOTAL.labels(source="parser_voenmeh_su").inc()
        logging.error(f"Ошибка парсинга с voenmeh.su: {e}", exc_info=True)
        return None
    finally:
        if close_session and session and hasattr(session, "close") and callable(session.close):
            try:
                await session.close()
            except Exception:
                pass


async def _fetch_and_parse_legacy_xml(session: aiohttp.ClientSession | None = None) -> dict | None:
    """
    Загружает и парсит XML расписание с сервера университета (API_URL).
    
    Проверяет актуальность года расписания. Если обнаружен прошлый учебный год
    (например, TimetableGroup50.xml от 2024/2025 года при текущем 2026/2027),
    данные отклоняются во избежание сброса расписания на старое.
    """
    global _LAST_ETAG, _LAST_MODIFIED
    close_session = False
    if session is None:
        headers = {"User-Agent": USER_AGENT}
        if _LAST_ETAG:
            headers["If-None-Match"] = _LAST_ETAG
        if _LAST_MODIFIED:
            headers["If-Modified-Since"] = _LAST_MODIFIED
        session = aiohttp.ClientSession(headers=headers)
        close_session = True

    try:
        attempts = 0
        xml_bytes = None
        while attempts < 3:
            attempts += 1
            try:
                async with session.get(
                    API_URL, timeout=aiohttp.ClientTimeout(total=45, connect=10, sock_read=30)
                ) as response:
                    response.raise_for_status()
                    if response.status == 304:
                        return NOT_MODIFIED
                    xml_bytes = await response.read()
                    _LAST_ETAG = response.headers.get("ETag") or _LAST_ETAG
                    _LAST_MODIFIED = response.headers.get("Last-Modified") or _LAST_MODIFIED
                    break
            except Exception:
                ERRORS_TOTAL.labels(source="parser").inc()
                if attempts < 3:
                    RETRIES_TOTAL.labels(component="parser").inc()
                    continue
                raise

        if not xml_bytes:
            return None

        # Ограничиваем общее время декодирования и парсинга XML
        xml_data = None
        for encoding in ("utf-8", "utf-16", "windows-1251", "utf-8-sig"):
            try:
                candidate = xml_bytes.decode(encoding).strip()
                if "<Timetable" in candidate or "<?xml" in candidate:
                    xml_data = candidate
                    break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if xml_data is None:
            xml_data = xml_bytes.decode("utf-8", errors="replace").strip()

        current_hash = hashlib.md5(xml_bytes).hexdigest()
        root = ET.fromstring(xml_data)

        all_schedules = {}
        teachers_index = {}
        classrooms_index = {}

        period_meta = root.find("Period").attrib if root.find("Period") is not None else {}
        weeks_meta = root.find("Weeks").attrib if root.find("Weeks") is not None else {}

        # Проверка на устаревший год (защита от сброса на прошлый год)
        if API_URL == "https://voenmeh.ru/wp-content/themes/Avada-Child-Theme-Voenmeh/_voenmeh_grafics/TimetableGroup50.xml":
            now = datetime.now()
            current_academic_year = now.year if now.month >= 8 else now.year - 1
            xml_start_year = int(period_meta.get("StartYear", 0))
            if xml_start_year and xml_start_year < current_academic_year:
                logging.warning(
                    f"XML с {API_URL} содержит устаревшее расписание за {xml_start_year} год "
                    f"(текущий учебный год: {current_academic_year}). Файл отклонён во избежание сброса расписания."
                )
                return None

        all_schedules["__metadata__"] = {
            "period": period_meta,
            "weeks": weeks_meta,
            "source": "voenmeh.ru_xml",
        }

        for group_element in root.findall("Group"):
            group_number = group_element.get("Number")
            if not group_number:
                continue

            group_schedule = {"odd": {}, "even": {}}
            for day_element in group_element.findall("Days/Day"):
                day_title = day_element.get("Title")
                if not day_title:
                    continue

                lessons_odd, lessons_even = [], []
                for lesson_element in day_element.findall("GroupLessons/Lesson"):
                    time_tag = lesson_element.find("Time")
                    discipline_tag = lesson_element.find("Discipline")
                    classroom_tag = lesson_element.find("Classroom")
                    week_code_tag = lesson_element.find("WeekCode")

                    time_raw = time_tag.text.strip() if time_tag is not None and time_tag.text else "N/A"
                    discipline_raw = (
                        discipline_tag.text.strip() if discipline_tag is not None and discipline_tag.text else "N/A"
                    )
                    disc_parts = discipline_raw.split(" ", 1)

                    lecturers = [
                        l.text.strip()
                        for l in lesson_element.findall("Lecturers/Lecturer/ShortName")
                        if l.text and l.text.strip()
                    ]
                    classroom = (
                        classroom_tag.text.strip("; ")
                        if classroom_tag is not None and classroom_tag.text and classroom_tag.text.strip()
                        else None
                    )

                    start_time_token = time_raw.split()[0]
                    try:
                        start_dt_obj = datetime.strptime(start_time_token, "%H:%M")
                        start_time_str = start_dt_obj.strftime("%H:%M")
                        end_dt_obj = start_dt_obj + timedelta(minutes=90)
                        end_time_str = end_dt_obj.strftime("%H:%M")
                    except ValueError:
                        start_time_str = start_time_token
                        end_time_str = "N/A"

                    lesson_info = {
                        "time": f"{start_time_str}-{end_time_str}",
                        "subject": (disc_parts[1] if len(disc_parts) > 1 else discipline_raw),
                        "type": disc_parts[0],
                        "teachers": ", ".join(lecturers),
                        "room": classroom or "кабинет не указан",
                        "group": group_number.upper(),
                        "start_time_raw": start_time_str,
                        "end_time_raw": end_time_str,
                    }

                    week_code = week_code_tag.text if week_code_tag is not None else "0"
                    if week_code == "1":
                        lessons_odd.append(lesson_info)
                    elif week_code == "2":
                        lessons_even.append(lesson_info)
                    else:
                        lessons_odd.append(lesson_info)
                        lessons_even.append(lesson_info)

                    lesson_for_index = lesson_info.copy()
                    lesson_for_index["day"] = day_title
                    lesson_for_index["week_code"] = week_code
                    lesson_for_index["groups"] = [lesson_info["group"]]

                    lesson_key_components = [
                        day_title,
                        week_code,
                        lesson_info["time"],
                        lesson_info["subject"],
                        lesson_info["type"],
                        lesson_info["room"],
                        "|".join(sorted(lecturers)),
                    ]
                    lesson_key = "-".join(lesson_key_components)

                    for lecturer in lecturers:
                        if lecturer not in teachers_index:
                            teachers_index[lecturer] = {}
                        if lesson_key in teachers_index[lecturer]:
                            if lesson_info["group"] not in teachers_index[lecturer][lesson_key]["groups"]:
                                teachers_index[lecturer][lesson_key]["groups"].append(lesson_info["group"])
                        else:
                            teachers_index[lecturer][lesson_key] = lesson_for_index.copy()

                    if classroom and classroom != "кабинет не указан":
                        if classroom not in classrooms_index:
                            classrooms_index[classroom] = {}
                        if lesson_key in classrooms_index[classroom]:
                            if lesson_info["group"] not in classrooms_index[classroom][lesson_key]["groups"]:
                                classrooms_index[classroom][lesson_key]["groups"].append(lesson_info["group"])
                        else:
                            classrooms_index[classroom][lesson_key] = lesson_for_index.copy()

                if lessons_odd:
                    group_schedule["odd"][day_title] = lessons_odd
                if lessons_even:
                    group_schedule["even"][day_title] = lessons_even

            all_schedules[group_number.upper()] = group_schedule

        all_schedules["__teachers_index__"] = {t: list(l.values()) for t, l in teachers_index.items()}
        all_schedules["__classrooms_index__"] = {c: list(l.values()) for c, l in classrooms_index.items()}
        all_schedules["__current_xml_hash__"] = current_hash

        logging.info(f"XML расписание успешно загружено. Найдено {len([k for k in all_schedules if not k.startswith('__')])} групп.")
        return all_schedules

    except Exception as e:
        ERRORS_TOTAL.labels(source="parser").inc()
        logging.error(f"Ошибка при загрузке и парсинге XML: {e}")
        return None
    finally:
        if close_session and session and hasattr(session, "close") and callable(session.close):
            try:
                await session.close()
            except Exception:
                pass


async def fetch_and_parse_all_schedules(session: aiohttp.ClientSession | None = None, force: bool = False) -> dict | None:
    """
    Асинхронно загружает и парсит актуальное расписание.
    
    Приоритет источников:
      1. Основной официальный API https://voenmeh.su (с быстрым conditional check по updated_at)
      2. Резервный XML с voenmeh.ru (с валидацией актуальности учебного года)
      3. Локальный fallback (fallback_schedule.json)
    """
    logging.info("Загрузка полного расписания (основной источник: API voenmeh.su)...")

    # 1. Основной источник: voenmeh.su
    try:
        voenmeh_su_data = await fetch_and_parse_from_voenmeh_su(session=session, force=force)
        if voenmeh_su_data is NOT_MODIFIED:
            logging.info("Расписание на voenmeh.su не изменилось (условный запрос 304).")
            return None
        if voenmeh_su_data:
            logging.info("Расписание успешно получено с основного API voenmeh.su.")
            save_fallback_schedule(voenmeh_su_data)
            return voenmeh_su_data
        logging.warning("voenmeh.su не вернул данные. Переход к резервному источнику XML...")
    except Exception as e:
        ERRORS_TOTAL.labels(source="parser_voenmeh_su").inc()
        logging.warning(f"Не удалось получить расписание с voenmeh.su: {e}. Переход к резервному источнику XML...")

    # 2. Резервный источник: XML с voenmeh.ru
    try:
        xml_data = await _fetch_and_parse_legacy_xml(session=session)
        if xml_data is NOT_MODIFIED:
            logging.info("XML расписание на voenmeh.ru не изменилось (304).")
            return None
        if xml_data:
            logging.info("Расписание успешно получено с резервного XML источника.")
            save_fallback_schedule(xml_data)
            return xml_data
    except Exception as e:
        ERRORS_TOTAL.labels(source="parser_xml").inc()
        logging.error(f"Ошибка резервного источника XML: {e}")

    # 3. Локальный оффлайн-fallback
    fallback_data = load_fallback_schedule()
    if fallback_data:
        logging.warning("Используются сохранённые локальные fallback данные расписания.")
        try:
            from core.alert_sender import AlertSender

            alert_sender = AlertSender()
            await alert_sender.send_alert(
                "warning",
                "Использовано резервное расписание",
                "Не удалось загрузить свежее расписание с серверов. Использованы резервные данные.",
            )
        except Exception:
            pass
        return fallback_data

    logging.critical("Нет доступных данных расписания ни из одного источника.")
    return None

