"""
Read backend access logs locally from marznode's log files.

Marzneshin/marznode does not expose Marzban's WebSocket log endpoints, so the
limiter tails the local log files that the backends are configured to write:
  • Xray     → ACCESS_LOG_PATH   (e.g. /var/lib/marznode/access.log)         VLESS WS/Reality
  • sing-box → SINGBOX_LOG_PATH  (e.g. /var/lib/marznode/singbox-access.log) Hysteria2

Both feed the shared ACTIVE_USERS via their respective parsers. Log rotation /
truncation (copytruncate) is handled by detecting size shrink.
"""

import asyncio
import os

from utils.logs import logger
from utils.parse_logs import parse_logs, parse_singbox_logs
from utils.read_config import read_config

DEFAULT_XRAY_LOG = "/var/lib/marznode/access.log"
DEFAULT_SINGBOX_LOG = "/var/lib/marznode/singbox-access.log"

_POLL_INTERVAL = 1.0  # seconds between reads when no new data


def _split_complete_lines(buffer: str) -> tuple[list[str], str]:
    """Return (complete_lines, trailing_partial) from a text buffer."""
    if "\n" not in buffer:
        return [], buffer
    parts = buffer.split("\n")
    trailing = parts.pop()  # last element is the incomplete line (or "")
    return [p for p in parts if p], trailing


async def _tail(path: str, parser, label: str) -> None:
    """Continuously tail `path`, feeding complete lines to `parser` (a coroutine)."""
    while not os.path.exists(path):
        logger.warning("[%s] log %s not found yet — waiting…", label, path)
        await asyncio.sleep(5)

    logger.info("[%s] tailing %s", label, path)
    pos = os.path.getsize(path)  # start at the end — ignore historical lines
    pending = ""

    while True:
        try:
            size = os.path.getsize(path)
            if size < pos:
                logger.info("[%s] log truncated — seeking to start", label)
                pos = 0
                pending = ""
            if size > pos:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                pending += chunk
                lines, pending = _split_complete_lines(pending)
                if lines:
                    await parser("\n".join(lines))
            else:
                await asyncio.sleep(_POLL_INTERVAL)
        except FileNotFoundError:
            await asyncio.sleep(5)
        except Exception as error:  # pylint: disable=broad-except
            logger.error("[%s] tail error: %s", label, error)
            await asyncio.sleep(_POLL_INTERVAL)


async def tail_xray_access_log() -> None:
    """Tail the Xray access log (VLESS WS/Reality)."""
    config = await read_config()
    path = config.get("ACCESS_LOG_PATH", DEFAULT_XRAY_LOG)
    await _tail(path, parse_logs, "xray")


async def tail_singbox_log() -> None:
    """Tail the sing-box access log (Hysteria2)."""
    config = await read_config()
    path = config.get("SINGBOX_LOG_PATH", DEFAULT_SINGBOX_LOG)
    await _tail(path, parse_singbox_logs, "singbox")
