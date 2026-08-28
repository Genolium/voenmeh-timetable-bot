
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, ANY
from core.image_generator import generate_schedule_image, _prepare_days, shutdown_image_generator

@pytest.fixture
def mock_template_render(mocker):
    # Mock template environment to verify background selection without actual file IO
    env_mock = MagicMock()
    template_mock = MagicMock()
    env_mock.get_template.return_value = template_mock
    mocker.patch("core.image_generator.Environment", return_value=env_mock)
    mocker.patch("core.image_generator.FileSystemLoader")
    # Also patch cache global
    # core.image_generator._template_cache needs to be None for Env to be called?
    # Or we can just patch _template_cache directly
    
    # Better approach: Patch _template_cache in the function scope if we can,
    # or rely on Environment intercept.
    return template_mock

@pytest.fixture
def mock_playwright_context():
    # Setup a working Playwright mock
    pw_mock = MagicMock()
    browser_mock = AsyncMock()
    page_mock = AsyncMock()
    
    pw_instance = AsyncMock()
    pw_instance.chromium.launch.return_value = browser_mock
    pw_mock.return_value.start = AsyncMock(return_value=pw_instance)
    pw_mock.return_value.__aenter__ = AsyncMock(return_value=pw_instance)
    
    browser_mock.new_page.return_value = page_mock
    browser_mock.new_context.return_value = AsyncMock()
    
    # Setup page content height mock to avoid 0 height error
    page_mock.evaluate.return_value = 1000
    page_mock.set_default_timeout = MagicMock()
    page_mock.set_default_navigation_timeout = MagicMock()
    
    return pw_mock, browser_mock, page_mock

@pytest.mark.asyncio
class TestImageGeneratorAdvanced:
    
    async def test_prepare_days_exceptions(self):
        # Test malformed data
        bad_data = {
            "ПОНЕДЕЛЬНИК": [
                {"start_time_raw": "invalid", "subject": "Math"}
            ]
        }
        res = _prepare_days(bad_data)
        # Should not raise, but fallback to empty firstStart
        assert res[0]["firstStart"] == "invalid"
        # Check title formation
        assert "Math" in res[0]["lessons"][0]["title"]

        # Test room parsing edge case
        bad_room = {
            "ПОНЕДЕЛЬНИК": [{"start_time_raw": "09:00", "room": "Кабинет не указан"}]
        }
        res = _prepare_days(bad_room)
        assert res[0]["lessons"][0]["room"] == ""

    async def test_themes_bg_selection(self, mock_playwright_context):
        # Patch async_playwright to enable function execution
        pw_mock, _, _ = mock_playwright_context
        
        with patch("core.image_generator.async_playwright", pw_mock):
             # Force template reload or cache bypass is tricky, 
             # but we can check if render called with specific "bg_image" if we mock template
             with patch("core.image_generator._template_cache") as mock_tmpl:
                 mock_tmpl.render.return_value = "<html></html>"
                 
                 # 1. Light Theme
                 await generate_schedule_image({}, "odd", "G1", "out.png", user_theme="light")
                 # Check bg_image in call args. We can't check exact base64 data easily unless we mock file read
                 # But we can verify _resolve_bg_key logic by seeing if it tried to load "light"
                 # Actually, logic: bg_key = "light", bg_image_data = _bg_images_cache.get("light")
                 
                 # 2. Dark Theme
                 await generate_schedule_image({}, "odd", "G1", "out.png", user_theme="dark")
                 
                 # 3. Classic Theme
                 await generate_schedule_image({}, "odd", "G1", "out.png", user_theme="classic")
                 
                 # Just asserting no compilation error and "render" was called
                 assert mock_tmpl.render.call_count >= 3

    async def test_shutdown_lifecycle(self, mock_playwright_context):
        pw_mock, browser, _ = mock_playwright_context
        with patch("core.image_generator.async_playwright", pw_mock):
            # Run one generation to create the pool
            await generate_schedule_image({}, "odd", "G1", "out.png")
            
            # Now verify state exists
            import asyncio
            loop = asyncio.get_running_loop()
            state = getattr(loop, "__img_pool_state__", None)
            assert state is not None
            assert state.browser == browser
            
            # Shutdown
            await shutdown_image_generator()
            
            # Browser should be closed
            browser.close.assert_called()
            # State should be cleared
            assert getattr(loop, "__img_pool_state__") is None

    async def test_pool_force_restart(self, mock_playwright_context):
        # Test the "Recovery" block when new_context fails (health check) or browser closed
        pw_mock, browser, _ = mock_playwright_context
        
        with patch("core.image_generator.async_playwright", pw_mock):
            # 1. Init pool
            await generate_schedule_image({}, "odd", "G1", "out.png")
            
            # 2. Corrupt the browser to force health check failure
            loop = asyncio.get_running_loop()
            state = getattr(loop, "__img_pool_state__")
            
            # Mock new_context to raise exception -> signals browser dead
            state.browser.new_context.side_effect = Exception("Dead")
            
            # 3. Run again - should trigger restart logic
            # Reset close calls to verify it gets called again
            state.browser.close.reset_mock()
            
            await generate_schedule_image({}, "odd", "G1", "out.png")
            
            # Verify close called (cleanup of dead browser)
            state.browser.close.assert_called()
            # Verify new browser launched (launch called twice total? or once more)
            pw_instance = pw_mock.return_value.start.return_value
            assert pw_instance.chromium.launch.call_count >= 2

    async def test_render_error_handling(self, mock_playwright_context):
        # Simulate rendering error (e.g. content height 0)
        pw_mock, _, page_mock = mock_playwright_context
        page_mock.evaluate.return_value = 0 # Triggers ValueError
        
        with patch("core.image_generator.async_playwright", pw_mock):
             res = await generate_schedule_image({}, "odd", "G1", "out.png")
             assert res is False

    async def test_pool_rotation_by_count(self, mock_playwright_context):
        pw_mock, browser, _ = mock_playwright_context
        
        # Create a second browser mock for the second launch
        browser2 = AsyncMock()
        browser2.new_page.return_value = browser.new_page.return_value # Return same page mock for simplicity
        
        pw_instance = pw_mock.return_value.start.return_value
        pw_instance.chromium.launch.side_effect = [browser, browser2]
        
        with patch("core.image_generator.async_playwright", pw_mock), \
             patch("core.image_generator._POOL_MAX_PAGES", 1): # Max 1 page before restart
             
             # 1. First generation
             await generate_schedule_image({}, "odd", "G1", "out.png")
             
             loop = asyncio.get_running_loop()
             state = getattr(loop, "__img_pool_state__")
             first_browser = state.browser
             assert first_browser is browser
             
             # 2. Second generation (Rotation triggers)
             await generate_schedule_image({}, "odd", "G1", "out.png")
             
             state_new = getattr(loop, "__img_pool_state__")
             assert state_new.browser is browser2
             
             # Verify strict closure
             first_browser.close.assert_called()
