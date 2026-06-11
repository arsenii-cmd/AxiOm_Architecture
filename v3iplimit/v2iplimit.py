"""
V3IpLimit (Marzneshin edition) — main entry point.

Single service that handles:
  • XRay log monitoring via the local marznode access-log file
  • IP limit enforcement (disable / re-enable users on the Marzneshin panel)
  • Optional Telegram monitoring bot (disabled when BOT_TOKEN is empty)
  • REST API on port 7070 (device counter + set_limit, served via Caddy /devices)
"""

import argparse
import asyncio
import threading
import time

try:
    import uvicorn
except ImportError:
    print("Module 'uvicorn' is not installed. Run: pip install fastapi uvicorn")
    uvicorn = None  # type: ignore

from api.rest_api import app as rest_app
from run_telegram import run_telegram_bot
from telegram_bot.send_message import send_logs
from utils.check_usage import run_check_users_usage
from utils.get_logs import tail_singbox_log, tail_xray_access_log
from utils.handel_dis_users import DisabledUsers
from utils.logs import logger
from utils.panel_api import enable_dis_user, enable_selected_users
from utils.read_config import read_config
from utils.types import PanelType

VERSION = "1.0.0-marzneshin"
REST_API_PORT = 7070

dis_obj = DisabledUsers()


parser = argparse.ArgumentParser(description="V3IpLimit (Marzneshin)")
parser.add_argument("--version", action="version", version=VERSION)
args = parser.parse_args()


async def main() -> None:
    """Boot all subsystems."""
    asyncio.create_task(run_telegram_bot())
    await asyncio.sleep(1)

    # Wait until config has all required fields
    while True:
        try:
            config_file = await read_config(check_required_elements=True)
            break
        except ValueError as error:
            logger.error(error)
            await send_logs("<code>" + str(error) + "</code>")
            await asyncio.sleep(60)

    panel_data = PanelType(
        config_file["PANEL_USERNAME"],
        config_file["PANEL_PASSWORD"],
        config_file["PANEL_DOMAIN"],
    )

    # Re-enable any users that were disabled before the last restart
    previously_disabled = await dis_obj.read_and_clear_users()
    if previously_disabled:
        await enable_selected_users(panel_data, previously_disabled)

    # REST API (device counter + set_limit) — replaces devices_api.py
    if uvicorn is not None:
        threading.Thread(
            target=lambda: uvicorn.run(
                rest_app, host="127.0.0.1", port=REST_API_PORT, log_level="warning"
            ),
            daemon=True,
        ).start()
        logger.info("REST API started on port %s", REST_API_PORT)

    async with asyncio.TaskGroup() as tg:
        # Local access-log tailers (replace panel/node WebSocket monitors)
        tg.create_task(tail_xray_access_log(), name="xray_log_tail")
        tg.create_task(tail_singbox_log(), name="singbox_log_tail")
        # Re-enable disabled users on schedule
        tg.create_task(enable_dis_user(panel_data), name="enable_dis_user")
        # Enforcement loop — check limits, disable violators
        await run_check_users_usage(panel_data)


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except Exception as er:  # pylint: disable=broad-except
            logger.error(er)
            time.sleep(10)
