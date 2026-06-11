"""
Tracks active users and enforces IP connection limits.

ACTIVE_USERS is populated in real-time by the Xray access-log tailer
(utils/get_logs.py → utils/parse_logs.py). Each IP carries a last-seen timestamp
(time.monotonic), so instead of hard-clearing every cycle we keep an IP "active"
for a sliding ACTIVE_MINS window. This avoids the sawtooth where a connected but
momentarily idle user drops to 0 right after a clear.
"""

import asyncio
import time

from telegram_bot.send_message import send_logs
from utils.logs import logger
from utils.panel_api import disable_user
from utils.read_config import read_config
from utils.types import PanelType, UserType

ACTIVE_USERS: dict[str, UserType] | dict = {}

# Snapshot of the last completed enforcement cycle: {username: [ip, ...]}
# Kept as a fallback for the REST API right after pruning.
LAST_CHECK_RESULT: dict[str, list[str]] = {}

DEFAULT_ACTIVE_MINS = 5


async def active_window_seconds() -> float:
    """Sliding window (seconds) during which a seen IP still counts as active."""
    data = await read_config()
    try:
        return float(data.get("ACTIVE_MINS", DEFAULT_ACTIVE_MINS)) * 60
    except (TypeError, ValueError):
        return DEFAULT_ACTIVE_MINS * 60


def prune_stale(now: float, window: float) -> None:
    """Drop IPs not seen within `window` seconds; drop users left with no IPs."""
    for email in list(ACTIVE_USERS.keys()):
        user = ACTIVE_USERS[email]
        user.ips = {ip: ts for ip, ts in user.ips.items() if now - ts <= window}
        if not user.ips:
            del ACTIVE_USERS[email]


def active_ips(email: str, now: float, window: float) -> list[str]:
    """Return the IPs of `email` seen within the sliding window."""
    user = ACTIVE_USERS.get(email)
    if user is None:
        return []
    return [ip for ip, ts in user.ips.items() if now - ts <= window]


async def check_ip_used() -> dict:
    """
    Return {username: [active ips]} for every user with at least one IP inside the
    sliding window, and send a status report to Telegram admins.
    """
    window = await active_window_seconds()
    now = time.monotonic()
    prune_stale(now, window)

    all_users_log = {
        email: list(user.ips.keys()) for email, user in ACTIVE_USERS.items() if user.ips
    }

    total_ips = sum(len(ips) for ips in all_users_log.values())
    all_users_log = dict(
        sorted(all_users_log.items(), key=lambda x: len(x[1]), reverse=True)
    )

    messages = [
        f"👤 <code>{email}</code> — <b>{len(ips)}</b> IP(s):\n  • " + "\n  • ".join(ips)
        for email, ips in all_users_log.items()
        if ips
    ]
    logger.info("Total active IPs: %s", total_ips)

    if messages:
        messages.append(f"─────────\n🌐 Total active IPs: <b>{total_ips}</b>")
    else:
        messages.append("📊 No active connections right now.")

    for chunk in ["\n\n".join(messages[i: i + 30]) for i in range(0, len(messages), 30)]:
        await send_logs(chunk)

    return all_users_log


async def check_users_usage(panel_data: PanelType) -> None:
    """
    Check all active users (within the sliding window) against their IP limit and
    disable any user who exceeds it. Stale IPs are pruned, but recently-seen IPs
    are retained — so the counter does not reset to zero between cycles.
    """
    config_data = await read_config()
    all_users_log = await check_ip_used()

    except_users = config_data.get("EXCEPT_USERS", [])
    special_limit = config_data.get("SPECIAL_LIMIT", {})
    general_limit = int(config_data["GENERAL_LIMIT"])

    for username, ips in all_users_log.items():
        if username in except_users:
            continue
        user_limit = int(special_limit.get(username, general_limit))
        if user_limit > 0 and len(set(ips)) > user_limit:
            msg = (
                f"⚠️ <b>Limit exceeded:</b> <code>{username}</code> "
                f"has {len(set(ips))} IPs (limit: {user_limit})\n"
                f"IPs: {', '.join(f'<code>{ip}</code>' for ip in set(ips))}"
            )
            logger.warning(msg)
            await send_logs(msg)
            try:
                await disable_user(panel_data, UserType(name=username))
            except ValueError as error:
                logger.error(error)

    # Keep a snapshot as a REST-API fallback (windowed view persists in ACTIVE_USERS).
    LAST_CHECK_RESULT.clear()
    LAST_CHECK_RESULT.update({u: list(ips) for u, ips in all_users_log.items() if ips})


async def run_check_users_usage(panel_data: PanelType) -> None:
    """Enforcement loop: check and enforce limits every CHECK_INTERVAL seconds."""
    while True:
        await check_users_usage(panel_data)
        data = await read_config()
        await asyncio.sleep(int(data["CHECK_INTERVAL"]))
