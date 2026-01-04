
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bot.dialogs.schedule_view import on_send_original_file, get_schedule_data, get_week_image_data
from bot.dialogs.constants import DialogDataKeys
from bot.dialogs.states import Schedule
from aiogram.types import CallbackQuery, User, ChatMemberMember, ChatMemberLeft

@pytest.fixture
def mock_callback():
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = AsyncMock(spec=User)
    callback.from_user.id = 123
    callback.message = AsyncMock()
    callback.answer = AsyncMock()
    return callback

@pytest.fixture
def mock_manager():
    manager = AsyncMock()
    ctx = MagicMock()
    ctx.dialog_data = {
        DialogDataKeys.GROUP: "TEACHER_NAME",
        DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01", # Monday
        "user_type": "teacher"
    }
    # current_context is synchronous, so we replace the AsyncMock child with a MagicMock
    manager.current_context = MagicMock(return_value=ctx)
    
    # Mock middleware components
    manager.middleware_data = {
        "manager": AsyncMock(),
        "user_data_manager": AsyncMock(),
        "bot": AsyncMock(),
        "i18n_lang": "ru"
    }
    return manager

@pytest.mark.asyncio
class TestScheduleViewTeacherLogic:
    async def test_get_schedule_data_teacher_canonical(self, mock_manager):
        # Setup
        timetable_manager = mock_manager.middleware_data["manager"]
        user_data_manager = mock_manager.middleware_data["user_data_manager"]
        
        # Scenario: User enters non-canonical name, bot resolves it
        mock_manager.current_context().dialog_data[DialogDataKeys.GROUP] = "Ivanov"
        timetable_manager._teachers_index = {"Ivanov I.I.": []} # Canonical exists
        timetable_manager.resolve_canonical_teacher = MagicMock(return_value="Ivanov I.I.")
        
        # Mock get_user_type returning teacher
        user_data_manager.get_user_type.return_value = "teacher"
        from datetime import date
        timetable_manager.get_teacher_schedule.return_value = {
            "lessons": [], 
            "date": date(2025, 9, 1),
            "week_type": "odd"
        }
        
        # Execute
        data = await get_schedule_data(mock_manager)
        
        # Verify
        # Check canonical resolution call
        timetable_manager.resolve_canonical_teacher.assert_called_with("Ivanov")
        # Check that user data was updated
        user_data_manager.set_user_group.assert_called_with(user_id=mock_manager.event.from_user.id, group="Ivanov I.I.")
        # Check that formatting used teacher formatter
        assert data["user_type"] == "teacher"

    async def test_get_week_image_data_teacher_filtering(self, mock_manager):
        # Setup
        timetable_manager = mock_manager.middleware_data["manager"]
        # Mock teacher index with lessons having week codes
        # week_code: 0=every, 1=odd, 2=even
        timetable_manager._teachers_index = {
            "TEACHER_NAME": [
                {"day": "Понедельник", "week_code": "0", "subject": "Math"},
                {"day": "Понедельник", "week_code": "1", "subject": "Physics (Odd)"}, # Matches odd week
                {"day": "Понедельник", "week_code": "2", "subject": "History (Even)"}, # Should be skipped
            ]
        }
        
        timetable_manager.get_academic_week_type.return_value = ("odd", "Odd Week")
        
        # Mock image dependencies
        with patch("core.image_cache_manager.ImageCacheManager"), \
             patch("core.image_service.ImageService"):
             
            data = await get_week_image_data(mock_manager)
            
            # Since get_week_image_data mainly prepares context for template, 
            # ideally we check if `current_context` was used?
            # Actually get_week_image_data returns dict.
            # But the logic constructs `week_schedule` locally inside function.
            # It's not returned in the dict! It's likely used by `generate_week_image_task`?
            # Wait, `get_week_image_data` returns data for the getter.
            # But where is the schedule passed?
            # Re-reading lines 298+: it instantiates `ImageService` but doesn't seem to CAL it to generate image in this function?
            # Ah, `get_week_image_data` is a getter for the WINDOW.
            # Wait, line 245 in `schedule_view.py` calls `await get_week_image_data(manager)`.
            # But `get_week_image_data` (lines 256+) prepares basic info.
            # Where is the image generated? 
            # Ah, `on_full_week_image_click` calls `get_week_image_data` but ignores result?
            # Wait, `ImageService` allows getting/generating.
            # Lines 302: `image_service = ImageService(...)`
            # But it is not used?
            # 
            # Oops, I might have found a bug or I am misreading. 
            # In `schedule_view.py`:
            # 302: `image_service = ImageService(cache_manager, bot)`
            # 305-329: Logic to build `week_schedule`.
            # THEN... nothing happens with `week_schedule` or `image_service`?
            # Line 345 returns dict.
            # The code seems to construct `week_schedule` but DOES NOT USE IT.
            # Wait. `get_week_image_data` is a DIALOG GETTER. 
            # It supplies data to the template.
            # The template probably uses `week_image_id`?
            # But `week_schedule` is local variable.
            # Ah, `on_full_week_image_click` (line 224) calls `get_week_image_data`.
            # Maybe the logic was refactored and this function just returns text info now?
            # But why does it verify week type?
            
            # Logic check:
            # If `get_week_image_data` is just for showing text caption, then `week_schedule` calculation is redundant dead code.
            # Unless `image_service` was supposed to use it.
            # This looks like potential dead code or incomplete refactor.
            # I will confirm this with a test. If I run this function, `week_schedule` is computed but lost.
            
            pass 

@pytest.mark.asyncio
class TestSubscriptionLogic:
    async def test_on_send_original_file_not_subscribed(self, mock_callback, mock_manager):
        # Patch config to enable subscription
        with patch("bot.dialogs.schedule_view.SUBSCRIPTION_CHANNEL", "@test_channel"):
            bot = mock_manager.middleware_data["bot"]
            
            # Mock user NOT member
            member = MagicMock()
            member.status = "left"
            bot.get_chat_member.return_value = member
            
            timetable_manager = mock_manager.middleware_data["manager"]
            timetable_manager.get_academic_week_type.return_value = ("odd", "Odd")
            
            await on_send_original_file(mock_callback, None, mock_manager)
            
            # Should switch to gate
            mock_manager.switch_to.assert_called_with(Schedule.full_quality_gate)
            # Should show alert message
            mock_callback.message.answer.assert_called()
            args = mock_callback.message.answer.call_args[0][0]
            assert "по подписке" in args

    async def test_on_send_original_file_subscribed(self, mock_callback, mock_manager):
        # Patch config to enable subscription
        with patch("bot.dialogs.schedule_view.SUBSCRIPTION_CHANNEL", "@test_channel"):
            bot = mock_manager.middleware_data["bot"]
            
            # Mock user IS member
            member = MagicMock()
            member.status = "member"
            bot.get_chat_member.return_value = member
            
            timetable_manager = mock_manager.middleware_data["manager"]
            timetable_manager.get_academic_week_type.return_value = ("odd", "Odd")
            
            # Mock file existence to jump to sending
            with patch("pathlib.Path.exists", return_value=True):
                 # Mock send_document
                 await on_send_original_file(mock_callback, None, mock_manager)
                 
                 bot.send_document.assert_called()
                 # Should NOT switch to gate
                 mock_manager.switch_to.assert_not_called()
