"""Тесты для маппинга кастомных эмодзи и CustomEmojiRequestMiddleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.client.default import Default, DefaultBotProperties
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
    SendPhoto,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from bot.middlewares.custom_emoji_middleware import (
    CustomEmojiRequestMiddleware,
    _is_html_parse_mode,
    attach_custom_emoji_middleware,
)
from core.config import settings
from core.custom_emojis import (
    EMOJI_TO_CUSTOM_ID,
    replace_emojis_html,
    transform_inline_button,
    transform_inline_keyboard,
)


def test_replace_emojis_html_basic_and_idempotent():
    assert replace_emojis_html("") == ""
    assert replace_emojis_html("Обычный текст без эмодзи") == "Обычный текст без эмодзи"

    src = "☀️ <b>Доброе утро!</b> 📍 216*"
    res = replace_emojis_html(src)
    assert f'<tg-emoji emoji-id="{EMOJI_TO_CUSTOM_ID["☀️"]}">☀️</tg-emoji>' in res
    assert f'<tg-emoji emoji-id="{EMOJI_TO_CUSTOM_ID["📍"]}">📍</tg-emoji>' in res
    assert "<b>Доброе утро!</b>" in res
    # Идемпотентность: повторный вызов не оборачивает <tg-emoji> дважды.
    assert replace_emojis_html(res) == res


def test_replace_emojis_html_skips_code_pre_and_tag_attributes():
    src = '<b>✅ Готово</b> <code>✅ raw</code> <pre>❌ log</pre> <a href="https://example.com/?x=✅">🔔 ссылка</a>'
    res = replace_emojis_html(src)
    assert f'<tg-emoji emoji-id="{EMOJI_TO_CUSTOM_ID["✅"]}">✅</tg-emoji> Готово' in res
    assert "<code>✅ raw</code>" in res
    assert "<pre>❌ log</pre>" in res
    assert 'href="https://example.com/?x=✅"' in res
    assert f'<tg-emoji emoji-id="{EMOJI_TO_CUSTOM_ID["🔔"]}">🔔</tg-emoji> ссылка' in res


def test_transform_inline_button_and_keyboard():
    # Кнопка с текстом и известным эмодзи -> переносим в icon_custom_emoji_id
    btn = InlineKeyboardButton(text="📅 Неделя", callback_data="week")
    updated = transform_inline_button(btn)
    assert updated.text == "Неделя"
    assert updated.icon_custom_emoji_id == EMOJI_TO_CUSTOM_ID["📅"]

    # Уже с icon_custom_emoji_id -> не трогаем
    assert transform_inline_button(updated) is updated

    # Чисто символьная кнопка без текста -> не трогаем
    only_emoji = InlineKeyboardButton(text="✅", callback_data="ok")
    assert transform_inline_button(only_emoji) is only_emoji

    # Кнопка без эмодзи из маппинга и без особого стиля -> не трогаем
    plain = InlineKeyboardButton(text="Обычная", callback_data="other")
    assert transform_inline_button(plain) is plain

    # Клавиатура без изменений возвращает тот же объект
    unchanged_kb = InlineKeyboardMarkup(inline_keyboard=[[plain, only_emoji]])
    assert transform_inline_keyboard(unchanged_kb) is unchanged_kb

    # Клавиатура с изменениями обновляет только подходящие кнопки
    mixed_kb = InlineKeyboardMarkup(inline_keyboard=[[btn, plain]])
    updated_kb = transform_inline_keyboard(mixed_kb)
    assert updated_kb is not mixed_kb
    assert updated_kb.inline_keyboard[0][0].text == "Неделя"
    assert updated_kb.inline_keyboard[0][0].icon_custom_emoji_id == EMOJI_TO_CUSTOM_ID["📅"]
    assert updated_kb.inline_keyboard[0][1] is plain

    # Цветные стили Bot API 9.4 (primary / success / danger)
    primary_btn = transform_inline_button(InlineKeyboardButton(text="Сегодня", callback_data="abc\x1dtoday"))
    assert primary_btn.style == "primary"

    success_btn = transform_inline_button(
        InlineKeyboardButton(text="✅ Отправить", callback_data="abc\x1dconfirm_broadcast_send")
    )
    assert success_btn.style == "success"
    assert success_btn.text == "Отправить"

    danger_btn = transform_inline_button(InlineKeyboardButton(text="❌ Отмена", callback_data="abc\x1dfeedback_cancel"))
    assert danger_btn.style == "danger"

    danger_text_btn = transform_inline_button(InlineKeyboardButton(text="❌ Удалить", callback_data="custom_del"))
    assert danger_text_btn.style == "danger"

    url_sub_btn = transform_inline_button(InlineKeyboardButton(text="🔔 Подписаться", url="https://t.me/voenmeh404"))
    assert url_sub_btn.style == "primary"

    # Уже заданный стиль не перезаписывается
    preset = InlineKeyboardButton(text="Сегодня", callback_data="abc\x1dtoday", style="success")
    assert transform_inline_button(preset) is preset

    # Выделение цветом кнопок настроек
    settings_btn = transform_inline_button(InlineKeyboardButton(text="⚙️ Настройки", callback_data="abc\x1dsettings"))
    assert settings_btn.style == "primary"
    assert settings_btn.text == "Настройки"

    theme_btn = transform_inline_button(InlineKeyboardButton(text="🎨 Тема: Vice VI", callback_data="abc\x1dtheme_btn"))
    assert theme_btn.style == "primary"

    enable_btn = transform_inline_button(
        InlineKeyboardButton(text="Включить утреннюю сводку", callback_data="abc\x1dmorning_summary")
    )
    assert enable_btn.style == "success"

    disable_btn = transform_inline_button(
        InlineKeyboardButton(text="Отключить утреннюю сводку", callback_data="abc\x1dmorning_summary")
    )
    assert disable_btn.style == "danger"

    unknown_toggle = transform_inline_button(InlineKeyboardButton(text="Статус", callback_data="abc\x1dmorning_summary"))
    assert unknown_toggle.style is None


def test_is_html_parse_mode():
    html_bot = MagicMock(spec=Bot)
    html_bot.default = DefaultBotProperties(parse_mode="HTML")

    md_bot = MagicMock(spec=Bot)
    md_bot.default = DefaultBotProperties(parse_mode="Markdown")

    no_default_bot = MagicMock(spec=Bot)
    no_default_bot.default = None

    assert _is_html_parse_mode(None, html_bot) is True
    assert _is_html_parse_mode(Default("parse_mode"), html_bot) is True
    assert _is_html_parse_mode(None, md_bot) is False
    assert _is_html_parse_mode(None, no_default_bot) is False
    assert _is_html_parse_mode("HTML", md_bot) is True
    assert _is_html_parse_mode("MarkdownV2", html_bot) is False


@pytest.mark.asyncio
async def test_middleware_transforms_text_caption_media_and_markup(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_EMOJIS_ENABLED", True)
    mw = CustomEmojiRequestMiddleware()
    bot = MagicMock(spec=Bot)
    bot.default = DefaultBotProperties(parse_mode="HTML")

    captured = []

    async def fake_make_request(_bot, method):
        captured.append(method)
        return "ok"

    # 1. SendMessage (text + reply_markup)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔍 Поиск", callback_data="find")]])
    msg = SendMessage(chat_id=1, text="☀️ Утро", reply_markup=kb)
    await mw(fake_make_request, bot, msg)
    sent_msg = captured[-1]
    assert "<tg-emoji" in sent_msg.text
    assert sent_msg.reply_markup.inline_keyboard[0][0].text == "Поиск"
    assert sent_msg.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id == EMOJI_TO_CUSTOM_ID["🔍"]

    # 2. SendMessage без изменений (без эмодзи)
    plain_msg = SendMessage(chat_id=1, text="Просто текст")
    await mw(fake_make_request, bot, plain_msg)
    assert captured[-1] is plain_msg

    # 3. SendPhoto (caption)
    photo = SendPhoto(chat_id=1, photo="file_id", caption="🗓 Расписание")
    await mw(fake_make_request, bot, photo)
    assert "<tg-emoji" in captured[-1].caption

    # 4. SendPhoto без эмодзи в caption
    plain_photo = SendPhoto(chat_id=1, photo="file_id", caption="Расписание")
    await mw(fake_make_request, bot, plain_photo)
    assert captured[-1] is plain_photo

    # 5. EditMessageMedia (media.caption + reply_markup)
    media_method = EditMessageMedia(
        chat_id=1,
        message_id=2,
        media=InputMediaPhoto(media="file_id", caption="📍 216*"),
        reply_markup=kb,
    )
    await mw(fake_make_request, bot, media_method)
    assert "<tg-emoji" in captured[-1].media.caption
    assert captured[-1].reply_markup.inline_keyboard[0][0].text == "Поиск"

    # 6. EditMessageMedia без эмодзи в caption и вообще без caption
    plain_media = EditMessageMedia(
        chat_id=1,
        message_id=2,
        media=InputMediaPhoto(media="file_id", caption="216*"),
    )
    await mw(fake_make_request, bot, plain_media)
    assert captured[-1] is plain_media

    no_caption_media = EditMessageMedia(
        chat_id=1,
        message_id=2,
        media=InputMediaPhoto(media="file_id"),
    )
    await mw(fake_make_request, bot, no_caption_media)
    assert captured[-1] is no_caption_media

    # 7. EditMessageReplyMarkup без изменений
    unchanged_rm = EditMessageReplyMarkup(
        chat_id=1,
        message_id=2,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Ок", callback_data="ok")]]),
    )
    await mw(fake_make_request, bot, unchanged_rm)
    assert captured[-1] is unchanged_rm

    # 8. AnswerCallbackQuery не трогается (нет parse_mode)
    cb = AnswerCallbackQuery(callback_query_id="1", text="✅ Сохранено")
    await mw(fake_make_request, bot, cb)
    assert captured[-1] is cb

    # 9. EditMessageText с Markdown не трогается
    md_msg = EditMessageText(chat_id=1, message_id=2, text="✅ Сохранено", parse_mode="Markdown")
    await mw(fake_make_request, bot, md_msg)
    assert captured[-1] is md_msg


@pytest.mark.asyncio
async def test_middleware_disabled(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_EMOJIS_ENABLED", False)
    mw = CustomEmojiRequestMiddleware()
    bot = MagicMock(spec=Bot)
    bot.default = DefaultBotProperties(parse_mode="HTML")
    make_request = AsyncMock(return_value="ok")
    msg = SendMessage(chat_id=1, text="☀️ Утро")

    res = await mw(make_request, bot, msg)
    assert res == "ok"
    make_request.assert_awaited_once_with(bot, msg)


def test_attach_custom_emoji_middleware():
    from bot.middlewares.network_retry_middleware import NetworkRetryRequestMiddleware

    bot_with_session = MagicMock(spec=Bot)
    bot_with_session.session = MagicMock()
    bot_with_session.session.middleware = MagicMock()
    assert attach_custom_emoji_middleware(bot_with_session) is bot_with_session
    assert bot_with_session.session.middleware.call_count == 2
    assert isinstance(bot_with_session.session.middleware.call_args_list[0][0][0], CustomEmojiRequestMiddleware)
    assert isinstance(bot_with_session.session.middleware.call_args_list[1][0][0], NetworkRetryRequestMiddleware)

    bot_without_session = MagicMock(spec=Bot)
    bot_without_session.session = None
    assert attach_custom_emoji_middleware(bot_without_session) is bot_without_session
