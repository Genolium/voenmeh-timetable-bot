import aiohttp
import xml.etree.ElementTree as ET
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from core.config import API_URL, USER_AGENT
from core.metrics import ERRORS_TOTAL, RETRIES_TOTAL
import logging

# Заготовки для условного кэширования
_LAST_ETAG: str | None = None
_LAST_MODIFIED: str | None = None

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

        with open(FALLBACK_SCHEDULE_PATH, 'r', encoding='utf-8') as f:
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
        with open(FALLBACK_SCHEDULE_PATH, 'w', encoding='utf-8') as f:
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
            "weeks": {"CurrentWeekType": "odd", "WeekStart": "2024-09-01"}
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
                        "week_type": "odd"
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
                        "week_type": "even"
                    }
                ]
            }
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
                        "week_type": "odd"
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
                        "week_type": "even"
                    }
                ]
            }
        }
    }

    try:
        return save_fallback_schedule(initial_data)
    except Exception as e:
        logging.error(f"Failed to create initial fallback schedule: {e}")
        return False

async def fetch_and_parse_all_schedules() -> dict | None:
    """
    Асинхронно загружает и парсит XML, возвращая словарь с расписанием,
    индексами и хешем XML-контента.
    """
    print("Асинхронная загрузка полного расписания с сервера...")
    try:
        global _LAST_ETAG, _LAST_MODIFIED
        headers = {'User-Agent': USER_AGENT}
        if _LAST_ETAG:
            headers['If-None-Match'] = _LAST_ETAG
        if _LAST_MODIFIED:
            headers['If-Modified-Since'] = _LAST_MODIFIED

        async with aiohttp.ClientSession(headers=headers) as session:
            attempts = 0
            while True:
                try:
                    attempts += 1
                    async with session.get(API_URL, timeout=15) as response:
                        response.raise_for_status()
                        if response.status == 304:
                            return None
                        xml_bytes = await response.read()
                        _LAST_ETAG = response.headers.get('ETag') or _LAST_ETAG
                        _LAST_MODIFIED = response.headers.get('Last-Modified') or _LAST_MODIFIED
                        break
                except Exception:
                    ERRORS_TOTAL.labels(source='parser').inc()
                    if attempts < 3:
                        RETRIES_TOTAL.labels(component='parser').inc()
                        continue
                    # New: Handle full failure with fallback
                    ERRORS_TOTAL.labels(source='parser').inc()
                    logging.critical("Failed to fetch XML after 3 attempts. Attempting to use fallback data.")

                    # Попытка использовать fallback данные
                    fallback_data = load_fallback_schedule()
                    if fallback_data:
                        logging.warning("Using fallback schedule data due to network failure.")
                        # Отправляем алерт о проблеме
                        try:
                            from core.alert_sender import AlertSender
                            alert_settings = {}  # Load from config if needed
                            async with AlertSender(alert_settings) as sender:
                                await sender.send({
                                    "severity": "warning",
                                    "summary": "Using fallback schedule data",
                                    "description": "Network failure - switched to offline mode"
                                })
                        except Exception:
                            pass  # Не прерываем выполнение из-за ошибки алерта
                        return fallback_data
                    else:
                        logging.critical("No fallback data available. Cannot continue.")
                        # Отправляем критический алерт
                        try:
                            from core.alert_sender import AlertSender
                            alert_settings = {}  # Load from config if needed
                            async with AlertSender(alert_settings) as sender:
                                await sender.send({
                                    "severity": "critical",
                                    "summary": "XML fetch failed and no fallback data available"
                                })
                        except Exception:
                            pass
                        raise
        
        # Ограничиваем общее время обработки
        try:
            import asyncio
            async with asyncio.timeout(30):
                xml_data = xml_bytes.decode('utf-16').strip()
                current_hash = hashlib.md5(xml_bytes).hexdigest()
                root = ET.fromstring(xml_data)
        except Exception as e:
            ERRORS_TOTAL.labels(source='parser').inc()
            logging.error(f"XML parsing timed out or failed: {e}")

            # Попытка использовать fallback данные при ошибке парсинга
            fallback_data = load_fallback_schedule()
            if fallback_data:
                logging.warning("Using fallback schedule data due to XML parsing failure.")
                try:
                    from core.alert_sender import AlertSender
                    alert_settings = {}
                    async with AlertSender(alert_settings) as sender:
                        await sender.send({
                            "severity": "warning",
                            "summary": "XML parsing failed - using fallback data",
                            "description": f"Parsing error: {str(e)}"
                        })
                except Exception:
                    pass
                return fallback_data
            else:
                logging.error("XML parsing failed and no fallback data available.")
                return None
        
        all_schedules = {}
        teachers_index = {}
        classrooms_index = {}
        
        period_meta = root.find('Period').attrib if root.find('Period') is not None else {}
        weeks_meta = root.find('Weeks').attrib if root.find('Weeks') is not None else {}
        all_schedules['__metadata__'] = {'period': period_meta, 'weeks': weeks_meta}

        for group_element in root.findall('Group'):
            group_number = group_element.get('Number')
            if not group_number: continue
            
            group_schedule = {'odd': {}, 'even': {}}
            for day_element in group_element.findall('Days/Day'):
                day_title = day_element.get('Title')
                if not day_title: continue
                
                lessons_odd, lessons_even = [], []
                for lesson_element in day_element.findall('GroupLessons/Lesson'):
                    time_tag = lesson_element.find('Time')
                    discipline_tag = lesson_element.find('Discipline')
                    classroom_tag = lesson_element.find('Classroom')
                    week_code_tag = lesson_element.find('WeekCode')

                    time_raw = time_tag.text.strip() if time_tag is not None and time_tag.text else "N/A"
                    discipline_raw = discipline_tag.text.strip() if discipline_tag is not None and discipline_tag.text else "N/A"
                    disc_parts = discipline_raw.split(' ', 1)
                    
                    lecturers = [l.text.strip() for l in lesson_element.findall('Lecturers/Lecturer/ShortName') if l.text and l.text.strip()]
                    classroom = classroom_tag.text.strip('; ') if classroom_tag is not None and classroom_tag.text and classroom_tag.text.strip() else None

                    start_time_token = time_raw.split()[0]
                    try:
                        start_dt_obj = datetime.strptime(start_time_token, '%H:%M')
                        # Нормализуем к 2-значному часу
                        start_time_str = start_dt_obj.strftime('%H:%M')
                        end_dt_obj = start_dt_obj + timedelta(minutes=90)
                        end_time_str = end_dt_obj.strftime('%H:%M')
                    except ValueError:
                        # Если формат неожиданно иной, оставляем как есть
                        start_time_str = start_time_token
                        end_time_str = "N/A"

                    lesson_info = {
                        "time": f"{start_time_str}-{end_time_str}",
                        "subject": disc_parts[1] if len(disc_parts) > 1 else discipline_raw,
                        "type": disc_parts[0],
                        "teachers": ", ".join(lecturers),
                        "room": classroom or 'кабинет не указан',
                        "group": group_number.upper(),
                        "start_time_raw": start_time_str,
                        "end_time_raw": end_time_str,
                    }
                    
                    week_code = week_code_tag.text if week_code_tag is not None else '0'
                    if week_code == '1': lessons_odd.append(lesson_info)
                    elif week_code == '2': lessons_even.append(lesson_info)
                    else: lessons_odd.append(lesson_info); lessons_even.append(lesson_info)

                    lesson_for_index = lesson_info.copy()
                    lesson_for_index['day'] = day_title
                    lesson_for_index['week_code'] = week_code
                    lesson_for_index['groups'] = [lesson_info["group"]]
                    
                    lesson_key_components = [
                        day_title, week_code, lesson_info['time'],
                        lesson_info['subject'], lesson_info['type'],
                        lesson_info['room'], "|".join(sorted(lecturers))
                    ]
                    lesson_key = "-".join(lesson_key_components)

                    for lecturer in lecturers:
                        if lecturer not in teachers_index:
                            teachers_index[lecturer] = {}
                        if lesson_key in teachers_index[lecturer]:
                            teachers_index[lecturer][lesson_key]["groups"].append(lesson_info["group"])
                        else:
                            teachers_index[lecturer][lesson_key] = lesson_for_index.copy()

                    if classroom and classroom != 'кабинет не указан':
                        if classroom not in classrooms_index:
                            classrooms_index[classroom] = {}
                        if lesson_key in classrooms_index[classroom]:
                            classrooms_index[classroom][lesson_key]["groups"].append(lesson_info["group"])
                        else:
                            classrooms_index[classroom][lesson_key] = lesson_for_index.copy()
                
                if lessons_odd: group_schedule['odd'][day_title] = lessons_odd
                if lessons_even: group_schedule['even'][day_title] = lessons_even
            
            all_schedules[group_number.upper()] = group_schedule
        
        all_schedules['__teachers_index__'] = {t: list(l.values()) for t, l in teachers_index.items()}
        all_schedules['__classrooms_index__'] = {c: list(l.values()) for c, l in classrooms_index.items()}
        all_schedules['__current_xml_hash__'] = current_hash

        # Обновляем fallback файл с актуальными данными для оффлайн-режима
        try:
            save_fallback_schedule(all_schedules)
            logging.info("Fallback schedule updated with current data")
        except Exception as e:
            logging.warning(f"Failed to update fallback schedule: {e}")

        print(f"Расписание успешно загружено. Найдено {len(all_schedules)-3} групп.")
        return all_schedules
        
    except Exception as e:
        ERRORS_TOTAL.labels(source='parser').inc()
        print(f"Произошла ошибка при загрузке и парсинге: {e}")
        return None