"""Telegram-авторизация на сайте axiom.DOMAIN (Telegram Login Widget).

Личность = Telegram ID (тот же аккаунт, что в боте — таблица users). Сессия хранится
в БД (web_sessions), клиенту отдаётся в HttpOnly-cookie. Никаких паролей/почты.

Подпись виджета проверяется HMAC-SHA256 по токену бота (@BUY_BOT), для которого
в BotFather задан домен axiom.DOMAIN.
Док: https://core.telegram.org/widgets/login#checking-authorization
"""
import hashlib
import hmac
import secrets
import time

from aiohttp import web

import config
import db

SESSION_COOKIE = "axiom_session"
SESSION_TTL_DAYS = 30
AUTH_MAX_AGE = 86400  # подпись Telegram считается свежей в течение суток

ALLOWED_ORIGINS = ("https://axiom.DOMAIN", "https://design.DOMAIN")


def _cors(resp: web.Response, request: web.Request) -> web.Response:
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _verify_telegram_auth(data: dict) -> bool:
    """Проверяет подпись Telegram Login Widget. data — поля, присланные виджетом
    (id, first_name, auth_date, hash и опц. last_name/username/photo_url)."""
    received_hash = data.get("hash")
    if not received_hash or not isinstance(received_hash, str):
        return False
    # Свежесть (защита от повторного использования старых данных)
    try:
        if time.time() - int(data.get("auth_date")) > AUTH_MAX_AGE:
            return False
    except (TypeError, ValueError):
        return False
    # data_check_string: все поля кроме hash, "key=value", отсортированы, через \n
    pairs = [f"{k}={data[k]}" for k in data if k != "hash"]
    data_check_string = "\n".join(sorted(pairs))
    secret_key = hashlib.sha256(config.BOT_TOKEN.encode()).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, received_hash)


def _set_session_cookie(resp: web.Response, token: str) -> None:
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True, secure=True, samesite="Lax", path="/",
    )


# ── Handlers ─────────────────────────────────────────────────────────────────

async def handle_options(request: web.Request) -> web.Response:
    return _cors(web.Response(status=204), request)


async def handle_telegram_login(request: web.Request) -> web.Response:
    """POST /api/web/auth/telegram — данные Telegram Login Widget. Заводит/находит
    аккаунт (users.telegram_id), создаёт сессию, ставит cookie."""
    try:
        data = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "invalid json"}, status=400), request)
    if not isinstance(data, dict) or not _verify_telegram_auth(data):
        return _cors(web.json_response({"error": "auth verification failed"}, status=403), request)

    try:
        tg_id = int(data["id"])
    except (KeyError, TypeError, ValueError):
        return _cors(web.json_response({"error": "no id"}, status=400), request)

    db.add_user(tg_id, data.get("first_name") or "")  # INSERT OR IGNORE — единый аккаунт с ботом
    token = secrets.token_urlsafe(32)
    db.create_web_session(token, tg_id, SESSION_TTL_DAYS)

    # Привязка висящего 2ч-триала (бутстрап для тех, кто заходил без VPN)
    trial_result = await _maybe_bind_trial(request, tg_id)

    resp = web.json_response({
        "ok": True, "telegram_id": tg_id, "first_name": data.get("first_name"),
        "trial": trial_result,  # "extended" | "already_used" | None
    })
    _set_session_cookie(resp, token)
    if request.cookies.get("axiom_trial"):
        resp.del_cookie("axiom_trial", path="/")
    return _cors(resp, request)


async def _maybe_bind_trial(request: web.Request, tg_id: int):
    """Если у пользователя в cookie висит непривязанный 2ч-триал — привязывает его к
    Telegram ID и продлевает до полного триала (config.TRIAL). Дедуп: один триал на ID."""
    token = request.cookies.get("axiom_trial")
    if not token:
        return None
    trial = db.get_web_trial(token)
    if not trial or trial.get("status") != "unbound":
        return None
    if not db.bind_web_trial(token, tg_id):
        return None  # гонка — уже привязан
    if db.get_trial_used(tg_id):
        return "already_used"  # 2ч-доступ остаётся, но до 3 дней не продлеваем
    import bot as bot_module  # ленивый импорт чтобы избежать цикла
    try:
        # Ставим РОВНО полный срок триала (а не 2ч + 3 дня) — иначе в кабинете «4 дня».
        await bot_module.set_user_expire(
            trial["marzban_username"], int(time.time()) + config.TRIAL["days"] * 86400)
    except Exception as e:
        print(f"⚠️ trial bind extend error: {e}")
    db.set_trial_used(tg_id)
    db.add_subscription(tg_id, trial["marzban_username"], config.TRIAL)
    return "extended"


async def handle_me(request: web.Request) -> web.Response:
    """GET /api/web/me — текущий аккаунт + его подписки (из Marzban). 401, если не вошёл."""
    sess = db.get_web_session(request.cookies.get(SESSION_COOKIE))
    if not sess:
        return _cors(web.json_response({"authenticated": False}, status=401), request)
    tg_id = sess["telegram_id"]

    import bot as bot_module  # ленивый импорт чтобы избежать цикла
    subs = db.get_subscriptions(tg_id)
    out = []
    if subs:
        infos = await bot_module.get_marzban_users([s["marzban_username"] for s in subs])
        for s in subs:
            info = infos.get(s["marzban_username"])
            if not isinstance(info, dict) or "username" not in info:
                continue  # «сирота» (удалён из Marzban) — не показываем
            out.append({
                "username": s["marzban_username"],
                "tariff": s.get("tariff_name"),
                "status": info.get("status"),
                "expire": info.get("expire"),
                "used_traffic": info.get("used_traffic"),
                "data_limit": info.get("data_limit"),
                "sub_url": info.get("subscription_url") or f"{config.MARZNESHIN_URL}/sub/{s['marzban_username']}",
            })

    return _cors(web.json_response({
        "authenticated": True,
        "telegram_id": tg_id,
        "referral_code": db.get_or_create_referral_code(tg_id),
        "subscriptions": out,
    }), request)


async def handle_logout(request: web.Request) -> web.Response:
    """POST /api/web/logout — удаляет сессию и cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_web_session(token)
    resp = web.json_response({"ok": True})
    resp.del_cookie(SESSION_COOKIE, path="/")
    return _cors(resp, request)


def register_auth_routes(app: web.Application):
    """Вызывается из web_api.register_routes()."""
    db.init_web_sessions()
    app.router.add_options("/api/web/auth/telegram", handle_options)
    app.router.add_post("/api/web/auth/telegram", handle_telegram_login)
    app.router.add_options("/api/web/me", handle_options)
    app.router.add_get("/api/web/me", handle_me)
    app.router.add_options("/api/web/logout", handle_options)
    app.router.add_post("/api/web/logout", handle_logout)
    print("🔐 web auth: /api/web/auth/telegram + /api/web/me + /api/web/logout")
