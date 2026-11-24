"""Персональные правила редактирования расписания пользователем (скрытие предметов, заметки, свои пары)."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from redis.asyncio.client import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import DAY_MAP
from core.db.models import UserLessonRule

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 1800


def _cache_key(user_id: int, target_group: str) -> str:
    return f"user_rules:{user_id}:{target_group.upper()}"


def _serialize_rule(rule: UserLessonRule) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if rule.payload_json:
        try:
            loaded = json.loads(rule.payload_json)
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {}
    return {
        "id": rule.id,
        "rule_type": rule.rule_type,
        "target_group": rule.target_group,
        "week_key": rule.week_key,
        "day_name": rule.day_name,
        "start_time": rule.start_time,
        "subject": rule.subject,
        "payload": payload,
    }


def rules_fingerprint(rules: list[dict[str, Any]]) -> str:
    """Короткий детерминированный хэш набора правил для ключа кэша изображений."""
    if not rules:
        return ""
    canonical = json.dumps(rules, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]


def _matches_scope(rule: dict[str, Any], *, day_name: str, week_key: str) -> bool:
    r_week = rule.get("week_key")
    if r_week and r_week != week_key:
        return False
    r_day = rule.get("day_name")
    if r_day and r_day.upper() != day_name.upper():
        return False
    return True


def apply_rules_to_lessons(
    lessons: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    *,
    day_name: str,
    week_key: str,
) -> list[dict[str, Any]]:
    """Применяет персональные правила пользователя к списку пар одного дня."""
    if not rules:
        return list(lessons)

    hidden_subjects: set[str] = set()
    notes_by_subject: dict[str, list[dict[str, Any]]] = {}
    custom_lessons: list[dict[str, Any]] = []

    for r in rules:
        rtype = r.get("rule_type")
        if not _matches_scope(r, day_name=day_name, week_key=week_key):
            continue
        subj_key = str(r.get("subject") or "").strip().upper()
        if rtype == "hide_subject" and subj_key:
            hidden_subjects.add(subj_key)
        elif rtype == "note" and subj_key:
            notes_by_subject.setdefault(subj_key, []).append(r)
        elif rtype == "custom_lesson":
            payload = r.get("payload") or {}
            start_raw = str(r.get("start_time") or payload.get("start_time_raw") or "09:00").strip()
            end_raw = str(payload.get("end_time_raw") or start_raw).strip()
            custom_lessons.append(
                {
                    "time": f"{start_raw}-{end_raw}",
                    "start_time_raw": start_raw,
                    "end_time_raw": end_raw,
                    "subject": str(r.get("subject") or "Своя пара"),
                    "type": str(payload.get("type") or "своя"),
                    "room": str(payload.get("room") or ""),
                    "teachers": str(payload.get("teachers") or ""),
                    "groups": [str(r.get("target_group") or "")],
                    "is_custom": True,
                }
            )

    result: list[dict[str, Any]] = []
    for lesson in lessons:
        subj = str(lesson.get("subject") or "")
        subj_key = subj.strip().upper()
        if subj_key in hidden_subjects:
            continue
        item = dict(lesson)
        matching_notes = notes_by_subject.get(subj_key) or []
        note_texts: list[str] = []
        for nr in matching_notes:
            r_start = nr.get("start_time")
            if r_start and r_start != item.get("start_time_raw"):
                continue
            note_val = str((nr.get("payload") or {}).get("note") or "").strip()
            if note_val:
                note_texts.append(note_val)
        if note_texts:
            item["subject"] = f"{subj} [{'; '.join(note_texts)}]"
            item["user_note"] = "; ".join(note_texts)
        result.append(item)

    result.extend(custom_lessons)
    result.sort(key=lambda x: str(x.get("start_time_raw") or "99:99"))
    return result


def apply_rules_to_weeks(
    weeks_data: dict[str, dict[str, list[dict[str, Any]]]],
    rules: list[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Применяет персональные правила ко всему двухнедельному расписанию."""
    if not rules:
        return weeks_data

    day_names = [d for d in DAY_MAP if d]
    updated: dict[str, dict[str, list[dict[str, Any]]]] = {"odd": {}, "even": {}}
    for week_key in ("odd", "even"):
        week_dict = weeks_data.get(week_key) or {}
        all_days = list(dict.fromkeys([*day_names, *week_dict.keys()]))
        for day_name in all_days:
            day_lessons = week_dict.get(day_name) or []
            processed = apply_rules_to_lessons(day_lessons, rules, day_name=day_name, week_key=week_key)
            if processed or day_name in week_dict:
                updated[week_key][day_name] = processed
    return updated


class UserScheduleRulesManager:
    """CRUD и кэширование персональных правил расписания."""

    def __init__(self, session_factory: async_sessionmaker, redis_client: Redis | None = None) -> None:
        self.session_factory = session_factory
        self.redis = redis_client

    async def _invalidate(self, user_id: int, target_group: str | None = None) -> None:
        if not self.redis:
            return
        try:
            if target_group:
                await self.redis.delete(_cache_key(user_id, target_group))
                return
            async for key in self.redis.scan_iter(f"user_rules:{user_id}:*"):
                await self.redis.delete(key)
        except Exception as e:
            logger.warning("Failed to invalidate user_rules cache for %s: %s", user_id, e)

    async def get_rules(self, user_id: int, target_group: str) -> list[dict[str, Any]]:
        if not target_group:
            return []
        norm_group = target_group.upper()
        key = _cache_key(user_id, norm_group)
        if self.redis:
            try:
                cached = await self.redis.get(key)
                if cached:
                    raw = cached.decode("utf-8") if isinstance(cached, bytes) else cached
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return parsed
            except Exception as e:
                logger.warning("Failed to read user_rules cache: %s", e)

        async with self.session_factory() as session:
            stmt = (
                select(UserLessonRule)
                .where(UserLessonRule.user_id == user_id, UserLessonRule.target_group == norm_group)
                .order_by(UserLessonRule.id.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            rules = [_serialize_rule(row) for row in rows]

        if self.redis:
            try:
                await self.redis.set(key, json.dumps(rules, ensure_ascii=False), ex=_CACHE_TTL_SECONDS)
            except Exception:
                pass
        return rules

    async def toggle_hide_subject(self, user_id: int, target_group: str, subject: str) -> bool:
        """Скрывает или возвращает предмет; возвращает True, если предмет теперь скрыт."""
        norm_group = target_group.upper()
        norm_subject = subject.strip()
        async with self.session_factory() as session:
            stmt = select(UserLessonRule).where(
                UserLessonRule.user_id == user_id,
                UserLessonRule.target_group == norm_group,
                UserLessonRule.rule_type == "hide_subject",
                UserLessonRule.subject == norm_subject,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                await session.delete(existing)
                await session.commit()
                hidden = False
            else:
                session.add(
                    UserLessonRule(
                        user_id=user_id,
                        rule_type="hide_subject",
                        target_group=norm_group,
                        subject=norm_subject,
                    )
                )
                await session.commit()
                hidden = True

        await self._invalidate(user_id, norm_group)
        return hidden

    async def add_note(
        self,
        user_id: int,
        target_group: str,
        subject: str,
        note: str,
        *,
        week_key: str | None = None,
        day_name: str | None = None,
        start_time: str | None = None,
    ) -> int:
        norm_group = target_group.upper()
        payload = json.dumps({"note": note.strip()}, ensure_ascii=False)
        async with self.session_factory() as session:
            rule = UserLessonRule(
                user_id=user_id,
                rule_type="note",
                target_group=norm_group,
                week_key=week_key,
                day_name=day_name,
                start_time=start_time,
                subject=subject.strip(),
                payload_json=payload,
            )
            session.add(rule)
            await session.commit()
            await session.refresh(rule)
            rule_id = rule.id
        await self._invalidate(user_id, norm_group)
        return rule_id

    async def add_custom_lesson(
        self,
        user_id: int,
        target_group: str,
        *,
        day_name: str,
        start_time: str,
        end_time: str,
        subject: str,
        room: str = "",
        lesson_type: str = "своя",
        week_key: str | None = None,
    ) -> int:
        norm_group = target_group.upper()
        payload = json.dumps(
            {
                "start_time_raw": start_time.strip(),
                "end_time_raw": end_time.strip(),
                "room": room.strip(),
                "type": lesson_type.strip(),
            },
            ensure_ascii=False,
        )
        async with self.session_factory() as session:
            rule = UserLessonRule(
                user_id=user_id,
                rule_type="custom_lesson",
                target_group=norm_group,
                week_key=week_key,
                day_name=day_name,
                start_time=start_time.strip(),
                subject=subject.strip(),
                payload_json=payload,
            )
            session.add(rule)
            await session.commit()
            await session.refresh(rule)
            rule_id = rule.id
        await self._invalidate(user_id, norm_group)
        return rule_id

    async def delete_rule(self, user_id: int, rule_id: int) -> bool:
        async with self.session_factory() as session:
            stmt = select(UserLessonRule).where(UserLessonRule.id == rule_id, UserLessonRule.user_id == user_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if not row:
                return False
            group = row.target_group
            await session.delete(row)
            await session.commit()
        await self._invalidate(user_id, group)
        return True

    async def clear_rules(self, user_id: int, target_group: str | None = None) -> int:
        async with self.session_factory() as session:
            stmt = delete(UserLessonRule).where(UserLessonRule.user_id == user_id)
            if target_group:
                stmt = stmt.where(UserLessonRule.target_group == target_group.upper())
            res = await session.execute(stmt)
            await session.commit()
            deleted = int(res.rowcount or 0)
        await self._invalidate(user_id, target_group)
        return deleted
