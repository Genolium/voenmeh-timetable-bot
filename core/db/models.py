from __future__ import annotations
from datetime import datetime 
from sqlalchemy import BigInteger, String, TIMESTAMP, Boolean, Integer, Date, Index, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = 'users'

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=True)
    group: Mapped[str] = mapped_column(String, nullable=True)
    
    registration_date: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    last_active_date: Mapped[datetime] = mapped_column(TIMESTAMP, onupdate=func.now(), server_default=func.now())
    
    evening_notify: Mapped[bool] = mapped_column(Boolean, server_default='t', default=True)
    morning_summary: Mapped[bool] = mapped_column(Boolean, server_default='t', default=True)
    lesson_reminders: Mapped[bool] = mapped_column(Boolean, server_default='t', default=True)

    reminder_time_minutes: Mapped[int] = mapped_column(Integer, default=60, server_default='60', nullable=False)

    # Индексы для оптимизации частых запросов
    __table_args__ = (
        Index('idx_user_group', 'group'),  # Для поиска по группам
        Index('idx_user_last_active', 'last_active_date'),  # Для статистики активности
        Index('idx_user_registration', 'registration_date'),  # Для статистики регистраций
        Index('idx_user_notifications', 'evening_notify', 'morning_summary', 'lesson_reminders'),  # Для рассылок
    )

class SemesterSettings(Base):
    __tablename__ = 'semester_settings'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fall_semester_start: Mapped[datetime] = mapped_column(Date, nullable=False)  # Дата начала осеннего семестра
    spring_semester_start: Mapped[datetime] = mapped_column(Date, nullable=False)  # Дата начала весеннего семестра
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_by: Mapped[int] = mapped_column(BigInteger, nullable=False)  # Telegram ID администратора



class Event(Base):
    __tablename__ = 'events'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)  # Telegram file_id
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, server_default='t', nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, onupdate=func.now(), server_default=func.now())

    __table_args__ = (
        Index('idx_events_start', 'start_at'),
        Index('idx_events_published', 'is_published'),
    )