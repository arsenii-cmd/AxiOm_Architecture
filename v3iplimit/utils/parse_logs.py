"""
This module contains functions to parse and validate logs.
"""

import ipaddress
import random
import re
import sys
import time
from collections import OrderedDict

from utils.check_usage import ACTIVE_USERS
from utils.read_config import read_config
from utils.types import UserType

try:
    import httpx
except ImportError:
    print("Module 'httpx' is not installed use: 'pip install httpx' to install it")
    sys.exit()

INVALID_EMAILS = [
    "API]",
    "Found",
    "(normal)",
    "timeout",
    "EOF",
    "address",
    "INFO",
    "request",
]
INVALID_IPS = {
    "1.1.1.1",
    "8.8.8.8",
}
VALID_IPS = []
CACHE = {}

API_ENDPOINTS = {
    "http://ip-api.com/json/": "countryCode",
    "https://ipinfo.io/": "country",
    "https://api.iplocation.net/?ip=": "country_code2",
    "https://ipapi.co/": None,
}


async def remove_id_from_username(username: str) -> str:
    """
    Remove the ID from the start of the username.
    Args:
        username (str): The username string from which to remove the ID.

    Returns:
        str: The username with the ID removed.
    """
    return re.sub(r"^\d+\.", "", username)


async def check_ip(ip_address: str) -> None | str:
    """
    Check the geographical location of an IP address.

    Get the location of the IP address.
    The result is cached to avoid unnecessary requests for the same IP address.

    Args:
        ip_address (str): The IP address to check.

    Returns:
        str: The country code of the IP address location, or None
    """
    if ip_address in CACHE:
        return CACHE[ip_address]
    endpoint, key = random.choice(list(API_ENDPOINTS.items()))
    url = endpoint + ip_address
    if "ipapi.co" in endpoint:
        url += "/country"
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(url, timeout=2)
        info = resp.json()
        country = info.get(key) if key else resp.text
        if country:
            CACHE[ip_address] = country
        return country
    except Exception:  # pylint: disable=broad-except
        return None


async def is_valid_ip(ip: str) -> bool:
    """
    Check if a string is a valid IP address.

    This function uses the ipaddress module to try to create an IP address object from the string.

    Args:
        ip (str): The string to check.

    Returns:
        bool: True if the string is a valid IP address, False otherwise.
    """
    try:
        ip_obj = ipaddress.ip_address(ip)
        return not ip_obj.is_private
    except ValueError:
        return False


IP_V6_REGEX = re.compile(r"\[([0-9a-fA-F:]+)\]:\d+\s+accepted")
# Anchored to "from IP:port" to capture source IP only
IP_V4_REGEX = re.compile(r"from\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):\d+")
EMAIL_REGEX = re.compile(r"email:\s*([A-Za-z0-9._%+-]+)")

# ── sing-box (Hysteria2) log format ──────────────────────────────────────────
# sing-box logs a connection across two lines sharing a connection id:
#   ... INFO [3708968451 0ms] inbound/hysteria2[HY2]: inbound connection from 1.2.3.4:5
#   ... INFO [3708968451 0ms] inbound/hysteria2[HY2]: [3.user] inbound connection to host
# so the source IP and the user must be correlated by the connection id.
SB_CONNID_REGEX = re.compile(r"INFO\s+\[(\d+)\s")
SB_FROM_V4_REGEX = re.compile(r"inbound connection from (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):\d+")
SB_FROM_V6_REGEX = re.compile(r"inbound connection from \[([0-9a-fA-F:]+)\]:\d+")
SB_USER_REGEX = re.compile(r"\]:\s*\[([^\]]+)\]\s*inbound connection to")
_SB_CONN_IP: "OrderedDict[str, str]" = OrderedDict()
_SB_CONN_IP_MAX = 4096


async def _ip_allowed(ip: str, data: dict) -> bool:
    """Apply the same validity/geo filtering used for Xray logs to a source IP."""
    if ip in VALID_IPS:
        return True
    if not await is_valid_ip(ip) or ip in INVALID_IPS:
        return False
    if data.get("IP_LOCATION", "None") != "None":
        country = await check_ip(ip)
        if country and country == data["IP_LOCATION"]:
            VALID_IPS.append(ip)
        elif country and country != data["IP_LOCATION"]:
            INVALID_IPS.add(ip)
            return False
    return True


def _record_user_ip(email: str, ip: str) -> None:
    """Add (email, ip) to ACTIVE_USERS with the current monotonic timestamp."""
    now = time.monotonic()
    user = ACTIVE_USERS.get(email)
    if user:
        user.ips[ip] = now
    else:
        ACTIVE_USERS.setdefault(email, UserType(name=email, ips={ip: now}))


async def parse_logs(log: str) -> dict[str, UserType] | dict:  # pylint: disable=too-many-branches
    """
    Asynchronously parse logs to extract and validate IP addresses and emails.

    Args:
        log (str): The log to parse.

    Returns:
        list[UserType]
    """
    data = await read_config()
    if data.get("INVALID_IPS"):
        INVALID_IPS.update(data.get("INVALID_IPS"))
    lines = log.splitlines()
    for line in lines:
        if "accepted" not in line:
            continue
        if "BLOCK]" in line:
            continue
        ip_v6_match = IP_V6_REGEX.search(line)
        ip_v4_match = IP_V4_REGEX.search(line)
        email_match = EMAIL_REGEX.search(line)
        if ip_v6_match:
            ip = ip_v6_match.group(1)
        elif ip_v4_match:
            ip = ip_v4_match.group(1)
        else:
            continue
        if not await _ip_allowed(ip, data):
            continue
        if not email_match:
            continue
        email = await remove_id_from_username(email_match.group(1))
        if email in INVALID_EMAILS:
            continue
        _record_user_ip(email, ip)

    return ACTIVE_USERS


async def parse_singbox_logs(log: str) -> dict[str, UserType] | dict:
    """
    Parse sing-box (Hysteria2) connection logs. The source IP and the user appear
    on two separate lines sharing a connection id, so they are correlated via
    _SB_CONN_IP before recording (user, ip) into ACTIVE_USERS.
    """
    data = await read_config()
    if data.get("INVALID_IPS"):
        INVALID_IPS.update(data.get("INVALID_IPS"))
    for line in log.splitlines():
        connid_match = SB_CONNID_REGEX.search(line)
        if not connid_match:
            continue
        connid = connid_match.group(1)

        if "inbound connection from " in line:
            from_match = SB_FROM_V4_REGEX.search(line) or SB_FROM_V6_REGEX.search(line)
            if from_match:
                _SB_CONN_IP[connid] = from_match.group(1)
                _SB_CONN_IP.move_to_end(connid)
                while len(_SB_CONN_IP) > _SB_CONN_IP_MAX:
                    _SB_CONN_IP.popitem(last=False)
            continue

        user_match = SB_USER_REGEX.search(line)
        if not user_match:
            continue
        ip = _SB_CONN_IP.get(connid)
        if not ip:
            continue
        if not await _ip_allowed(ip, data):
            continue
        email = await remove_id_from_username(user_match.group(1))
        if email in INVALID_EMAILS:
            continue
        _record_user_ip(email, ip)

    return ACTIVE_USERS
