"""
Manages the set of users disabled by the IP limiter.
Persists disabled users to disk so they survive restarts.
"""

import json
import os

from utils.logs import logger

DISABLED_USERS: set = set()


class DisabledUsers:
    """Tracks users disabled by the IP limiter and persists them across restarts."""

    def __init__(self, filename=".disable_users.json"):
        self.filename = filename
        self.disabled_users = self._load()

    def _load(self) -> set:
        try:
            if os.path.exists(self.filename):
                with open(self.filename, "r", encoding="utf-8") as f:
                    return set(json.load(f).get("disable_user", []))
        except Exception as error:  # pylint: disable=broad-except
            logger.error("Failed to load disabled users: %s", error)
        return set()

    async def _save(self) -> None:
        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump({"disable_user": list(self.disabled_users)}, f)

    async def add_user(self, username: str) -> None:
        DISABLED_USERS.add(username)
        self.disabled_users.add(username)
        await self._save()

    async def read_and_clear_users(self) -> set:
        users = set(self.disabled_users)
        self.disabled_users.clear()
        DISABLED_USERS.clear()
        await self._save()
        return users
