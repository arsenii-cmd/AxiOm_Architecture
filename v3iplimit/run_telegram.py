"""Run the telegram bot."""

import asyncio

from telegram_bot.main import TELEGRAM_ENABLED, application


async def run_telegram_bot():
    """Run the telegram bot. No-op when Telegram is disabled (no BOT_TOKEN)."""
    if not TELEGRAM_ENABLED:
        return
    while True:
        try:
            async with application:
                await application.start()
                await application.updater.start_polling()
                while True:
                    await asyncio.sleep(40)
        except Exception:  # pylint: disable=broad-except
            await asyncio.sleep(10)
            continue
