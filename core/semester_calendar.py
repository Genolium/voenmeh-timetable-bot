"""Single source of truth for academic semester weeks.

All schedule week calculations (odd/even parity and week numbers) must go
through this module so that text output and generated images never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_FALL_START = (9, 1)
DEFAULT_SPRING_START = (2, 9)
SEMESTER_LENGTH_WEEKS = 18


@dataclass(frozen=True, slots=True)
class SemesterInfo:
    semester: str  # "fall" | "spring"
    semester_start: date
    week_number: int  # 1-based, 0 when outside active semesters
    week_key: str  # "odd" | "even"

    @property
    def is_active(self) -> bool:
        return self.week_number > 0


def _academic_year_start(target: date) -> int:
    """September starts a new academic year; August and earlier belong to the previous one."""
    return target.year if target.month >= 8 else target.year - 1


def _monday_of_week(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _resolve_dates(
    target: date,
    fall_start: date | None,
    spring_start: date | None,
) -> tuple[date, date]:
    """Roll stored month/day settings onto the academic year that contains target."""
    fall_month, fall_day = (fall_start.month, fall_start.day) if fall_start else DEFAULT_FALL_START
    spring_month, spring_day = (spring_start.month, spring_start.day) if spring_start else DEFAULT_SPRING_START
    academic_year = _academic_year_start(target)
    fall = date(academic_year, fall_month, fall_day)
    spring = date(academic_year + 1, spring_month, spring_day)
    return fall, spring


def get_semester_info(
    target: date,
    fall_start: date | None = None,
    spring_start: date | None = None,
) -> SemesterInfo:
    """Return semester, week number and odd/even parity for ``target``.

    Settings are stored as month/day; their year is ignored and the dates are
    rolled onto the academic year that contains ``target``. This keeps the bot
    correct when the admin forgets to update the year every September.
    """
    fall, spring = _resolve_dates(target, fall_start, spring_start)

    if target >= spring:
        semester = "spring"
        semester_start = spring
    else:
        semester = "fall"
        semester_start = fall

    start_monday = _monday_of_week(semester_start)
    target_monday = _monday_of_week(target)
    weeks_since_start = (target_monday - start_monday).days // 7
    week_number = weeks_since_start + 1

    # Fall semester runs through January; spring ends after 18 weeks.
    if semester == "spring" and target >= semester_start + timedelta(weeks=SEMESTER_LENGTH_WEEKS):
        week_number = 0

    week_key = "odd" if week_number % 2 == 1 else "even"
    return SemesterInfo(
        semester=semester,
        semester_start=semester_start,
        week_number=max(week_number, 0),
        week_key=week_key,
    )
