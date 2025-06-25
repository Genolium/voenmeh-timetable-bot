import requests
import xml.etree.ElementTree as ET
import hashlib
from datetime import datetime, timedelta 
from core.config import API_URL, USER_AGENT 

def fetch_and_parse_all_schedules() -> dict | None:
    print("Загрузка полного расписания с сервера...")
    try:
        response = requests.get(API_URL, headers={'User-Agent': USER_AGENT}, timeout=15)
        response.raise_for_status()
        xml_bytes = response.content
        xml_data = xml_bytes.decode('utf-16').strip()
        
        current_hash = hashlib.md5(xml_bytes).hexdigest()

        root = ET.fromstring(xml_data)
        
        all_schedules = {}
        teachers_index = {}
        classrooms_index = {}
        
        period_element = root.find('Period')
        period_meta = period_element.attrib if period_element is not None else {}
        weeks_element = root.find('Weeks')
        weeks_meta = weeks_element.attrib if weeks_element is not None else {}
        all_schedules['__metadata__'] = {'period': period_meta, 'weeks': weeks_meta}

        for group_element in root.findall('Group'):
            group_number = group_element.get('Number')
            if not group_number:
                continue
            
            group_schedule = {'odd': {}, 'even': {}}
            
            for day_element in group_element.findall('Days/Day'):
                day_title = day_element.get('Title')
                if not day_title:
                    continue
                
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
                    classroom = classroom_tag.text.strip(';* ') if classroom_tag is not None and classroom_tag.text and classroom_tag.text.strip() else None

                    lesson_start_time_str = time_raw.split()[0] # "10:50 Нечетная" -> "10:50"
                    
                    try:
                        # Парсим время начала, добавляем 90 минут
                        start_dt_obj = datetime.strptime(lesson_start_time_str, '%H:%M')
                        end_dt_obj = start_dt_obj + timedelta(minutes=90)
                        lesson_end_time_str = end_dt_obj.strftime('%H:%M')
                    except ValueError:
                        lesson_end_time_str = "N/A" # В случае ошибки парсинга времени

                    lesson_info = {
                        "time": f"{lesson_start_time_str}-{lesson_end_time_str}", # Форматируем время в "начало-конец"
                        "subject": disc_parts[1] if len(disc_parts) > 1 else discipline_raw,
                        "type": disc_parts[0],
                        "teachers": ", ".join(lecturers),
                        "room": classroom or 'кабинет не указан',
                        "group": group_number.upper(),
                        "start_time_raw": lesson_start_time_str, # Для сортировки и планировщика
                        "end_time_raw": lesson_end_time_str,     # Для планировщика
                    }
                    
                    week_code = week_code_tag.text if week_code_tag is not None else '0'
                    if week_code == '1': 
                        lessons_odd.append(lesson_info)
                    elif week_code == '2': 
                        lessons_even.append(lesson_info)
                    else: 
                        lessons_odd.append(lesson_info)
                        lessons_even.append(lesson_info)

                    lesson_for_index = lesson_info.copy()
                    lesson_for_index['day'] = day_title
                    lesson_for_index['week_code'] = week_code
                    lesson_for_index['groups'] = [lesson_info["group"]]
                    
                    lesson_key_components = [
                        day_title, week_code, lesson_info['time'], # Используем уже форматированный интервал
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
        
        print(f"Расписание успешно загружено. Найдено {len(all_schedules)-3} групп.")
        return all_schedules
        
    except Exception as e:
        print(f"Произошла ошибка при загрузке и парсинге: {e}")
        return None