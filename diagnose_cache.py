#!/usr/bin/env python3
"""
Diagnostic script to identify cache issues.
"""

import asyncio
import logging
from pathlib import Path
from core.image_cache_manager import ImageCacheManager
from core.image_service import ImageService
from redis.asyncio import Redis

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

async def diagnose_cache_issue():
    """Diagnose the cache issue."""
    
    # Test data
    group = "И121Б"
    week_key = "even"
    cache_key = f"{group}_{week_key}"
    
    print(f"🔍 Diagnosing cache issue for {cache_key}")
    
    # Setup Redis (you'll need to adjust connection details)
    try:
        redis_client = Redis.from_url("redis://localhost:6379", password="your_password")
        cache_manager = ImageCacheManager(redis_client, cache_ttl_hours=24)
        mock_bot = MockBot()
        image_service = ImageService(cache_manager, mock_bot)
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return
    
    # Step 1: Check cache status
    print(f"\n1️⃣ Checking cache status...")
    is_cached = await cache_manager.is_cached(cache_key)
    print(f"   Is cached: {is_cached}")
    
    # Step 2: Detailed diagnosis
    print(f"\n2️⃣ Detailed diagnosis...")
    diagnosis = await image_service.diagnose_cache(cache_key)
    
    print(f"   Overall status: {diagnosis['overall_status']}")
    print(f"   Redis: exists={diagnosis['redis']['exists']}, size={diagnosis['redis']['size_bytes']} bytes")
    print(f"   File: exists={diagnosis['file']['exists']}, size={diagnosis['file']['size_bytes']} bytes")
    print(f"   Metadata: exists={diagnosis['metadata']['exists']}")
    
    if diagnosis['file']['exists']:
        print(f"   File path: {diagnosis['file']['path']}")
        print(f"   File modified: {diagnosis['file']['modified']}")
    
    if diagnosis['metadata']['data']:
        print(f"   Metadata: {diagnosis['metadata']['data']}")
    
    # Step 3: Check file system directly
    print(f"\n3️⃣ File system check...")
    file_path = Path(diagnosis['file']['path'])
    if file_path.exists():
        print(f"   File exists: ✅")
        print(f"   File size: {file_path.stat().st_size} bytes")
        print(f"   File readable: {file_path.is_file()}")
        print(f"   File permissions: {oct(file_path.stat().st_mode)[-3:]}")
        
        # Try to read the file
        try:
            with open(file_path, 'rb') as f:
                data = f.read(1024)  # Read first 1KB
                print(f"   File readable: ✅ ({len(data)} bytes read)")
        except Exception as e:
            print(f"   File readable: ❌ ({e})")
    else:
        print(f"   File exists: ❌")
    
    # Step 4: Test cache operations
    print(f"\n4️⃣ Testing cache operations...")
    
    # Test getting cached image
    try:
        cached_image = await cache_manager.get_cached_image(cache_key)
        if cached_image:
            print(f"   Get cached image: ✅ ({len(cached_image)} bytes)")
        else:
            print(f"   Get cached image: ❌ (None)")
    except Exception as e:
        print(f"   Get cached image: ❌ ({e})")
    
    # Test cache info
    try:
        cache_info = await cache_manager.get_cache_info(cache_key)
        if cache_info:
            print(f"   Get cache info: ✅")
            print(f"   Cache info: {cache_info}")
        else:
            print(f"   Get cache info: ❌ (None)")
    except Exception as e:
        print(f"   Get cache info: ❌ ({e})")
    
    # Step 5: Test file compression
    print(f"\n5️⃣ Testing file compression...")
    if file_path.exists():
        try:
            from bot.utils.image_compression import get_telegram_safe_image_path
            safe_path = get_telegram_safe_image_path(str(file_path))
            safe_path_obj = Path(safe_path)
            
            print(f"   Original file: {file_path}")
            print(f"   Safe file: {safe_path}")
            print(f"   Safe file exists: {safe_path_obj.exists()}")
            
            if safe_path_obj.exists():
                print(f"   Safe file size: {safe_path_obj.stat().st_size} bytes")
        except Exception as e:
            print(f"   Compression test: ❌ ({e})")
    
    # Step 6: Cache statistics
    print(f"\n6️⃣ Cache statistics...")
    try:
        stats = await image_service.get_cache_stats()
        print(f"   Cache stats: {stats}")
    except Exception as e:
        print(f"   Cache stats: ❌ ({e})")
    
    await redis_client.close()
    print(f"\n✅ Diagnosis completed!")

if __name__ == "__main__":
    asyncio.run(diagnose_cache_issue())
