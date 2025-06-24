import requests
import xml.etree.ElementTree as ET
from core.config import API_URL, USER_AGENT

def fetch_and_parse_all_schedules() -> dict | None:
    """
    Загружает и парсит ОДИН XML-файл, содержащий расписания для всех групп.
    Возвращает структурированный словарь или None в случае ошибки.
    """
    print("Загрузка полного расписания с сервера...")
    try:
        response = requests.get(API_URL, headers={'User-Agent': USER_AGENT}, timeout=15)
        response.raise_for_status()
        xml_data = response.content.decode('utf-16').strip()
        root = ET.fromstring(xml_data)
        
        all_schedules = {}
        
        # Сохраняем глобальные метаданные
        period_meta = root.find('Period').attrib if root.find('Period') is not None else {}
        weeks_meta = root.find('Weeks').attrib if root.find('Weeks') is not None else {}
        all_schedules['__metadata__'] = {'period': period_meta, 'weeks': weeks_meta}

        # Парсим каждую группу
        for group_element in root.findall('Group'):
            group_number = group_element.get('Number')
            if not group_number:
                continue
            
            group_schedule = {'odd': {}, 'even': {}}
            for day_element in group_element.findall('.//Day'):
                day_title = day_element.get('Title')
                if not day_title: continue
                
                lessons_odd, lessons_even = [], []
                for lesson_element in day_element.findall('.//Lesson'):
                    time_tag = lesson_element.find('Time')
                    discipline_tag = lesson_element.find('Discipline')
                    classroom_tag = lesson_element.find('Classroom')
                    week_code_tag = lesson_element.find('WeekCode')

                    time_raw = time_tag.text.strip() if time_tag is not None and time_tag.text else "N/A"
                    discipline_raw = discipline_tag.text.strip() if discipline_tag is not None and discipline_tag.text else "N/A"
                    disc_parts = discipline_raw.split(' ', 1)
                    
                    lesson_info = {
                        "time": time_raw.split()[0],
                        "subject": disc_parts[1] if len(disc_parts) > 1 else discipline_raw,
                        "type": disc_parts[0],
                        "teachers": ", ".join([l.text.strip() for l in lesson_element.findall('.//Lecturer/ShortName') if l.text and l.text.strip()]),
                        "room": classroom_tag.text.strip(';* ') if classroom_tag is not None and classroom_tag.text and classroom_tag.text.strip() else 'кабинет не указан',
                    }

                    week_code = week_code_tag.text if week_code_tag is not None else '0'
                    if week_code == '1': lessons_odd.append(lesson_info)
                    elif week_code == '2': lessons_even.append(lesson_info)
                    else: lessons_odd.append(lesson_info); lessons_even.append(lesson_info)

                if lessons_odd: group_schedule['odd'][day_title] = lessons_odd
                if lessons_even: group_schedule['even'][day_title] = lessons_even
            
            all_schedules[group_number.upper()] = group_schedule

        print(f"Расписание успешно загружено. Найдено {len(all_schedules)-1} групп.")
        return all_schedules
        
    except Exception as e:
        print(f"Произошла ошибка при загрузке и парсинге: {e}")
        return None