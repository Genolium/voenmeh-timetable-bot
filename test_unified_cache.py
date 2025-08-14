#!/usr/bin/env python3
"""
Test script to verify the unified image caching system.
This script tests the ImageService and ImageCacheManager integration.
"""

import asyncio
import os
import time
from pathlib import Path
from core.image_cache_manager import ImageCacheManager
from core.image_service import ImageService
from redis.asyncio import Redis
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Mock bot for testing
class MockBot:
    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        logger.info(f"MockBot: Sending photo to {chat_id}")
        return True
    
    async def edit_message_media(self, chat_id, message_id, media, reply_markup=None):
        logger.info(f"MockBot: Editing message {message_id} for {chat_id}")
        return True

async def test_unified_caching():
    """Test the unified image caching system."""
    
    # Test data
    group = "TEST_GROUP"
    week_key = "even"
    cache_key = f"{group}_{week_key}"
    
    # Create test schedule data
    schedule_data = {
        "ПОНЕДЕЛЬНИК": [
            {"start_time_raw": "09:00", "subject": "Тест", "type": "лек", "room": "101", "time": "09:00-10:30"}
        ],
        "ВТОРНИК": [
            {"start_time_raw": "10:30", "subject": "Практика", "type": "пр", "room": "202", "time": "10:30-12:00"}
        ]
    }
    
    # Setup Redis (you'll need to adjust connection details)
    try:
        redis_client = Redis.from_url("redis://localhost:6379", password="your_password")
        cache_manager = ImageCacheManager(redis_client, cache_ttl_hours=24)
        mock_bot = MockBot()
        image_service = ImageService(cache_manager, mock_bot)
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return
    
    print(f"🧪 Testing unified caching system for {cache_key}")
    
    # Step 1: Check initial cache state
    print("\n1️⃣ Checking initial cache state...")
    is_cached = await cache_manager.is_cached(cache_key)
    cache_info = await cache_manager.get_cache_info(cache_key)
    print(f"   Is cached: {is_cached}")
    print(f"   Cache info: {cache_info}")
    
    # Step 2: Test cache miss and generation
    print("\n2️⃣ Testing cache miss and generation...")
    success, file_path = await image_service.get_or_generate_week_image(
        group=group,
        week_key=week_key,
        week_name="Чётная",
        week_schedule=schedule_data,
        user_id=12345,  # Mock user ID
        final_caption="Test caption"
    )
    
    print(f"   Generation success: {success}")
    print(f"   File path: {file_path}")
    
    if success and file_path:
        print(f"   File exists: {Path(file_path).exists()}")
        print(f"   File size: {Path(file_path).stat().st_size if Path(file_path).exists() else 0} bytes")
    
    # Step 3: Test cache hit
    print("\n3️⃣ Testing cache hit...")
    success2, file_path2 = await image_service.get_or_generate_week_image(
        group=group,
        week_key=week_key,
        week_name="Чётная",
        week_schedule=schedule_data,
        user_id=12345,
        final_caption="Test caption 2"
    )
    
    print(f"   Cache hit success: {success2}")
    print(f"   Same file path: {file_path == file_path2}")
    
    # Step 4: Test cache info after generation
    print("\n4️⃣ Testing cache info after generation...")
    is_cached_after = await cache_manager.is_cached(cache_key)
    cache_info_after = await cache_manager.get_cache_info(cache_key)
    print(f"   Is cached after: {is_cached_after}")
    print(f"   Cache info after: {cache_info_after}")
    
    # Step 5: Test cache invalidation
    print("\n5️⃣ Testing cache invalidation...")
    invalidated = await image_service.invalidate_cache(cache_key)
    print(f"   Cache invalidated: {invalidated}")
    
    is_cached_after_invalidation = await cache_manager.is_cached(cache_key)
    print(f"   Is cached after invalidation: {is_cached_after_invalidation}")
    
    # Step 6: Test cache stats
    print("\n6️⃣ Testing cache stats...")
    stats = await image_service.get_cache_stats()
    print(f"   Cache stats: {stats}")
    
    # Step 7: Test cleanup
    print("\n7️⃣ Testing cache cleanup...")
    cleaned = await image_service.cleanup_expired_cache()
    print(f"   Cleaned files: {cleaned}")
    
    # Cleanup test files
    print("\n8️⃣ Cleanup test files...")
    try:
        if file_path and Path(file_path).exists():
            Path(file_path).unlink()
            print(f"   ✅ Removed test file: {file_path}")
    except Exception as e:
        print(f"   ❌ Failed to remove test file: {e}")
    
    await redis_client.close()
    print(f"\n✅ Unified cache test completed!")

async def test_concurrent_generation():
    """Test concurrent image generation to ensure locks work correctly."""
    
    print(f"\n🧪 Testing concurrent generation...")
    
    # Setup
    try:
        redis_client = Redis.from_url("redis://localhost:6379", password="your_password")
        cache_manager = ImageCacheManager(redis_client, cache_ttl_hours=24)
        mock_bot = MockBot()
        image_service = ImageService(cache_manager, mock_bot)
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return
    
    # Test data
    group = "CONCURRENT_TEST"
    week_key = "odd"
    cache_key = f"{group}_{week_key}"
    
    schedule_data = {
        "ПОНЕДЕЛЬНИК": [
            {"start_time_raw": "09:00", "subject": "Конкурентный тест", "type": "лек", "room": "101", "time": "09:00-10:30"}
        ]
    }
    
    # Create multiple concurrent tasks
    async def generate_task(task_id: int):
        logger.info(f"Task {task_id}: Starting generation")
        success, file_path = await image_service.get_or_generate_week_image(
            group=group,
            week_key=week_key,
            week_name="Нечётная",
            week_schedule=schedule_data,
            user_id=1000 + task_id,
            final_caption=f"Task {task_id} caption"
        )
        logger.info(f"Task {task_id}: Generation completed - success={success}")
        return success, file_path
    
    # Run 3 concurrent tasks
    tasks = [generate_task(i) for i in range(3)]
    results = await asyncio.gather(*tasks)
    
    print(f"   Concurrent generation results:")
    for i, (success, file_path) in enumerate(results):
        print(f"   Task {i}: success={success}, file={file_path}")
    
    # Check that all tasks got the same file path
    file_paths = [result[1] for result in results if result[1]]
    if file_paths:
        unique_paths = set(file_paths)
        print(f"   Unique file paths: {len(unique_paths)}")
        print(f"   All same file: {len(unique_paths) == 1}")
    
    # Cleanup
    try:
        if file_paths:
            Path(file_paths[0]).unlink()
            print(f"   ✅ Removed concurrent test file")
    except Exception as e:
        print(f"   ❌ Failed to remove concurrent test file: {e}")
    
    await redis_client.close()

if __name__ == "__main__":
    print("🚀 Starting unified caching system tests...")
    
    # Run basic test
    asyncio.run(test_unified_caching())
    
    # Run concurrent test
    asyncio.run(test_concurrent_generation())
    
    print("\n🎉 All tests completed!")
