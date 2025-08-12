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

@pytest.mark.asyncio
async def test_parser_success_builds_indexes(monkeypatch):
    from core import parser
    import hashlib
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Root>'
        '  <Period StartYear="2024" StartMonth="9" StartDay="1" />'
        '  <Weeks FirstWeek="odd" />'
        '  <Group Number="G1">'
        '    <Days>'
        '      <Day Title="Понедельник">'
        '        <GroupLessons>'
        '          <Lesson>'
        '            <Time>09:00</Time>'
        '            <Discipline>лек Математика</Discipline>'
        '            <Classroom>101</Classroom>'
        '            <WeekCode>1</WeekCode>'
        '            <Lecturers><Lecturer><ShortName>Иванов</ShortName></Lecturer></Lecturers>'
        '          </Lesson>'
        '        </GroupLessons>'
        '      </Day>'
        '    </Days>'
        '  </Group>'
        '</Root>'
    ).encode('utf-16')

    class Resp:
        status = 200
        headers = {}
        async def read(self):
            return xml
        def raise_for_status(self):
            return None

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def get(self, *a, **k):
            class Ctx:
                async def __aenter__(self_inner):
                    return Resp()
                async def __aexit__(self_inner, *a): return False
            return Ctx()

    monkeypatch.setattr('aiohttp.ClientSession', lambda *a, **k: Session())

    res = await parser.fetch_and_parse_all_schedules()
    assert res
    assert res['__current_xml_hash__'] == hashlib.md5(xml).hexdigest()
    assert 'G1' in res and '__teachers_index__' in res and '__classrooms_index__' in res


@pytest.mark.asyncio
async def test_parser_handles_304(monkeypatch):
    # Смоделируем 304 Not Modified
    import core.parser as parser
    class Resp:
        status = 304
        headers = {}
        async def read(self):
            return b''
        def raise_for_status(self):
            return None
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, *a, **k):
            class Ctx:
                async def __aenter__(self_inner):
                    return Resp()
                async def __aexit__(self_inner, *a):
                    return False
            return Ctx()
    monkeypatch.setattr('aiohttp.ClientSession', lambda *a, **k: Session())
    res = await parser.fetch_and_parse_all_schedules()
    assert res is None