"""Web payment API — маршруты для лендинга axiom.DOMAIN.

Регистрируются в start_webhook_server() в bot.py.
Не использует Telegram — отдаёт subscription URL прямо в ответе.
"""
import asyncio
import datetime
import random
import secrets
import string
import uuid
import aiohttp
from aiohttp import web
import config
import db

# CORS-заголовки: лендинг и бот на одном домене, но на случай локальной разработки
ALLOWED_ORIGIN = "https://axiom.DOMAIN"


def _claim_url(token: str) -> str:
    """Deep-link для привязки веб-покупки к Telegram-аккаунту."""
    return f"https://t.me/{config.BOT_USERNAME}?start=claim_{token}" if token else ""


def _validate_promo(code: str) -> dict | None:
    """Возвращает промокод, если он активен, не истёк и не исчерпан; иначе None."""
    if not code:
        return None
    p = db.get_promo(code)
    if not p or not p.get("active"):
        return None
    exp = p.get("expires_at")
    if exp and datetime.date.today().isoformat() > exp:
        return None
    max_uses = p.get("max_uses") or 0
    if max_uses and (p.get("used_count") or 0) >= max_uses:
        return None
    return p


def _discounted_price(base: int, percent: int) -> int:
    """Цена со скидкой (целые рубли). 100% → 0."""
    return max(0, round(base * (100 - percent) / 100))


# 2-часовой пробный доступ (bootstrap для тех, у кого нет VPN, чтобы дойти до Telegram)
TRIAL_PREVIEW_HOURS = 2


def _client_ip(request: web.Request) -> str:
    """Реальный IP клиента (за Caddy — из X-Forwarded-For)."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote or ""


def _cors(resp: web.Response, request: web.Request) -> web.Response:
    origin = request.headers.get("Origin", "")
    if origin in (ALLOWED_ORIGIN, "https://design.DOMAIN"):
        resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _payment_auth():
    return aiohttp.BasicAuth(config.PAYMENT_SHOP_ID, config.PAYMENT_SECRET)


def _payment_session() -> aiohttp.ClientSession:
    """Сессия для запросов к Платёжке: привязка исходящего адреса к RU-IP
    (см. config.PAYMENT_LOCAL_ADDR) + таймаут, чтобы при сбое падать быстро."""
    connector = None
    if config.PAYMENT_LOCAL_ADDR:
        connector = aiohttp.TCPConnector(local_addr=(config.PAYMENT_LOCAL_ADDR, 0))
    return aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=20))


def _gen_username() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"web_{suffix}"


def _resolve_referrer(ref) -> int | None:
    """Из тела оплаты (?ref=<telegram_id> на лендинге). Возвращает referrer_id, если это
    валидный существующий пользователь бота; иначе None. Самоприглашение на вебе проверить
    нельзя — покупатель анонимен (без Telegram ID до возможного claim)."""
    try:
        rid = int(str(ref).strip())
    except (TypeError, ValueError):
        return None
    if rid <= 0:
        return None
    return rid if db.user_exists(rid) else None


async def _create_payment(tariff_idx: int, username: str, method: str = None, amount: int = None) -> tuple[str, str]:
    """Создаёт платёж в Платёжке. Возвращает (payment_id, confirmation_url).
    amount — итоговая сумма в рублях (по умолчанию цена тарифа; для промокода — со скидкой)."""
    t = config.TARIFFS[tariff_idx]
    if amount is None:
        amount = t["price"]
    order_no = db.next_order_number()
    payload = {
        "amount": {"value": f"{amount}.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": "https://design.DOMAIN/?axiom_paid=1",
        },
        "description": f"Заказ №{order_no}",
        "metadata": {
            "user_id": "web",
            "tariff_idx": str(tariff_idx),
            "username": username,
            "payment_id_self": "",  # заполним после получения id
        },
    }
    if method:
        payload["payment_method_data"] = {"type": method}

    async with _payment_session() as s:
        r = await s.post(
            f"{config.PAYMENT_API_URL}/payments",
            json=payload,
            auth=_payment_auth(),
            headers={"Idempotence-Key": str(uuid.uuid4())},
        )
        data = await r.json()

    if r.status not in (200, 201) or "confirmation" not in data:
        raise Exception(data.get("description") or f"HTTP {r.status}")
    return data["id"], data["confirmation"]["confirmation_url"]


async def _create_marzban_user(username: str, tariff_idx: int) -> str:
    """Создаёт пользователя в Marzban. Возвращает subscription_url."""
    import bot as bot_module  # ленивый импорт чтобы избежать цикла
    t = config.TARIFFS[tariff_idx]
    result = await bot_module.create_user(username, t)
    if "detail" in result:
        # Юзер мог быть создан прошлой оборванной попыткой (таймаут ПОСЛЕ создания):
        # тогда повтор ловит 409. Это не ошибка — подтягиваем существующего.
        existing = await bot_module.get_marzban_user(username)
        if existing.get("username") and "detail" not in existing:
            result = existing
        else:
            raise Exception(result["detail"])
    sub_url = result.get("subscription_url", f"{config.MARZNESHIN_URL}/sub/{username}")

    ip_limit = t.get("ip_limit", 0)
    if ip_limit and ip_limit > 0:
        await bot_module.set_v2iplimit(username, ip_limit)

    return sub_url


async def issue_web_subscription(payment_id: str) -> str:
    """Создаёт Marzban-пользователя и возвращает sub_url.
    Идемпотентно: если уже succeeded — возвращает cached sub_url.
    Гонка webhook + опрос статуса разводится CAS-захватом pending → issuing:
    проигравший вызов не дублирует создание юзера (раньше ловил 409), а ждёт
    результат победителя."""
    wp = db.get_web_payment(payment_id)
    if not wp:
        raise Exception("web_payment not found")
    if wp["status"] == "succeeded":
        return wp["sub_url"]

    if not db.claim_web_issue(payment_id):
        # Выдачей уже занят параллельный вызов — дожидаемся его результата
        for _ in range(15):
            await asyncio.sleep(1)
            wp = db.get_web_payment(payment_id)
            if wp.get("status") == "succeeded":
                return wp["sub_url"]
        raise Exception("issue in progress")

    try:
        sub_url = await _create_marzban_user(wp["marzban_username"], wp["tariff_idx"])
    except Exception:
        # Вернуть платёж в pending, чтобы следующий webhook/опрос повторил выдачу
        db.update_web_payment(payment_id, "pending")
        raise
    # Засчитываем использование промокода один раз — на переходе в succeeded
    if wp.get("promo_code"):
        db.incr_promo_use(wp["promo_code"])
    db.update_web_payment(payment_id, "succeeded", sub_url)
    # Реферальные бонусы (как в боте). Не должны ломать выдачу подписки — внутри try/except.
    await _grant_referral_bonus(wp)
    return sub_url


async def _grant_referral_bonus(wp: dict) -> None:
    """Начисляет реферальные бонусы за ОПЛАЧЕННУЮ веб-покупку, как в боте:
      • покупателю — +7/30 дн. (по тарифу) к только что купленной веб-подписке;
      • рефереру (если пришёл по ?ref=<id>) — те же +7/30 дн. к его подписке (или в банк)
        с уведомлением в Telegram (через apply_pending_bonus, как в боте).
    Ровно один раз на платёж (CAS db.mark_web_bonus_granted) — защита от гонки webhook+опрос.
    Бесплатные покупки (100% промо) сюда не попадают (минуют issue_web_subscription)."""
    try:
        if not db.mark_web_bonus_granted(wp["payment_id"]):
            return  # бонус уже начислялся за этот платёж
        import bot as bot_module  # ленивый импорт чтобы избежать цикла
        t = config.TARIFFS[wp["tariff_idx"]]
        bonus = bot_module.referral_bonus_for(t)
        # 1) Покупателю — к купленной веб-подписке (анонимен, без Telegram-уведомления)
        try:
            await bot_module.extend_user_expire(wp["marzban_username"], bonus)
        except Exception as e:
            print(f"⚠️ web ref: бонус покупателю не начислен: {e}")
        # 2) Рефереру — в банк + применить к его активной подписке + уведомить (как в боте)
        referrer_id = wp.get("referrer_id")
        if referrer_id:
            try:
                db.add_ref_bonus_days(referrer_id, bonus)
                await bot_module.apply_pending_bonus(referrer_id, notify=True)
            except Exception as e:
                print(f"⚠️ web ref: бонус рефереру не начислен: {e}")
    except Exception as e:
        print(f"⚠️ web ref bonus error: {e}")


# ── Handlers ─────────────────────────────────────────────────────────────────

async def handle_options(request: web.Request) -> web.Response:
    return _cors(web.Response(status=204), request)


async def handle_create_payment(request: web.Request) -> web.Response:
    """POST /api/web/payment  body: {tariff_idx, method?}"""
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "invalid json"}, status=400), request)

    tariff_idx = body.get("tariff_idx")
    method = body.get("method")  # "sbp" or null
    referrer_id = _resolve_referrer(body.get("ref"))  # реферал с лендинга (?ref=<id>)

    if tariff_idx is None or not isinstance(tariff_idx, int) or not (0 <= tariff_idx < len(config.TARIFFS)):
        return _cors(web.json_response({"error": "invalid tariff_idx"}, status=400), request)
    if method not in (None, "sbp"):
        return _cors(web.json_response({"error": "invalid method"}, status=400), request)

    # Поле промокода принимает И скидочный промокод, И реферальный код.
    promo_raw = (body.get("promo") or "").strip().upper()
    promo = None
    if promo_raw:
        promo = _validate_promo(promo_raw)  # скидочный промокод?
        if not promo:
            rid = db.get_referrer_by_code(promo_raw)  # иначе — реферальный код?
            if rid:
                referrer_id = rid  # реф-код в поле промо приоритетнее ?ref
            else:
                return _cors(web.json_response({"error": "Код недействителен или исчерпан"}, status=400), request)
    # Самоприглашение: залогиненный покупатель не может применить свой же код/ссылку
    if referrer_id:
        _sess = db.get_web_session(request.cookies.get("axiom_session"))
        if _sess and _sess.get("telegram_id") == referrer_id:
            referrer_id = None
    base_price = config.TARIFFS[tariff_idx]["price"]
    price = _discounted_price(base_price, promo["percent"]) if promo else base_price
    promo_code = promo["code"] if promo else None

    username = _gen_username()
    claim_token = secrets.token_urlsafe(12)

    # Скидка 100% (или цена < 1 ₽): Платёжка не принимает платёж 0 ₽ —
    # выдаём подписку сразу, минуя оплату.
    if price < 1:
        payment_id = "free_" + secrets.token_urlsafe(10)
        db.save_web_payment(payment_id, tariff_idx, username, claim_token, promo_code, referrer_id)
        try:
            sub_url = await _create_marzban_user(username, tariff_idx)
        except Exception as e:
            print(f"❌ web_api: free issue error: {e}")
            return _cors(web.json_response({"error": str(e)}, status=502), request)
        if promo_code:
            db.incr_promo_use(promo_code)
        db.update_web_payment(payment_id, "succeeded", sub_url)
        return _cors(web.json_response({
            "status": "succeeded", "free": True,
            "sub_url": sub_url, "claim_url": _claim_url(claim_token),
        }), request)

    try:
        payment_id, payment_url = await _create_payment(tariff_idx, username, method, amount=price)
    except Exception as e:
        print(f"❌ web_api: create_payment error: {e}")
        return _cors(web.json_response({"error": str(e)}, status=502), request)

    db.save_web_payment(payment_id, tariff_idx, username, claim_token, promo_code, referrer_id)
    return _cors(web.json_response({"payment_id": payment_id, "payment_url": payment_url}), request)


async def handle_status(request: web.Request) -> web.Response:
    """GET /api/web/status?pid=XXXX"""
    payment_id = request.query.get("pid", "").strip()
    if not payment_id:
        return _cors(web.json_response({"error": "missing pid"}, status=400), request)

    wp = db.get_web_payment(payment_id)
    if not wp:
        return _cors(web.json_response({"status": "not_found"}, status=404), request)

    if wp["status"] == "succeeded":
        return _cors(web.json_response({
            "status": "succeeded", "sub_url": wp["sub_url"],
            "claim_url": _claim_url(wp.get("claim_token")),
        }), request)

    # Ещё pending — сверяемся с Платёжкой
    try:
        async with _payment_session() as s:
            r = await s.get(f"{config.PAYMENT_API_URL}/payments/{payment_id}", auth=_payment_auth())
            p = await r.json()
    except Exception as e:
        print(f"❌ web_api: status check error: {e}")
        return _cors(web.json_response({"status": "pending"}), request)

    if p.get("status") == "succeeded" and p.get("paid"):
        try:
            sub_url = await issue_web_subscription(payment_id)
            return _cors(web.json_response({
                "status": "succeeded", "sub_url": sub_url,
                "claim_url": _claim_url(wp.get("claim_token")),
            }), request)
        except Exception as e:
            print(f"❌ web_api: issue_web_subscription error: {e}")
            return _cors(web.json_response({"status": "error", "error": str(e)}, status=500), request)

    if p.get("status") == "canceled":
        db.update_web_payment(payment_id, "canceled")
        return _cors(web.json_response({"status": "canceled"}), request)

    return _cors(web.json_response({"status": "pending"}), request)


async def handle_promo(request: web.Request) -> web.Response:
    """POST /api/web/promo  body: {code, tariff_idx?} → проверка кода и пересчёт цены."""
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"valid": False, "error": "invalid json"}, status=400), request)

    code = (body.get("code") or "").strip().upper()
    tariff_idx = body.get("tariff_idx")
    if not code:
        return _cors(web.json_response({"valid": False}), request)

    promo = _validate_promo(code)
    if not promo:
        # не скидочный — может это реферальный код?
        if db.get_referrer_by_code(code):
            return _cors(web.json_response({"valid": True, "type": "referral", "code": code}), request)
        return _cors(web.json_response({"valid": False, "reason": "Код недействителен или исчерпан"}), request)

    resp = {"valid": True, "type": "discount", "code": promo["code"], "percent": promo["percent"]}
    if isinstance(tariff_idx, int) and 0 <= tariff_idx < len(config.TARIFFS):
        base = config.TARIFFS[tariff_idx]["price"]
        resp["old_price"] = base
        resp["new_price"] = _discounted_price(base, promo["percent"])
    return _cors(web.json_response(resp), request)


async def handle_create_trial(request: web.Request) -> web.Response:
    """POST /api/web/trial — выдаёт анонимный пробный доступ на 2 часа (для тех, у кого
    нет VPN дойти до Telegram). При последующем входе через Telegram он продлевается до
    полного триала (см. web_auth._maybe_bind_trial). Анти-спам: 1 на IP в сутки."""
    import time
    ip = _client_ip(request)
    if db.count_recent_trials_by_ip(ip, 24) >= 1:
        return _cors(web.json_response(
            {"error": "Пробный доступ с этого адреса уже выдавался. Войдите через Telegram, "
                      "чтобы получить полные 3 дня."}, status=429), request)

    import bot as bot_module  # ленивый импорт чтобы избежать цикла
    username = "wtrial_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    expire = int(time.time()) + TRIAL_PREVIEW_HOURS * 3600
    try:
        result = await bot_module.create_user(username, config.TRIAL, expire_override=expire)
        if "detail" in result:
            raise Exception(result["detail"])
        sub_url = result.get("subscription_url", f"{config.MARZNESHIN_URL}/sub/{username}")
        ip_limit = config.TRIAL.get("ip_limit", 0)
        if ip_limit and ip_limit > 0:
            await bot_module.set_v2iplimit(username, ip_limit)
    except Exception as e:
        print(f"❌ web_api: trial issue error: {e}")
        return _cors(web.json_response({"error": str(e)}, status=502), request)

    claim_token = secrets.token_urlsafe(16)
    db.create_web_trial(claim_token, username, ip)
    resp = web.json_response({"sub_url": sub_url, "hours": TRIAL_PREVIEW_HOURS})
    # cookie живёт чуть дольше окна, чтобы привязка при входе успела сработать
    resp.set_cookie("axiom_trial", claim_token, max_age=TRIAL_PREVIEW_HOURS * 3600 + 1800,
                    httponly=True, secure=True, samesite="Lax", path="/")
    return _cors(resp, request)


def register_routes(app: web.Application):
    """Вызывается из bot.py::start_webhook_server()."""
    db.init_web_payments()
    db.init_promos()
    db.init_web_trials()
    db.init_referral_codes()
    import web_auth
    web_auth.register_auth_routes(app)
    app.router.add_options("/api/web/trial", handle_options)
    app.router.add_post("/api/web/trial", handle_create_trial)
    app.router.add_options("/api/web/payment", handle_options)
    app.router.add_post("/api/web/payment", handle_create_payment)
    app.router.add_options("/api/web/status", handle_options)
    app.router.add_get("/api/web/status", handle_status)
    app.router.add_options("/api/web/promo", handle_options)
    app.router.add_post("/api/web/promo", handle_promo)
    print("🌐 web API: /api/web/payment + /api/web/status + /api/web/promo")
