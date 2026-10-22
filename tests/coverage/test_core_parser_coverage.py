"""Coverage tests for ``core.parser``.

Every HTTP interaction is faked; no real network access happens.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from core import parser

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Resp:
    """Fake aiohttp response usable as an async context manager."""

    def __init__(self, status=200, data=None, content=b"", headers=None, raise_exc=None, read_exc=None):
        self.status = status
        self._data = data if data is not None else {}
        self._content = content
        self.headers = headers or {}
        self._raise = raise_exc
        self._read_exc = read_exc

    async def json(self):
        return self._data

    async def read(self):
        if self._read_exc:
            raise self._read_exc
        return self._content

    def raise_for_status(self):
        if self._raise:
            raise self._raise


class _Ctx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp

    async def __aexit__(self, *args):
        return False


class FakeVoenmehSession:
    def __init__(self, meta, lessons=None, close_raises=False):
        self.meta = meta
        self.lessons = lessons or {}
        self.close_raises = close_raises
        self.closed = False

    def get(self, url, **kwargs):
        if "/meta" in url:
            return _Ctx(self.meta)
        name = parse_qs(urlparse(url).query).get("name", [""])[0]
        return _Ctx(self.lessons.get(name, _Resp(404)))

    async def close(self):
        self.closed = True
        if self.close_raises:
            raise RuntimeError("close failed")


class FakeXmlSession:
    def __init__(self, responses, close_raises=False):
        self._responses = list(responses)
        self.close_raises = close_raises
        self.closed = False

    def get(self, url, **kwargs):
        return _Ctx(self._responses.pop(0))

    async def close(self):
        self.closed = True
        if self.close_raises:
            raise RuntimeError("close failed")


class _SessionFactory:
    def __init__(self, session):
        self.session = session
        self.kwargs = None

    def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return self.session


class _RaisingMetric:
    def labels(self, *args, **kwargs):
        raise RuntimeError("metric boom")


@pytest.fixture(autouse=True)
def _reset_parser_globals():
    parser._LAST_VOENMEH_SU_UPDATED_AT = None
    parser._LAST_ETAG = None
    parser._LAST_MODIFIED = None
    yield
    parser._LAST_VOENMEH_SU_UPDATED_AT = None
    parser._LAST_ETAG = None
    parser._LAST_MODIFIED = None


# ---------------------------------------------------------------------------
# helpers / fallback file
# ---------------------------------------------------------------------------


def test_not_modified_sentinel_repr():
    assert repr(parser.NOT_MODIFIED) == "<NOT_MODIFIED>"


def test_has_enough_groups():
    assert parser.has_enough_groups(0, 0) is False
    assert parser.has_enough_groups(1, 0) is False
    assert parser.has_enough_groups(4, 5) is True
    assert parser.has_enough_groups(3, 5) is False


def test_load_fallback_schedule_missing(tmp_path):
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", tmp_path / "missing.json"):
        assert parser.load_fallback_schedule() is None


def test_load_fallback_schedule_success(tmp_path):
    f = tmp_path / "fb.json"
    f.write_text(json.dumps({"__current_xml_hash__": "h", "G": {}}), encoding="utf-8")
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", f):
        data = parser.load_fallback_schedule()
    assert data["__current_xml_hash__"] == "h"


def test_load_fallback_schedule_invalid_json(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", f):
        assert parser.load_fallback_schedule() is None


def test_save_fallback_schedule_success(tmp_path):
    f = tmp_path / "nested" / "fb.json"
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", f):
        assert parser.save_fallback_schedule({"G": {}}) is True
    assert f.exists()


def test_save_fallback_schedule_error(tmp_path):
    f = tmp_path / "fb.json"
    with (
        patch("core.parser.FALLBACK_SCHEDULE_PATH", f),
        patch("core.parser.json.dump", side_effect=RuntimeError("boom")),
    ):
        assert parser.save_fallback_schedule({"G": {}}) is False


# ---------------------------------------------------------------------------
# fetch_and_parse_from_voenmeh_su
# ---------------------------------------------------------------------------


def _lesson(day=1, week="odd", time="14:55", kind="пр", subject="НИР", teachers=None, rooms=None):
    return {
        "day": day,
        "week": week,
        "time": time,
        "kind": kind,
        "subject": subject,
        "teachers": teachers if teachers is not None else ["Снижко Е.А."],
        "rooms": rooms if rooms is not None else ["258*"],
    }


@pytest.mark.asyncio
async def test_voenmeh_su_meta_304():
    session = FakeVoenmehSession(_Resp(304))
    assert await parser.fetch_and_parse_from_voenmeh_su(session=session) is parser.NOT_MODIFIED


@pytest.mark.asyncio
async def test_voenmeh_su_meta_error():
    session = FakeVoenmehSession(_Resp(500))
    assert await parser.fetch_and_parse_from_voenmeh_su(session=session) is None


@pytest.mark.asyncio
async def test_voenmeh_su_unchanged_updated_at():
    parser._LAST_VOENMEH_SU_UPDATED_AT = "2026-09-10T09:16:14Z"
    session = FakeVoenmehSession(_Resp(200, {"updated_at": "2026-09-10T09:16:14Z", "groups": ["G1"]}))
    assert await parser.fetch_and_parse_from_voenmeh_su(session=session) is parser.NOT_MODIFIED

    # force=True bypasses the check.
    session = FakeVoenmehSession(
        _Resp(200, {"updated_at": "2026-09-10T09:16:14Z", "groups": ["G1"]}),
        {"G1": _Resp(200, {"lessons": [_lesson()]})},
    )
    result = await parser.fetch_and_parse_from_voenmeh_su(session=session, force=True)
    assert result is not None


@pytest.mark.asyncio
async def test_voenmeh_su_empty_groups():
    session = FakeVoenmehSession(_Resp(200, {"groups": [], "updated_at": "x"}))
    assert await parser.fetch_and_parse_from_voenmeh_su(session=session) is None


@pytest.mark.asyncio
async def test_voenmeh_su_partial_groups_rejected():
    groups = [f"G{i}" for i in range(5)]
    lessons = {g: _Resp(200, {"lessons": [_lesson()]}) for g in groups[:3]}
    # Remaining two groups fail.
    lessons[groups[3]] = _Resp(500)
    lessons[groups[4]] = RuntimeError("boom")
    session = FakeVoenmehSession(_Resp(200, {"groups": groups, "updated_at": "x"}), lessons)
    assert await parser.fetch_and_parse_from_voenmeh_su(session=session) is None


@pytest.mark.asyncio
async def test_voenmeh_su_success_full():
    lessons = [
        _lesson(day=1, week="odd", time="14:55", subject="НИР", teachers=["A"], rooms=["258*"]),
        _lesson(day=2, week="even", time="12:40", subject="X", teachers=["B"], rooms=["216*"]),
        _lesson(day=3, week="all", time="09:00", subject="Y", teachers=["C"], rooms=[]),
        _lesson(day=7, week="odd", time="10:00"),  # unknown day -> skipped
        _lesson(day=1, week="odd", time="N/A", subject="Z", teachers=[], rooms=[]),  # bad time
        _lesson(day=1, week="odd", time="09:00", subject="Shared", teachers=["D"], rooms=["500"]),
        _lesson(day=1, week="odd", time="09:00", subject="Shared", teachers=["D"], rooms=["500"]),
        # Teacher-less lesson duplicated across groups -> classroom duplicate append.
        _lesson(day=4, week="odd", time="13:00", subject="NoTeacher", teachers=[], rooms=["777"]),
    ]
    # Second group repeats identical lessons to exercise duplicate handling.
    lessons_g2 = [
        _lesson(day=1, week="odd", time="09:00", subject="Shared", teachers=["D"], rooms=["500"]),
        _lesson(day=4, week="odd", time="13:00", subject="NoTeacher", teachers=[], rooms=["777"]),
    ]
    session = FakeVoenmehSession(
        _Resp(200, {"groups": ["G1", "G2"], "updated_at": "u", "period": "ОСЕННИЙ СЕМЕСТР 2026/2027 уч. г."}),
        {
            "G1": _Resp(200, {"lessons": lessons}),
            "G2": _Resp(200, {"lessons": lessons_g2}),
        },
    )
    result = await parser.fetch_and_parse_from_voenmeh_su(session=session)
    assert result is not None
    assert "G1" in result and "G2" in result
    assert "__teachers_index__" in result
    assert "__classrooms_index__" in result
    assert result["__metadata__"]["period"]["StartYear"] == "2026"
    # Empty rooms -> placeholder.
    assert any(lesson["room"] == "кабинет не указан" for day in result["G1"]["odd"].values() for lesson in day)


@pytest.mark.asyncio
async def test_voenmeh_su_spring_period():
    session = FakeVoenmehSession(
        _Resp(200, {"groups": ["G1"], "updated_at": "u", "period": "ВЕСЕННИЙ СЕМЕСТР 2025/2026 уч. г."}),
        {"G1": _Resp(200, {"lessons": [_lesson()]})},
    )
    result = await parser.fetch_and_parse_from_voenmeh_su(session=session)
    assert result["__metadata__"]["period"]["StartMonth"] == "2"
    assert result["__metadata__"]["period"]["StartYear"] == "2026"


@pytest.mark.asyncio
async def test_voenmeh_su_period_without_digits():
    session = FakeVoenmehSession(
        _Resp(200, {"groups": ["G1"], "updated_at": "u", "period": "ВЕСЕННИЙ СЕМЕСТР"}),
        {"G1": _Resp(200, {"lessons": [_lesson()]})},
    )
    result = await parser.fetch_and_parse_from_voenmeh_su(session=session)
    assert result["__metadata__"]["period"]["Title"] == "ВЕСЕННИЙ СЕМЕСТР"


@pytest.mark.asyncio
async def test_voenmeh_su_period_missing():
    session = FakeVoenmehSession(
        _Resp(200, {"groups": ["G1"], "updated_at": "u"}),
        {"G1": _Resp(200, {"lessons": [_lesson()]})},
    )
    result = await parser.fetch_and_parse_from_voenmeh_su(session=session)
    assert result["__metadata__"]["period"]["Title"] == ""


@pytest.mark.asyncio
async def test_voenmeh_su_creates_and_closes_session(monkeypatch):
    session = FakeVoenmehSession(_Resp(304))
    factory = _SessionFactory(session)
    monkeypatch.setattr(parser.aiohttp, "ClientSession", factory)
    assert await parser.fetch_and_parse_from_voenmeh_su() is parser.NOT_MODIFIED
    assert session.closed is True
    assert factory.kwargs["headers"]["User-Agent"]


@pytest.mark.asyncio
async def test_voenmeh_su_close_failure(monkeypatch):
    session = FakeVoenmehSession(_Resp(304), close_raises=True)
    monkeypatch.setattr(parser.aiohttp, "ClientSession", lambda *a, **k: session)
    assert await parser.fetch_and_parse_from_voenmeh_su() is parser.NOT_MODIFIED


@pytest.mark.asyncio
async def test_voenmeh_su_metric_errors(monkeypatch):
    # Success path with a failing status metric.
    monkeypatch.setattr(parser, "EXTERNAL_SERVICE_STATUS", _RaisingMetric())
    session = FakeVoenmehSession(
        _Resp(200, {"groups": ["G1"], "updated_at": "u"}),
        {"G1": _Resp(200, {"lessons": [_lesson()]})},
    )
    assert await parser.fetch_and_parse_from_voenmeh_su(session=session) is not None

    # Error path with a failing status metric.
    session = FakeVoenmehSession(_Resp(500))
    assert await parser.fetch_and_parse_from_voenmeh_su(session=session) is None


@pytest.mark.asyncio
async def test_voenmeh_su_lessons_not_dict():
    session = FakeVoenmehSession(
        _Resp(200, {"groups": ["G1"], "updated_at": "u"}),
        {"G1": _Resp(200, "not-a-dict")},
    )
    # Group payload is invalid -> treated as no data -> partial rejection -> None.
    assert await parser.fetch_and_parse_from_voenmeh_su(session=session) is None


# ---------------------------------------------------------------------------
# _fetch_and_parse_legacy_xml
# ---------------------------------------------------------------------------

FULL_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<Timetable>"
    '<Period StartYear="2026" StartMonth="9" StartDay="1" />'
    '<Weeks FirstWeek="odd" />'
    '<Group Number="G1">'
    "<Days>"
    '<Day Title="Понедельник">'
    "<GroupLessons>"
    "<Lesson>"
    "<Time>09:00</Time>"
    "<Discipline>лек Математика</Discipline>"
    "<Classroom>101</Classroom>"
    "<WeekCode>1</WeekCode>"
    "<Lecturers><Lecturer><ShortName>Иванов</ShortName></Lecturer></Lecturers>"
    "</Lesson>"
    "<Lesson>"
    "<Time>N/A</Time>"
    "<Discipline>безтипа</Discipline>"
    "<Classroom></Classroom>"
    "<WeekCode>2</WeekCode>"
    "</Lesson>"
    "<Lesson>"
    "<Time>11:00</Time>"
    "<Discipline>пр Физика</Discipline>"
    "<Classroom>202</Classroom>"
    "<WeekCode>0</WeekCode>"
    "<Lecturers><Lecturer><ShortName>Петров</ShortName></Lecturer></Lecturers>"
    "</Lesson>"
    "<Lesson>"
    "<Time>13:00</Time>"
    "<Discipline>лек БезПрепода</Discipline>"
    "<Classroom>303</Classroom>"
    "<WeekCode>1</WeekCode>"
    "</Lesson>"
    "</GroupLessons>"
    "</Day>"
    "<Day><GroupLessons/></Day>"
    "</Days>"
    "</Group>"
    '<Group Number="G2">'
    "<Days>"
    '<Day Title="Понедельник">'
    "<GroupLessons>"
    "<Lesson>"
    "<Time>09:00</Time>"
    "<Discipline>лек Математика</Discipline>"
    "<Classroom>101</Classroom>"
    "<WeekCode>1</WeekCode>"
    "<Lecturers><Lecturer><ShortName>Иванов</ShortName></Lecturer></Lecturers>"
    "</Lesson>"
    "<Lesson>"
    "<Time>13:00</Time>"
    "<Discipline>лек БезПрепода</Discipline>"
    "<Classroom>303</Classroom>"
    "<WeekCode>1</WeekCode>"
    "</Lesson>"
    "</GroupLessons>"
    "</Day>"
    "</Days>"
    "</Group>"
    "<Group><Days/></Group>"
    "</Timetable>"
).encode("utf-8")


@pytest.mark.asyncio
async def test_legacy_xml_success():
    session = FakeXmlSession([_Resp(200, content=FULL_XML, headers={"ETag": "e1", "Last-Modified": "lm1"})])
    result = await parser._fetch_and_parse_legacy_xml(session=session)
    assert result is not None
    assert "G1" in result and "G2" in result
    assert "__metadata__" in result
    assert "__current_xml_hash__" in result
    assert parser._LAST_ETAG == "e1"
    assert parser._LAST_MODIFIED == "lm1"
    # Duplicate lesson across groups -> teacher index accumulates groups.
    shared = result["__teachers_index__"]["Иванов"]
    assert any(len(lesson["groups"]) >= 2 for lesson in shared)


@pytest.mark.asyncio
async def test_legacy_xml_304():
    session = FakeXmlSession([_Resp(304)])
    assert await parser._fetch_and_parse_legacy_xml(session=session) is parser.NOT_MODIFIED


@pytest.mark.asyncio
async def test_legacy_xml_retry_then_success():
    session = FakeXmlSession([_Resp(200, raise_exc=RuntimeError("net")), _Resp(200, content=FULL_XML)])
    result = await parser._fetch_and_parse_legacy_xml(session=session)
    assert result is not None


@pytest.mark.asyncio
async def test_legacy_xml_all_attempts_fail():
    session = FakeXmlSession(
        [
            _Resp(200, raise_exc=RuntimeError("net")),
            _Resp(200, raise_exc=RuntimeError("net")),
            _Resp(200, raise_exc=RuntimeError("net")),
        ]
    )
    assert await parser._fetch_and_parse_legacy_xml(session=session) is None


@pytest.mark.asyncio
async def test_legacy_xml_empty_body():
    session = FakeXmlSession([_Resp(200, content=b"")])
    assert await parser._fetch_and_parse_legacy_xml(session=session) is None


@pytest.mark.asyncio
async def test_legacy_xml_undecodable_body():
    session = FakeXmlSession([_Resp(200, content=b"\x00\x01\x02")])
    assert await parser._fetch_and_parse_legacy_xml(session=session) is None


@pytest.mark.asyncio
async def test_legacy_xml_windows1251_encoding():
    xml = (
        '<?xml version="1.0" encoding="windows-1251"?>'
        '<Timetable><Period StartYear="2026"/><Group Number="Г1">'
        '<Days><Day Title="Понедельник"><GroupLessons><Lesson>'
        "<Time>09:00</Time><Discipline>лек Математика</Discipline>"
        "<Classroom>101</Classroom><WeekCode>1</WeekCode>"
        "</Lesson></GroupLessons></Day></Days></Group></Timetable>"
    ).encode("windows-1251")
    session = FakeXmlSession([_Resp(200, content=xml)])
    result = await parser._fetch_and_parse_legacy_xml(session=session)
    assert result is not None


@pytest.mark.asyncio
async def test_legacy_xml_no_period_or_weeks():
    xml = b'<?xml version="1.0" encoding="utf-8"?><Timetable><Group Number="G1"><Days/></Group></Timetable>'
    session = FakeXmlSession([_Resp(200, content=xml)])
    result = await parser._fetch_and_parse_legacy_xml(session=session)
    assert result["__metadata__"]["period"] == {}


@pytest.mark.asyncio
async def test_legacy_xml_outdated_year(monkeypatch):
    monkeypatch.setattr(
        parser,
        "API_URL",
        "https://voenmeh.ru/wp-content/themes/Avada-Child-Theme-Voenmeh/_voenmeh_grafics/TimetableGroup50.xml",
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><Timetable>'
        '<Period StartYear="2000" StartMonth="9" StartDay="1" />'
        '<Group Number="G1"><Days/></Group></Timetable>'
    ).encode("utf-8")
    session = FakeXmlSession([_Resp(200, content=xml)])
    assert await parser._fetch_and_parse_legacy_xml(session=session) is None


@pytest.mark.asyncio
async def test_legacy_xml_creates_session_with_conditional_headers(monkeypatch):
    parser._LAST_ETAG = "etag-x"
    parser._LAST_MODIFIED = "lm-x"
    session = FakeXmlSession([_Resp(200, content=FULL_XML)])
    factory = _SessionFactory(session)
    monkeypatch.setattr(parser.aiohttp, "ClientSession", factory)
    result = await parser._fetch_and_parse_legacy_xml()
    assert result is not None
    assert factory.kwargs["headers"]["If-None-Match"] == "etag-x"
    assert factory.kwargs["headers"]["If-Modified-Since"] == "lm-x"
    assert session.closed is True


@pytest.mark.asyncio
async def test_legacy_xml_close_failure(monkeypatch):
    session = FakeXmlSession([_Resp(200, content=FULL_XML)], close_raises=True)
    monkeypatch.setattr(parser.aiohttp, "ClientSession", lambda *a, **k: session)
    assert await parser._fetch_and_parse_legacy_xml() is not None


# ---------------------------------------------------------------------------
# fetch_and_parse_all_schedules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_all_voenmeh_su_not_modified():
    with patch("core.parser.fetch_and_parse_from_voenmeh_su", AsyncMock(return_value=parser.NOT_MODIFIED)):
        assert await parser.fetch_and_parse_all_schedules() is None


@pytest.mark.asyncio
async def test_fetch_all_voenmeh_su_success():
    data = {"__current_xml_hash__": "h", "G": {}}
    with (
        patch("core.parser.fetch_and_parse_from_voenmeh_su", AsyncMock(return_value=data)),
        patch("core.parser.save_fallback_schedule") as save,
    ):
        result = await parser.fetch_and_parse_all_schedules()
    assert result == data
    save.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_all_voenmeh_su_exception_then_xml_success():
    data = {"__current_xml_hash__": "x", "G": {}}
    with (
        patch("core.parser.fetch_and_parse_from_voenmeh_su", AsyncMock(side_effect=RuntimeError("down"))),
        patch("core.parser._fetch_and_parse_legacy_xml", AsyncMock(return_value=data)),
        patch("core.parser.save_fallback_schedule") as save,
    ):
        result = await parser.fetch_and_parse_all_schedules()
    assert result == data
    save.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_all_xml_not_modified():
    with (
        patch("core.parser.fetch_and_parse_from_voenmeh_su", AsyncMock(return_value=None)),
        patch("core.parser._fetch_and_parse_legacy_xml", AsyncMock(return_value=parser.NOT_MODIFIED)),
    ):
        assert await parser.fetch_and_parse_all_schedules() is None


@pytest.mark.asyncio
async def test_fetch_all_xml_exception_then_fallback():
    data = {"__current_xml_hash__": "fb", "G": {}}
    with (
        patch("core.parser.fetch_and_parse_from_voenmeh_su", AsyncMock(return_value=None)),
        patch("core.parser._fetch_and_parse_legacy_xml", AsyncMock(side_effect=RuntimeError("xml down"))),
        patch("core.parser.load_fallback_schedule", return_value=data),
        patch("core.alert_sender.AlertSender") as alert_cls,
    ):
        alert_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock(send_alert=AsyncMock()))
        alert_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await parser.fetch_and_parse_all_schedules()
    assert result == data


@pytest.mark.asyncio
async def test_fetch_all_fallback_alert_sender_failure():
    data = {"__current_xml_hash__": "fb", "G": {}}
    with (
        patch("core.parser.fetch_and_parse_from_voenmeh_su", AsyncMock(return_value=None)),
        patch("core.parser._fetch_and_parse_legacy_xml", AsyncMock(return_value=None)),
        patch("core.parser.load_fallback_schedule", return_value=data),
        patch("core.alert_sender.AlertSender", side_effect=RuntimeError("no sender")),
    ):
        assert await parser.fetch_and_parse_all_schedules() == data


@pytest.mark.asyncio
async def test_fetch_all_no_data_available():
    with (
        patch("core.parser.fetch_and_parse_from_voenmeh_su", AsyncMock(return_value=None)),
        patch("core.parser._fetch_and_parse_legacy_xml", AsyncMock(return_value=None)),
        patch("core.parser.load_fallback_schedule", return_value=None),
    ):
        assert await parser.fetch_and_parse_all_schedules() is None


@pytest.mark.asyncio
async def test_fetch_all_metrics_paths(monkeypatch):
    monkeypatch.setattr(parser, "PARSER_DURATION", _RaisingMetric())

    with patch("core.parser._fetch_and_parse_all_schedules_impl", AsyncMock(return_value={"G": {}})):
        assert await parser.fetch_and_parse_all_schedules() == {"G": {}}

    with patch("core.parser._fetch_and_parse_all_schedules_impl", AsyncMock(return_value=None)):
        assert await parser.fetch_and_parse_all_schedules() is None
