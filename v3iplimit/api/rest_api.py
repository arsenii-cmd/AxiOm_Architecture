"""
REST API for V3IpLimit.

Exposes real-time connection data from ACTIVE_USERS (populated by XRay log monitoring).
Replaces devices_api.py — served via Caddy on /devices/*.

Endpoints:
    GET /api/devices                         — all active users with IP counts
    GET /api/devices/{username_or_token}     — by username OR subscription token (auto-detected)
    GET /api/devices/sub/{token}             — explicit subscription token lookup
    GET /health                              — service health check

All endpoints require ?key=<API_KEY> query parameter.

Caddy config (change 8765 → 7070):
    handle_path /devices/* { reverse_proxy localhost:7070 }
"""

import base64
import hmac
import json
import os
import re
import sys
from collections import Counter

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
except ImportError:
    print("Module 'fastapi' is not installed. Run: pip install fastapi uvicorn")
    sys.exit()

try:
    import httpx
except ImportError:
    print("Module 'httpx' is not installed. Run: pip install httpx")
    sys.exit()

import time

from utils.check_usage import (
    ACTIVE_USERS,
    LAST_CHECK_RESULT,
    active_ips,
    active_window_seconds,
)
from utils.read_config import read_config

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

# Ключ device-API берётся ТОЛЬКО из окружения (DEVICE_API_KEY) — в коде не хранится.
# На сервере задаётся через /opt/v3iplimit/.env (EnvironmentFile в systemd-юните).
API_KEY = os.getenv("DEVICE_API_KEY")
if not API_KEY:
    print("FATAL: переменная окружения DEVICE_API_KEY не задана", file=sys.stderr)
    sys.exit(1)

app = FastAPI(title="V3IpLimit REST API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Subscription tokens are long base64url strings (url-safe base64, may contain _)
_TOKEN_RE = re.compile(r'^[A-Za-z0-9+/=\-_]{28,}$')


# ──────────────────────────────────────────────
# Token helpers
# ──────────────────────────────────────────────

def _looks_like_token(value: str) -> bool:
    """
    True if value looks like a Marzban subscription token rather than a plain username.
    Tokens are long base64url strings (28+ chars). Usernames are shorter.
    """
    return bool(_TOKEN_RE.match(value))


def _decode_username_from_token(token: str) -> str | None:
    """
    Fast O(1) extraction of Marzban username from a subscription token.

    Marzban encodes the subscription token as:
        base64url( username + "," + unix_timestamp ) + random_suffix

    The random suffix makes the length not a multiple of 4 (invalid base64),
    so we try trimming 0–5 chars from the end until we get a valid decode.

    Example:
        "QXJjbzk4MV9iYTk0LDE3NzkzNDM5NTEFjZnDeySll"
        → trim 1 → decode → b"Arco981_ba94,1793439151..."
        → username = "Arco981_ba94"
    """
    for trim in range(0, min(6, len(token) - 20)):
        chunk = token[: len(token) - trim]
        if len(chunk) % 4 == 1:
            continue
        padded = chunk + "=" * (-len(chunk) % 4)
        for decode_fn in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                raw = decode_fn(padded)
                m = re.match(rb'^([A-Za-z0-9][A-Za-z0-9_]{2,49}),\d+', raw)
                if m:
                    return m.group(1).decode("ascii")
            except Exception:  # pylint: disable=broad-except
                continue
    return None


async def _resolve_token(token: str) -> str | None:
    """
    Resolve a subscription token/key to a Marzneshin username.

    The VPN client app normally sends the plain username (Marzneshin sub URLs are
    /sub/{username}/{key}, and the client extracts the {username} segment), so
    resolution is rarely needed. This fallback covers the explicit
    /api/devices/sub/{key} path: scan Marzneshin users (page-based) and match the
    trailing {key} of their subscription_url.

    Strategy 1 — instant (O(1)): legacy Marzban base64 token decode (no-op for
                 Marzneshin keys, kept for backward compatibility).
    Strategy 2 — fallback  (O(N)): scan all users via the Marzneshin API.
    """
    # Fast path — legacy Marzban tokens only (Marzneshin keys won't decode)
    username = _decode_username_from_token(token)
    if username:
        return username

    # Slow path — scan the Marzneshin API
    try:
        config = await read_config()
        domain = config.get("PANEL_DOMAIN", "")
        panel_user = config.get("PANEL_USERNAME", "")
        panel_pass = config.get("PANEL_PASSWORD", "")

        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            access_token = None
            used_scheme = "https"
            for scheme in ["https", "http"]:
                try:
                    r = await client.post(
                        f"{scheme}://{domain}/api/admins/token",
                        data={"username": panel_user, "password": panel_pass},
                    )
                    r.raise_for_status()
                    access_token = r.json()["access_token"]
                    used_scheme = scheme
                    break
                except Exception:  # pylint: disable=broad-except
                    continue

            if not access_token:
                return None

            headers = {"Authorization": f"Bearer {access_token}"}
            page = 1
            size = 100
            while True:
                r = await client.get(
                    f"{used_scheme}://{domain}/api/users",
                    headers=headers,
                    params={"page": page, "size": size},
                )
                if r.status_code != 200:
                    break
                data = r.json()
                users = data.get("items", data.get("users", []))
                if not users:
                    break
                for u in users:
                    sub_url = u.get("subscription_url", "")
                    if sub_url.rstrip("/").rsplit("/", 1)[-1] == token:
                        return u.get("username")
                if len(users) < size:
                    break
                page += 1
    except Exception:  # pylint: disable=broad-except
        pass
    return None


# ──────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────

def _require_key(key: str) -> None:
    # Сравнение за константное время — обычный != уязвим к timing-атаке на подбор ключа.
    if not hmac.compare_digest(key, API_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")


async def _user_ips(username: str) -> list[str]:
    """
    Return active IPs for a user within the sliding ACTIVE_MINS window.

    Primary: live ACTIVE_USERS filtered by last-seen timestamp.
    Fallback: LAST_CHECK_RESULT snapshot from the previous enforcement cycle.
    """
    window = await active_window_seconds()
    live = active_ips(username, time.monotonic(), window)
    if live:
        return live
    # Fall back to last known good state
    return list(LAST_CHECK_RESULT.get(username, []))


async def _get_limit(username: str) -> int:
    """Return per-user IP limit from config. 0 = unlimited (shown as ∞ in the app)."""
    try:
        config = await read_config()
        general = int(config.get("GENERAL_LIMIT", 0))
        special = config.get("SPECIAL_LIMIT", {})
        return int(special.get(username, general))
    except Exception:  # pylint: disable=broad-except
        return 0


async def _build_response(username: str) -> JSONResponse:
    # Marzneshin приводит все username к нижнему регистру, и счётчик/лимиты используют
    # lowercase-ключи. Старые Marzban-токены кодируют ИСХОДНЫЙ регистр (напр. "Arco"),
    # поэтому без нормализации резолв по старой ссылке давал connected:0. Нормализуем.
    username = username.lower()
    ips = await _user_ips(username)
    limit = await _get_limit(username)
    # ips наружу не отдаём: ключ зашит в клиентское приложение, а список IP
    # всех юзеров — утечка приватных данных.
    return JSONResponse(content={
        "username": username,
        "connected": len(ips),
        "limit": limit,
    })


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

class SetLimitRequest(BaseModel):
    username: str
    limit: int


class IngestRequest(BaseModel):
    backend: str  # "xray" | "singbox"
    lines: str


@app.post("/api/ingest")
async def ingest(body: IngestRequest, key: str = Query(..., description="API key")):
    """Ingest access-log lines pushed by remote node agents (multi-node IP counting).

    Each node's log-forwarder POSTs new Xray/sing-box access-log lines here; they
    feed the same parsers as the local FR tailers, so ACTIVE_USERS aggregates IPs
    across ALL nodes (global per-user device count + enforcement)."""
    _require_key(key)
    from utils.parse_logs import parse_logs, parse_singbox_logs
    if body.lines:
        if body.backend == "singbox":
            await parse_singbox_logs(body.lines)
        else:
            await parse_logs(body.lines)
    return {"ok": True}


@app.post("/api/set_limit")
async def set_limit(body: SetLimitRequest, key: str = Query(..., description="API key")):
    """Set per-user IP limit in config.json. limit=0 removes the special limit."""
    _require_key(key)
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        special = cfg.setdefault("SPECIAL_LIMIT", {})
        if body.limit == 0:
            special.pop(body.username, None)
        else:
            special[body.username] = body.limit
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        # Invalidate read_config cache so next read picks up the change
        from utils.read_config import read_config as _rc
        import utils.read_config as _rc_mod
        _rc_mod.LAST_READ_TIME = 0
    except Exception as exc:  # pylint: disable=broad-except
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "username": body.username, "limit": body.limit}


@app.get("/api/devices")
async def get_all_devices(key: str = Query(..., description="API key")):
    """Return all currently active users with their IP counts."""
    _require_key(key)
    result = {}
    for uname in list(ACTIVE_USERS.keys()):
        ips = await _user_ips(uname)
        if ips:
            result[uname] = {"connected": len(ips)}
    return JSONResponse(content=result)


@app.get("/api/devices/sub/{token}")
async def get_devices_by_token(
    token: str,
    key: str = Query(..., description="API key"),
):
    """Return device info for a user identified by their subscription token."""
    _require_key(key)
    username = await _resolve_token(token)
    if username is None:
        raise HTTPException(status_code=404, detail="User not found for this token")
    return await _build_response(username)


@app.get("/api/devices/{username_or_token}")
async def get_devices(
    username_or_token: str,
    key: str = Query(..., description="API key"),
):
    """
    Return device info by username or subscription token.
    Auto-detects whether the input is a plain username or a base64 subscription token.
    """
    _require_key(key)

    username = username_or_token

    # Auto-detect: if it looks like a subscription token, resolve it first
    if _looks_like_token(username_or_token):
        resolved = await _resolve_token(username_or_token)
        if resolved:
            username = resolved

    return await _build_response(username)


@app.get("/health")
async def health():
    """Service health check."""
    return {"status": "ok", "tracked_users": len(ACTIVE_USERS)}
