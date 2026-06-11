"""
Functions for interacting with the Marzneshin panel API.

Adapted from the original Marzban client:
  • token   : POST /api/admins/token        (OAuth2 password form → access_token)
  • disable : POST /api/users/{name}/disable
  • enable  : POST /api/users/{name}/enable

Node/WebSocket-log handling is gone — logs are read locally from the marznode
Xray access log (see utils/get_logs.py), so there is no get_nodes here.
"""

import asyncio
import random
import sys

try:
    import httpx
except ImportError:
    print("Module 'httpx' is not installed. Run: pip install httpx")
    sys.exit()

from telegram_bot.send_message import send_logs
from utils.handel_dis_users import DISABLED_USERS, DisabledUsers
from utils.logs import logger
from utils.read_config import read_config
from utils.types import PanelType, UserType


async def get_token(panel_data: PanelType) -> PanelType:
    """Get an access token from the Marzneshin panel. Retries up to 20 times."""
    payload = {
        "username": panel_data.panel_username,
        "password": panel_data.panel_password,
    }
    for attempt in range(20):
        for scheme in ["https", "http"]:
            url = f"{scheme}://{panel_data.panel_domain}/api/admins/token"
            try:
                async with httpx.AsyncClient(verify=False) as client:
                    response = await client.post(url, data=payload, timeout=10)
                    response.raise_for_status()
                panel_data.panel_token = response.json()["access_token"]
                return panel_data
            except httpx.HTTPStatusError:
                msg = f"[{response.status_code}] {response.text}"
                await send_logs(msg)
                logger.error(msg)
            except Exception as error:  # pylint: disable=broad-except
                msg = f"Unexpected error getting token: {error}"
                await send_logs(msg)
                logger.error(msg)
        await asyncio.sleep(random.randint(2, 5) * (attempt + 1))
    msg = "Failed to get token after 20 attempts."
    await send_logs(msg)
    logger.error(msg)
    raise ValueError(msg)


async def disable_user(panel_data: PanelType, username: UserType) -> None:
    """Disable a user on the Marzneshin panel and record them in DisabledUsers."""
    for attempt in range(20):
        panel_data = await get_token(panel_data)
        headers = {"Authorization": f"Bearer {panel_data.panel_token}"}
        for scheme in ["https", "http"]:
            url = f"{scheme}://{panel_data.panel_domain}/api/users/{username.name}/disable"
            try:
                async with httpx.AsyncClient(verify=False) as client:
                    response = await client.post(url, headers=headers, timeout=10)
                    response.raise_for_status()
                msg = f"⛔ Disabled user: {username.name}"
                await send_logs(msg)
                logger.info(msg)
                await DisabledUsers().add_user(username.name)
                return
            except httpx.HTTPStatusError:
                # 409 = already disabled — treat as success and record it.
                if response.status_code == 409:
                    await DisabledUsers().add_user(username.name)
                    return
                msg = f"[{response.status_code}] {response.text}"
                await send_logs(msg)
                logger.error(msg)
            except Exception as error:  # pylint: disable=broad-except
                msg = f"Unexpected error disabling {username.name}: {error}"
                await send_logs(msg)
                logger.error(msg)
        await asyncio.sleep(random.randint(2, 5) * (attempt + 1))
    raise ValueError(f"Failed to disable user {username.name} after 20 attempts.")


async def enable_selected_users(panel_data: PanelType, users: set[str]) -> None:
    """Re-enable a specific set of users on the Marzneshin panel."""
    for username in users:
        for attempt in range(5):
            panel_data = await get_token(panel_data)
            headers = {"Authorization": f"Bearer {panel_data.panel_token}"}
            done = False
            for scheme in ["https", "http"]:
                url = f"{scheme}://{panel_data.panel_domain}/api/users/{username}/enable"
                try:
                    async with httpx.AsyncClient(verify=False) as client:
                        response = await client.post(url, headers=headers, timeout=10)
                        response.raise_for_status()
                    msg = f"✅ Re-enabled user: {username}"
                    await send_logs(msg)
                    logger.info(msg)
                    done = True
                    break  # success — move to next user
                except httpx.HTTPStatusError:
                    if response.status_code == 409:  # already enabled
                        done = True
                        break
                    msg = f"[{response.status_code}] {response.text}"
                    await send_logs(msg)
                    logger.error(msg)
                except Exception as error:  # pylint: disable=broad-except
                    msg = f"Unexpected error enabling {username}: {error}"
                    await send_logs(msg)
                    logger.error(msg)
            if done:
                break
            await asyncio.sleep(random.randint(2, 5) * (attempt + 1))


async def enable_dis_user(panel_data: PanelType) -> None:
    """Background task: re-enable disabled users every TIME_TO_ACTIVE_USERS seconds."""
    dis_obj = DisabledUsers()
    while True:
        data = await read_config()
        await asyncio.sleep(int(data["TIME_TO_ACTIVE_USERS"]))
        if DISABLED_USERS:
            await enable_selected_users(panel_data, set(DISABLED_USERS))
            await dis_obj.read_and_clear_users()
