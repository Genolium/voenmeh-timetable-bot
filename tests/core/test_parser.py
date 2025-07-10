import pytest
from unittest.mock import AsyncMock, MagicMock
from core.parser import fetch_and_parse_all_schedules

@pytest.fixture
def sample_xml_bytes():
    """
    Фикстура с минимальным валидным XML.
    Сначала создаем обычную строку, а потом кодируем ее в байты.
    """
    xml_string = """<?xml version="1.0" encoding="utf-16"?>
<Timetable>
    <Period StartYear="2023" StartMonth="9" StartDay="1" />
    <Group Number="О735Б">
        <Days>
            <Day Title="Понедельник">
                <GroupLessons>
                    <Lesson>
                        <Time>09:00 </Time>
                        <Discipline>Лекция Математика</Discipline>
                        <Lecturers><Lecturer><ShortName>Иванов И.И.</ShortName></Lecturer></Lecturers>
                        <Classroom>101</Classroom>
                        <WeekCode>1</WeekCode>
                    </Lesson>
                </GroupLessons>
            </Day>
        </Days>
    </Group>
</Timetable>
    """.strip()
    return xml_string.encode('utf-16')

@pytest.mark.asyncio
async def test_fetch_and_parse_all_schedules(mocker, sample_xml_bytes):
    mock_response = AsyncMock()
    mock_response.read.return_value = sample_xml_bytes
    mock_response.raise_for_status = MagicMock()

    mock_session_get = AsyncMock()
    mock_session_get.__aenter__.return_value = mock_response
    
    mocker.patch('aiohttp.ClientSession.get', return_value=mock_session_get)
    
    result = await fetch_and_parse_all_schedules()

    assert result is not None
    assert "О735Б" in result
    assert "__teachers_index__" in result
    assert "__current_xml_hash__" in result
    
    group_schedule = result["О735Б"]
    assert "odd" in group_schedule
    assert "Понедельник" in group_schedule["odd"]
    
    lesson = group_schedule["odd"]["Понедельник"][0]
    assert lesson["subject"] == "Математика"
    assert lesson["type"] == "Лекция"
    assert lesson["teachers"] == "Иванов И.И."