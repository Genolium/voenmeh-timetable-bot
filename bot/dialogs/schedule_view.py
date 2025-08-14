from datetime import date, timedelta, datetime
import os
from aiogram.types import CallbackQuery, ContentType, FSInputFile, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot
from aiogram_dialog import Dialog, Window, DialogManager, StartMode
from aiogram_dialog.widgets.text import Format, Const
from aiogram_dialog.widgets.kbd import Button, Row, SwitchTo
from aiogram_dialog.widgets.media import StaticMedia
from bot.utils.image_compression import get_telegram_safe_image_path

from .states import Schedule, MainMenu, SettingsMenu, FindMenu
from .constants import DialogDataKeys, WidgetIds
from core.manager import TimetableManager
from core.image_generator import generate_schedule_image
from bot.text_formatters import (
    format_schedule_text, generate_dynamic_header
)
from core.config import MOSCOW_TZ, NO_LESSONS_IMAGE_PATH, MEDIA_PATH, SUBSCRIPTION_CHANNEL
from core.metrics import SCHEDULE_GENERATION_TIME, IMAGE_CACHE_HITS, IMAGE_CACHE_MISSES, GROUP_POPULARITY, USER_ACTIVITY_DAILY
from core.image_cache_manager import ImageCacheManager
from bot.tasks import generate_week_image_task
import logging
import asyncio

async def cleanup_old_cache():
    """Очищает ВСЕ картинки из кэша."""
    try:
        output_dir = MEDIA_PATH / "generated"
        if not output_dir.exists():
            return
            
        deleted_files = 0
        deleted_size = 0
        
        for file_path in output_dir.glob("*.png"):
            try:
                file_size = file_path.stat().st_size
                file_path.unlink()
                deleted_files += 1
                deleted_size += file_size
                logging.info(f"Удален файл кэша: {file_path}")
            except Exception as e:
                logging.error(f"Ошибка при удалении файла {file_path}: {e}")
        
        logging.info(f"Очистка кэша завершена: удалено {deleted_files} файлов, {deleted_size / (1024*1024):.2f} MB")
    except Exception as e:
        logging.error(f"Ошибка при очистке кэша: {e}")

async def get_cache_info():
    """Возвращает информацию о размере кэша."""
    try:
        output_dir = MEDIA_PATH / "generated"
        if not output_dir.exists():
            return {"total_files": 0, "total_size_mb": 0, "cache_dir": str(output_dir)}
            
        total_files = 0
        total_size_bytes = 0
        file_list = []
        
        for file_path in output_dir.glob("*.png"):
            try:
                file_size = file_path.stat().st_size
                total_files += 1
                total_size_bytes += file_size
                file_list.append(f"{file_path.name} ({file_size / (1024*1024):.2f} MB)")
            except Exception as e:
                logging.error(f"Ошибка при получении информации о файле {file_path}: {e}")
            
        total_size_mb = round(total_size_bytes / (1024 * 1024), 2)
        
        logging.info(f"Кэш содержит {total_files} файлов: {', '.join(file_list)}")
        
        return {
            "total_files": total_files,
            "total_size_mb": total_size_mb,
            "cache_dir": str(output_dir),
            "files": file_list
        }
    except Exception as e:
        logging.error(f"Ошибка при получении информации о кэше: {e}")
        return {"error": str(e)}

async def get_schedule_data(dialog_manager: DialogManager, **kwargs):
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    ctx = dialog_manager.current_context()

    if DialogDataKeys.GROUP not in ctx.dialog_data:
        ctx.dialog_data[DialogDataKeys.GROUP] = dialog_manager.start_data.get(DialogDataKeys.GROUP)
        
    if not ctx.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO):
        ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = datetime.now(MOSCOW_TZ).date().isoformat()

    current_date = date.fromisoformat(ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO])
    group = ctx.dialog_data.get(DialogDataKeys.GROUP, "N/A")
    
    day_info = await manager.get_schedule_for_day(group, target_date=current_date)
    
    # Метрики активности и популярности
    try:
        GROUP_POPULARITY.labels(group_name=group.upper()).inc()
        USER_ACTIVITY_DAILY.labels(action_type="view_day", user_group=group.upper()).inc()
    except Exception:
        pass
    
    dynamic_header, progress_bar = generate_dynamic_header(day_info.get("lessons", []), current_date)

    return {
        "dynamic_header": dynamic_header,
        "progress_bar": progress_bar,
        "schedule_text": format_schedule_text(day_info),
        "has_lessons": bool(day_info.get("lessons"))
    }

async def on_full_week_image_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    # Защита от спама: проверяем, не нажимал ли пользователь кнопку недавно
    ctx = manager.current_context()
    user_id = callback.from_user.id
    
    # Проверяем время последнего нажатия для этого пользователя
    last_click_key = f"last_week_click:{user_id}"
    try:
        manager_obj = manager.middleware_data.get("manager")
        last_click_time = await manager_obj.redis.get(last_click_key)
        if last_click_time:
            # Если прошло меньше 3 секунд, игнорируем нажатие
            await callback.answer("⏳ Подождите немного, изображение уже генерируется...", show_alert=True)
            return
        # Устанавливаем блокировку на 3 секунды
        await manager_obj.redis.set(last_click_key, "1", ex=3)
    except Exception:
        pass
    
    # Сохраняем user_id, запускаем генерацию и фиксируем состояние на Schedule.view
    ctx.dialog_data["user_id"] = user_id
    await get_week_image_data(manager)
    try:
        await manager.switch_to(Schedule.view)
    except Exception:
        pass
    try:
        await callback.answer()
    except Exception:
        pass

async def get_week_image_data(dialog_manager: DialogManager, **kwargs):
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    bot = dialog_manager.middleware_data.get("bot")
    ctx = dialog_manager.current_context()
    
    if DialogDataKeys.GROUP not in ctx.dialog_data:
        ctx.dialog_data[DialogDataKeys.GROUP] = dialog_manager.start_data.get(DialogDataKeys.GROUP)
        
    if not ctx.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO):
        ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = datetime.now(MOSCOW_TZ).date().isoformat()

    current_date = date.fromisoformat(ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO])
    group = ctx.dialog_data.get(DialogDataKeys.GROUP, "N/A")

    # Метрики активности и популярности
    try:
        GROUP_POPULARITY.labels(group_name=group.upper()).inc()
        USER_ACTIVITY_DAILY.labels(action_type="view_week", user_group=group.upper()).inc()
    except Exception:
        pass
    
    week_info = manager.get_week_type(current_date)
    if not week_info:
        return {
            "week_name": "Неизвестно",
            "group": group,
            "start_date": "??.??",
            "end_date": "??.??"
        }
    
    week_key, week_name_full = week_info
    week_name = week_name_full.split(" ")[0]
    
    # Даты недели
    days_since_monday = current_date.weekday()
    monday_date = current_date - timedelta(days=days_since_monday)
    sunday_date = monday_date + timedelta(days=6)
    start_date_str = monday_date.strftime("%d.%m")
    end_date_str = sunday_date.strftime("%d.%m")

    # Ключ и пути
    cache_key = f"{group}_{week_key}"
    output_dir = MEDIA_PATH / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{cache_key}.png"

    cache_manager = ImageCacheManager(manager.redis, cache_ttl_hours=24)

    # 1) Попытка отдать из кэша (TTL учитывается в Redis)
    if await cache_manager.is_cached(cache_key):
        IMAGE_CACHE_HITS.labels(cache_type="week_schedule").inc()
        photo = FSInputFile(output_path)
        user_id = ctx.dialog_data.get("user_id")
        if user_id:
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]
            ])
            final_caption = (
                "✅ Расписание на неделю готово!\n\n"
                f"🗓 <b>Расписание для группы {group}</b>\n"
                f"Неделя: <b>{week_name}</b>\n"
                f"Период: <b>с {start_date_str} по {end_date_str}</b>"
            )
            await bot.send_photo(chat_id=user_id, photo=photo, caption=final_caption, reply_markup=back_kb)
        return {
            "week_name": week_name,
            "group": group,
            "start_date": start_date_str,
            "end_date": end_date_str
        }

    # 2) Попытка захватить лок на генерацию, чтобы избежать дубликатов
    lock_key = f"image_gen_lock:{cache_key}"
    lock_acquired = False
    try:
        # ex=120: двухминутная блокировка
        lock_acquired = await manager.redis.set(lock_key, "1", nx=True, ex=120)
    except Exception:
        pass

    # 3) Плейсхолдер только один раз на ключ
    placeholder_flag_key = f"placeholder_sent:{cache_key}"
    placeholder_sent = ctx.dialog_data.get(placeholder_flag_key)

    IMAGE_CACHE_MISSES.labels(cache_type="week_schedule").inc()

    if not placeholder_sent:
        placeholder_path = MEDIA_PATH / "logo.png"
        if os.path.exists(placeholder_path):
            placeholder_photo = FSInputFile(placeholder_path)
            user_id = ctx.dialog_data.get("user_id")
            if user_id:
                back_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]
                ])
                caption_text = (
                    "⏳ Генерирую расписание на неделю... Это может занять несколько секунд.\n\n"
                    f"🗓 <b>Расписание для группы {group}</b>\n"
                    f"Неделя: <b>{week_name}</b>\n"
                    f"Период: <b>с {start_date_str} по {end_date_str}</b>"
                )
                try:
                    sent_msg = await bot.send_photo(
                        chat_id=user_id,
                        photo=placeholder_photo,
                        caption=caption_text,
                        reply_markup=back_kb,
                        request_timeout=30  # Увеличиваем таймаут до 30 секунд
                    )
                except Exception as e:
                    logging.error(f"Ошибка при отправке плейсхолдера пользователю {user_id}: {e}")
                    ERRORS_TOTAL.labels(source='schedule_view').inc()
                    # Пытаемся отправить текстовое сообщение вместо фото
                    try:
                        sent_msg = await bot.send_message(
                            chat_id=user_id,
                            text=caption_text + "\n\n⏳ Генерирую изображение...",
                            reply_markup=back_kb
                        )
                    except Exception as e2:
                        logging.error(f"Не удалось отправить даже текстовое сообщение: {e2}")
                        return
                ctx.dialog_data[placeholder_flag_key] = True
                ctx.dialog_data[f"placeholder_msg_id:{cache_key}"] = sent_msg.message_id

    # 4) Запускаем генерацию в очереди Dramatiq только если лок получен
    if lock_acquired:
        full_schedule = manager._schedules.get(group.upper(), {})
        week_schedule = full_schedule.get(week_key, {})
        user_id = ctx.dialog_data.get("user_id")
        placeholder_msg_id = ctx.dialog_data.get(f"placeholder_msg_id:{cache_key}")
        final_caption = (
            "✅ Расписание на неделю готово!\n\n"
            f"🗓 <b>Расписание для группы {group}</b>\n"
            f"Неделя: <b>{week_name}</b>\n"
            f"Период: <b>с {start_date_str} по {end_date_str}</b>"
        )
        try:
            generate_week_image_task.send(cache_key, week_schedule, week_name, group, user_id, placeholder_msg_id, final_caption)
        except Exception:
            asyncio.create_task(
                generate_week_schedule_background(
                    manager=manager,
                    group=group,
                    week_key=week_key,
                    week_name=week_name,
                    output_path=str(output_path),
                    cache_key=cache_key,
                    bot=bot,
                    ctx=ctx,
                    dialog_manager=dialog_manager,
                    lock_key=lock_key,
                )
            )
    else:
        # Если лок не получен, но пользователь еще не получил плейсхолдер, отправляем его
        user_id = ctx.dialog_data.get("user_id")
        if user_id and not placeholder_sent:
            # Отправляем плейсхолдер и запускаем генерацию в фоне
            placeholder_path = MEDIA_PATH / "logo.png"
            if os.path.exists(placeholder_path):
                placeholder_photo = FSInputFile(placeholder_path)
                back_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]
                ])
                caption_text = (
                    "⏳ Генерирую расписание на неделю... Это может занять несколько секунд.\n\n"
                    f"🗓 <b>Расписание для группы {group}</b>\n"
                    f"Неделя: <b>{week_name}</b>\n"
                    f"Период: <b>с {start_date_str} по {end_date_str}</b>"
                )
                try:
                    sent_msg = await bot.send_photo(
                        chat_id=user_id,
                        photo=placeholder_photo,
                        caption=caption_text,
                        reply_markup=back_kb,
                        request_timeout=30
                    )
                    ctx.dialog_data[placeholder_flag_key] = True
                    ctx.dialog_data[f"placeholder_msg_id:{cache_key}"] = sent_msg.message_id
                    
                    # Запускаем генерацию в фоне без Dramatiq
                    asyncio.create_task(
                        generate_week_schedule_background(
                            manager=manager,
                            group=group,
                            week_key=week_key,
                            week_name=week_name,
                            output_path=str(output_path),
                            cache_key=cache_key,
                            bot=bot,
                            ctx=ctx,
                            dialog_manager=dialog_manager,
                            lock_key=lock_key,
                        )
                    )
                except Exception as e:
                    logging.error(f"Ошибка при отправке плейсхолдера пользователю {user_id}: {e}")
                    ERRORS_TOTAL.labels(source='schedule_view').inc()
                    # Пытаемся отправить текстовое сообщение вместо фото
                    try:
                        sent_msg = await bot.send_message(
                            chat_id=user_id,
                            text=caption_text + "\n\n⏳ Генерирую изображение...",
                            reply_markup=back_kb
                        )
                        ctx.dialog_data[placeholder_flag_key] = True
                        ctx.dialog_data[f"placeholder_msg_id:{cache_key}"] = sent_msg.message_id
                        
                        # Запускаем генерацию в фоне
                        asyncio.create_task(
                            generate_week_schedule_background(
                                manager=manager,
                                group=group,
                                week_key=week_key,
                                week_name=week_name,
                                output_path=str(output_path),
                                cache_key=cache_key,
                                bot=bot,
                                ctx=ctx,
                                dialog_manager=dialog_manager,
                                lock_key=lock_key,
                            )
                        )
                    except Exception as e2:
                        logging.error(f"Не удалось отправить даже текстовое сообщение: {e2}")
                        return

    return {
        "week_name": week_name,
        "group": group,
        "start_date": start_date_str,
        "end_date": end_date_str
    }

async def generate_week_schedule_background(
    manager: TimetableManager,
    group: str,
    week_key: str,
    week_name: str,
    output_path: str,
    cache_key: str,
    bot,
    ctx,
    dialog_manager: DialogManager,
    lock_key: str,
):
    """Генерирует расписание в фоне, сохраняет в кэш и отправляет пользователю."""
    try:
        start_time = datetime.now()
        
        # Проверяем, не было ли изображение уже сгенерировано другим процессом
        if os.path.exists(output_path):
            logging.info(f"Изображение {cache_key} уже существует, пропускаем генерацию")
            # Просто отправляем существующее изображение
            await send_generated_image_to_user(
                output_path=output_path,
                cache_key=cache_key,
                group=group,
                week_name=week_name,
                bot=bot,
                ctx=ctx
            )
            return
        
        full_schedule = manager._schedules.get(group.upper(), {})
        week_schedule = full_schedule.get(week_key, {})

        # Оптимизированный рендер для Telegram
        highres_vp = {"width": 2048, "height": 1400}
        success = await generate_schedule_image(
            schedule_data=week_schedule,
            week_type=week_name,
            group=group,
            output_path=str(output_path),
            viewport_size=highres_vp,
        )

        if success and os.path.exists(output_path):
            cache_manager = ImageCacheManager(manager.redis, cache_ttl_hours=24)
            try:
                with open(output_path, 'rb') as f:
                    image_bytes = f.read()
                await cache_manager.cache_image(cache_key, image_bytes, metadata={
                    "group": group,
                    "week_key": week_key,
                    "week_name": week_name,
                })
            except Exception as e:
                logging.warning(f"Не удалось сохранить изображение в кэш: {e}")

                        # Отправляем изображение пользователю
            await send_generated_image_to_user(
                output_path=output_path,
                cache_key=cache_key,
                group=group,
                week_name=week_name,
                bot=bot,
                ctx=ctx
            )
        else:
            logging.error(f"Не удалось сгенерировать изображение для {cache_key}")
    except Exception as e:
        logging.error(f"generate_week_schedule_background failed: {e}")
    finally:
        # Освобождаем лок
        try:
            await manager.redis.delete(lock_key)
        except Exception:
            pass

async def send_generated_image_to_user(
    output_path: str,
    cache_key: str,
    group: str,
    week_name: str,
    bot,
    ctx
):
    """Отправляет сгенерированное изображение пользователю."""
    try:
        # Сжимаем изображение для Telegram если нужно
        safe_image_path = get_telegram_safe_image_path(output_path)
        photo = FSInputFile(safe_image_path)
        user_id = ctx.dialog_data.get("user_id")
        
        if not user_id:
            return
            
        # Если есть плейсхолдер, редактируем сообщение, иначе отправляем новое фото
        placeholder_msg_id = ctx.dialog_data.get(f"placeholder_msg_id:{cache_key}")
        
        # Формируем даты для подписи
        current_date = date.fromisoformat(ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO])
        days_since_monday = current_date.weekday()
        monday_date = current_date - timedelta(days=days_since_monday)
        sunday_date = monday_date + timedelta(days=6)
        start_date_str = monday_date.strftime("%d.%m")
        end_date_str = sunday_date.strftime("%d.%m")
        
        final_caption = (
            "✅ Расписание на неделю готово!\n\n"
            f"🗓 <b>Расписание для группы {group}</b>\n"
            f"Неделя: <b>{week_name}</b>\n"
            f"Период: <b>с {start_date_str} по {end_date_str}</b>"
        )
        
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]
        ])
        
        if placeholder_msg_id:
            try:
                media = InputMediaPhoto(media=photo, caption=final_caption)
                await bot.edit_message_media(
                    chat_id=user_id, 
                    message_id=placeholder_msg_id, 
                    media=media, 
                    reply_markup=back_kb
                )
            except Exception as edit_error:
                logging.error(f"Ошибка при редактировании сообщения: {edit_error}")
                # Пробуем отправить новое сообщение
                try:
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=photo,
                        caption=final_caption,
                        reply_markup=back_kb,
                    )
                except Exception as send_error:
                    logging.error(f"Ошибка при отправке изображения: {send_error}")
                    ERRORS_TOTAL.labels(source='schedule_view').inc()
                    await bot.send_message(
                        chat_id=user_id,
                        text=f"{final_caption}\n\n⚠️ Изображение слишком большое для отправки."
                    )
        else:
            try:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=final_caption,
                    reply_markup=back_kb,
                )
            except Exception as send_error:
                logging.error(f"Ошибка при отправке изображения: {send_error}")
                ERRORS_TOTAL.labels(source='schedule_view').inc()
                await bot.send_message(
                    chat_id=user_id,
                    text=f"{final_caption}\n\n⚠️ Изображение слишком большое для отправки."
                )
    except Exception as e:
        logging.error(f"Ошибка в send_generated_image_to_user: {e}")

async def on_send_original_file_callback(callback: CallbackQuery, dialog_manager: DialogManager):
    """Callback handler для кнопки 'Оригинал' без параметра button"""
    await on_send_original_file(callback, None, dialog_manager)

async def on_send_original_file(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Отправляет оригинал изображения недели как файл (Document), без сжатия Telegram."""
    ctx = manager.current_context()
    manager_obj: TimetableManager = manager.middleware_data.get("manager")
    # Всегда сохраняем user_id для последующих отправок/плейсхолдера
    try:
        if callback.from_user:
            ctx.dialog_data["user_id"] = callback.from_user.id
    except Exception:
        pass
    # Поддержка нажатия за пределами диалога: достаём группу из БД пользователя при отсутствии в контексте
    if not ctx.dialog_data.get(DialogDataKeys.GROUP):
        try:
            user_data_manager = manager.middleware_data.get("user_data_manager")
            if user_data_manager and callback.from_user:
                saved_group = await user_data_manager.get_user_group(callback.from_user.id)
                if saved_group:
                    ctx.dialog_data[DialogDataKeys.GROUP] = saved_group
        except Exception:
            pass
    if not ctx.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO):
        ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = datetime.now(MOSCOW_TZ).date().isoformat()
    group = ctx.dialog_data.get(DialogDataKeys.GROUP, "N/A")
    try:
        current_date = date.fromisoformat(ctx.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO))
    except Exception:
        current_date = datetime.now(MOSCOW_TZ).date()
    week_info = manager_obj.get_week_type(current_date)
    if not week_info:
        try:
            await callback.answer("Неделя не определена", show_alert=True)
        except Exception:
            pass
        return
    week_key, week_name_full = week_info
    cache_key = f"{group}_{week_key}"
    output_dir = MEDIA_PATH / "generated"
    output_path = output_dir / f"{cache_key}.png"
    # Проверка подписки на канал, если настроено
    try:
        if SUBSCRIPTION_CHANNEL:
            bot: Bot = manager.middleware_data.get("bot")
            member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, callback.from_user.id)
            status = getattr(member, "status", None)
            if status not in ("member", "administrator", "creator"):
                # Переводим пользователя в окно-гейт
                try:
                    await manager.switch_to(Schedule.full_quality_gate)
                except Exception:
                    pass
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔔 Подписаться", url=f"https://t.me/{str(SUBSCRIPTION_CHANNEL).lstrip('@')}")],
                    [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]
                ])
                await callback.message.answer("Доступ к полному качеству доступен по подписке на канал.", reply_markup=kb)
                try:
                    await callback.answer()
                except Exception:
                    pass
                return
    except Exception:
        pass
    # Если отсутствует — инициируем фоновую генерацию
    if not output_path.exists():
        await get_week_image_data(manager)
        try:
            await callback.answer("Готовлю оригинал, вернитесь через пару секунд…")
        except Exception:
            pass
        return
    try:
        await callback.message.answer("📤 Отправляю изображение в полном качестве…")
        await callback.message.answer_document(FSInputFile(output_path))
    except Exception:
        pass
    try:
        await callback.answer()
    except Exception:
        pass

async def on_date_shift(callback: CallbackQuery, button: Button, manager: DialogManager, days: int):
    ctx = manager.current_context()
    current_date = date.fromisoformat(ctx.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO))
    new_date = current_date + timedelta(days=days)
    ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = new_date.isoformat()

async def on_today_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    manager.current_context().dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = datetime.now(MOSCOW_TZ).date().isoformat()
    
async def on_change_group_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(MainMenu.enter_group, mode=StartMode.RESET_STACK)
    
async def on_settings_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(SettingsMenu.main)

async def on_find_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(FindMenu.choice)

async def on_news_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Открывает канал с новостями разработки"""
    await callback.answer("📢 Переходим к новостям разработки!")
    await callback.message.answer(
        "🚀 <b>Новости разработки бота</b>\n\n"
        "Все обновления, планы и новости о разработке бота публикуются в нашем канале:\n\n"
        "📢 <a href='https://t.me/voenmeh404'>Аудитория 404 | Военмех</a>\n\n"
        "Там вы узнаете:\n"
        "• О новых функциях первыми\n"
        "• О планах развития\n"
        "• Сможете повлиять на разработку\n"
        "• Увидите закулисье проекта\n\n"
        "<i>Подписывайтесь, чтобы быть в курсе! 👆</i>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

async def on_inline_back(callback: CallbackQuery, dialog_manager: DialogManager):
    # Удаляем медиасообщение с изображением и остаёмся в текущем окне диалога
    try:
        await callback.message.delete()
    except Exception:
        pass
    try:
        await callback.answer()
    except Exception:
        pass

schedul_dialog_windows = [
    Window(
        StaticMedia(
            path=NO_LESSONS_IMAGE_PATH,
            type=ContentType.PHOTO,
            when=lambda data, widget, manager: not data.get("has_lessons")
        ),
        Format("{dynamic_header}"),
        Format("{progress_bar}"),
        Format("{schedule_text}"),
        Row(
            Button(Const("⏪"), id=WidgetIds.PREV_WEEK, on_click=lambda c, b, m: on_date_shift(c, b, m, -7)),
            Button(Const("◀️"), id=WidgetIds.PREV_DAY, on_click=lambda c, b, m: on_date_shift(c, b, m, -1)),
            Button(Const("📅"), id=WidgetIds.TODAY, on_click=on_today_click),
            Button(Const("▶️"), id=WidgetIds.NEXT_DAY, on_click=lambda c, b, m: on_date_shift(c, b, m, 1)),
            Button(Const("⏩"), id=WidgetIds.NEXT_WEEK, on_click=lambda c, b, m: on_date_shift(c, b, m, 7)),
        ),
        Row(
            Button(Const("🗓 Расписание на неделю"), id="week_as_image", on_click=on_full_week_image_click),
            Button(Const("🔄 Сменить группу"), id=WidgetIds.CHANGE_GROUP, on_click=on_change_group_click),
            Button(Const("⚙️ Настройки"), id=WidgetIds.SETTINGS, on_click=on_settings_click),
        ),
        Row(
            Button(Const("🔍 Поиск"), id=WidgetIds.FIND_BTN, on_click=on_find_click),
            Button(Const("📢 Новости"), id="news_btn", on_click=on_news_clicked),
        ),
        state=Schedule.view, getter=get_schedule_data,
        parse_mode="HTML", disable_web_page_preview=True
    ),
    Window(
        Const("🖼 Генерация или показ расписания недели выполняется в отдельном сообщении. Нажмите ◀️ Назад внизу изображения."),
        Button(Const("◀️ Назад"), id="noop_back_to_day", on_click=lambda c, b, m: m.switch_to(Schedule.view)),
        state=Schedule.week_image_view
    ),
    # Окно гейта подписки временно отключено
]

schedule_dialog = Dialog(*schedul_dialog_windows)