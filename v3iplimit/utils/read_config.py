"""
Read config file and return data.
"""
# pylint: disable=global-statement

import json
import os
import sys
import time

CONFIG_DATA = None
LAST_READ_TIME = 0


async def read_config(check_required_elements=None) -> dict:
    """
    Read and return data from config.json.
    Uses a simple file-mtime cache — re-reads only when the file changes.
    """
    global CONFIG_DATA
    global LAST_READ_TIME
    config_file = "config.json"

    if not os.path.exists(config_file):
        print("config.json not found.")
        sys.exit()

    file_mod_time = os.path.getmtime(config_file)
    if CONFIG_DATA is None or file_mod_time > LAST_READ_TIME:
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                CONFIG_DATA = json.load(f)
        except json.JSONDecodeError as error:
            print("Error decoding config.json:", error)
            sys.exit()
        # BOT_TOKEN / ADMINS are optional: the Telegram monitoring bot is disabled
        # when BOT_TOKEN is absent/empty (headless limiter mode).
        CONFIG_DATA.setdefault("BOT_TOKEN", "")
        CONFIG_DATA.setdefault("ADMINS", [])
        LAST_READ_TIME = time.time()

    if check_required_elements:
        required = [
            "PANEL_DOMAIN",
            "PANEL_USERNAME",
            "PANEL_PASSWORD",
            "CHECK_INTERVAL",
            "TIME_TO_ACTIVE_USERS",
            "IP_LOCATION",
            "GENERAL_LIMIT",
        ]
        for element in required:
            if element not in CONFIG_DATA:
                raise ValueError(
                    f"Missing required config field: '{element}'"
                )

    return CONFIG_DATA
