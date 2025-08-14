from aiogram import Router, F
router = Router()

@router.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group(message):
    if "schedule" in message.text.lower():
        # Parse group and day, send schedule
        pass

# In main.py: dp.include_router(router)
