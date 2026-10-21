from datetime import datetime

from core.time_utils import now_msk_naive, to_msk_naive
from core.config import MOSCOW_TZ


def test_now_msk_naive_matches_moscow_time_without_tzinfo():
    value = now_msk_naive()
    assert value.tzinfo is None
    expected = datetime.now(MOSCOW_TZ).replace(tzinfo=None)
    assert abs((value - expected).total_seconds()) < 5


def test_to_msk_naive_converts_utc():
    aware = datetime(2026, 1, 1, 0, 0, tzinfo=MOSCOW_TZ)
    naive = to_msk_naive(aware)
    assert naive.tzinfo is None
    assert naive.hour == 0
