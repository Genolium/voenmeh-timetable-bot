from core.metrics import ERRORS_TOTAL, RETRIES_TOTAL, LAST_SCHEDULE_UPDATE_TS


def test_counters_and_gauge_increment_and_set():
    # базовые smoke-тесты метрик
    before = ERRORS_TOTAL.labels(source='parser')._value.get()
    ERRORS_TOTAL.labels(source='parser').inc()
    after = ERRORS_TOTAL.labels(source='parser')._value.get()
    assert after == before + 1

    r_before = RETRIES_TOTAL.labels(component='weather')._value.get()
    RETRIES_TOTAL.labels(component='weather').inc()
    r_after = RETRIES_TOTAL.labels(component='weather')._value.get()
    assert r_after == r_before + 1

    LAST_SCHEDULE_UPDATE_TS.set(1234567890)
    # Если set не бросил — считаем успехом


