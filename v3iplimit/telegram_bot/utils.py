"""
Utility functions for reading/writing config.json and managing bot admins.
"""

import json
import os
import sys

from utils.types import PanelType

try:
    import httpx
except ImportError:
    print("Module 'httpx' is not installed. Run: pip install httpx")
    sys.exit()


async def get_token(panel_data: PanelType) -> PanelType | ValueError:
    """
    Obtain a Marzban API token.
    Duplicate of panel_api.get_token to avoid circular imports.
    """
    # pylint: disable=duplicate-code
    payload = {
        "username": f"{panel_data.panel_username}",
        "password": f"{panel_data.panel_password}",
    }
    for scheme in ["https", "http"]:
        url = f"{scheme}://{panel_data.panel_domain}/api/admin/token"
        try:
            async with httpx.AsyncClient(verify=False) as client:
                response = await client.post(url, data=payload, timeout=5)
                response.raise_for_status()
            json_obj = response.json()
            panel_data.panel_token = json_obj["access_token"]
            return panel_data
        except Exception:  # pylint: disable=broad-except
            continue
    raise ValueError(
        "Failed to get token. Make sure the panel is running "
        "and the username and password are correct."
    )


async def read_json_file() -> dict:
    """Read and return the contents of config.json."""
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


async def write_json_file(data: dict) -> None:
    """Write data to config.json."""
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


async def add_admin_to_config(new_admin_id: int) -> int | None:
    """Add a new admin ID to config.json. Returns the ID on success, None if already exists."""
    if os.path.exists("config.json"):
        data = await read_json_file()
        admins = data.get("ADMINS", [])
        if int(new_admin_id) not in admins:
            admins.append(int(new_admin_id))
            data["ADMINS"] = admins
            await write_json_file(data)
            return new_admin_id
    else:
        await write_json_file({"ADMINS": [new_admin_id]})
        return new_admin_id
    return None


async def check_admin() -> list[int] | None:
    """Return the list of admin chat IDs from config.json."""
    if os.path.exists("config.json"):
        data = await read_json_file()
        return data.get("ADMINS", [])
    return []


async def remove_admin_from_config(admin_id: int) -> bool:
    """Remove an admin from config.json. Returns True if removed, False if not found."""
    data = await read_json_file()
    admins = data.get("ADMINS", [])
    if admin_id in admins:
        admins.remove(admin_id)
        data["ADMINS"] = admins
        await write_json_file(data)
        return True
    return False


async def add_base_information(domain: str, password: str, username: str) -> None:
    """Validate panel credentials and save them to config.json."""
    await get_token(
        PanelType(panel_domain=domain, panel_password=password, panel_username=username)
    )
    data = await read_json_file() if os.path.exists("config.json") else {}
    data.update(
        {
            "PANEL_DOMAIN": domain,
            "PANEL_USERNAME": username,
            "PANEL_PASSWORD": password,
        }
    )
    await write_json_file(data)


async def write_country_code_json(country_code: str) -> None:
    """Save the IP_LOCATION country code to config.json."""
    data = await read_json_file()
    data["IP_LOCATION"] = country_code
    await write_json_file(data)
