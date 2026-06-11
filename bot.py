import asyncio
import re
import time
import uuid
from datetime import datetime, timezone
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import BotCommand
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import db
import web_api

db.init_db()

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


class PayStates(StatesGroup):
    waiting_fio = State()


class AdminStates(StatesGroup):
    waiting_search = State()   # ждём username/часть для поиска подписок
    waiting_amount = State()   # ждём число (±дни или ±ГБ) для правки


# ── Вспомогательные функции ──────────────────────────────────────────────────

def main_menu_kb(show_trial: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if show_trial:
        rows.append([InlineKeyboardButton(text="🎁 Получить пробный период (3 дня)", callback_data="get_trial")])
    rows += [
        [InlineKeyboardButton(text="🛒 Купить", callback_data="buy")],
        [InlineKeyboardButton(text="📋 Сравнить тарифы", callback_data="tariffs_info")],
        [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="cabinet")],
        [InlineKeyboardButton(text="🎁 Пригласить друга", callback_data="referral")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_category_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Лимит по устройствам", callback_data="cat_ip")],
        [InlineKeyboardButton(text="📊 Лимит по трафику", callback_data="cat_traffic")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])


def gb_str(t: dict) -> str:
    return f"{t['gb']} GB" if t.get("gb", 0) > 0 else "Безлимит"


# Максимальный суммарный срок подписки при продлении (2 года)
MAX_TOTAL_DAYS = 730


def tariff_index_by_name(name: str):
    """Индекс тарифа в config.TARIFFS по его названию (или None)."""
    for i, t in enumerate(config.TARIFFS):
        if t["name"] == name:
            return i
    return None


def tariff_index_for_sub(sub: dict):
    """Индекс тарифа подписки: сначала по стабильному коду, затем (для старых записей) по имени."""
    code = sub.get("tariff_code")
    if code:
        for i, t in enumerate(config.TARIFFS):
            if t.get("code") == code:
                return i
    return tariff_index_by_name(sub.get("tariff_name", ""))


def md_escape(s: str) -> str:
    """Экранирует спецсимволы legacy-Markdown (для пользовательского ввода вроде ФИО)."""
    for ch in ("_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def fio_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancelfio")]
    ])


FIO_PROMPT = (
    "✍️ Введите *ФИО владельца карты*, с которой вы отправили (или отправите) перевод.\n\n"
    "Это нужно, чтобы мы нашли ваш платёж. Напишите одним сообщением.\n"
    "Например: `Иван Иванович И.`"
)


def days_left(info: dict) -> int:
    """Сколько дней осталось до истечения подписки по данным Marzban."""
    expire = info.get("expire") or 0
    if not expire:
        return 0
    return max(0, int((expire - time.time()) / 86400))


WELCOME_TEXT = (
    f"🛡 *{config.BOT_NAME}*\n\n"
    f"{config.WELCOME_TEXT}\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📋 *Команды:*\n"
    "• /start — главное меню\n"
    "• /me — личный кабинет\n"
    "• /help — помощь\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎁 *Приглашай друзей — получай дни VPN бесплатно!*\n"
    f"За каждую оплату друга тебе начисляются дни к подписке: "
    f"*+{config.REFERRAL_BONUS_DAYS} дн.* за месячный тариф, "
    f"*+{config.REFERRAL_BONUS_DAYS_YEAR} дн.* за годовой.\n"
    "Жми «🎁 Пригласить друга» и поделись своей ссылкой.\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "💬 *Сообщество:* @COMMUNITY\n\n"
    "👇 Выбери действие:"
)

HELP_TEXT = (
    "❓ *Помощь*\n\n"
    "📌 *Как начать пользоваться VPN:*\n"
    "1. Купи подписку — выбери тариф и оплати\n"
    "2. После подтверждения оплаты получишь ссылку\n"
    "3. Открой приложение *Hiddify* и вставь ссылку\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📋 *Команды:*\n"
    "• /start — главное меню\n"
    "• /me — статус подписок и трафик\n"
    "• /help — эта справка\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🆘 *Проблемы с подключением?*\n"
    "Напиши нам: @SUPPORT_BOT\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎁 *Реферальная программа*\n"
    "Приглашай друзей по своей ссылке и получай дни VPN бесплатно. "
    f"За каждую оплату друга: *+{config.REFERRAL_BONUS_DAYS} дн.* за месячный тариф, "
    f"*+{config.REFERRAL_BONUS_DAYS_YEAR} дн.* за годовой.\n"
    "Ссылка — в главном меню, кнопка «🎁 Пригласить друга».\n\n"
    "💬 *Наше сообщество:* @COMMUNITY"
)


# ── V2IpLimit ────────────────────────────────────────────────────────────────

async def set_v2iplimit(username: str, ip_limit: int) -> None:
    """Обновляет индивидуальный лимит IP через HTTP API V2IpLimit."""
    url = f"{config.V2IPLIMIT_API_URL}/api/set_limit"
    try:
        async with _http() as s:
            r = await s.post(
                url,
                params={"key": config.V2IPLIMIT_API_KEY},
                json={"username": username, "limit": ip_limit},
            )
            if r.status == 200:
                print(f"✅ V2IpLimit: {username} → {ip_limit} IP")
            else:
                text = await r.text()
                print(f"❌ V2IpLimit: HTTP {r.status} — {text}")
    except Exception as e:
        print(f"❌ V2IpLimit: не удалось обновить лимит: {e}")


async def get_device_count(username: str) -> dict | None:
    """Текущее число подключённых IP и лимит из V2IpLimit.
    Возвращает {'connected': int, 'limit': int} (limit 0 = безлимит) или None при ошибке."""
    url = f"{config.V2IPLIMIT_API_URL}/api/devices/{username}"
    try:
        async with _http() as s:
            r = await s.get(url, params={"key": config.V2IPLIMIT_API_KEY})
            if r.status == 200:
                d = await r.json()
                return {"connected": int(d.get("connected", 0)), "limit": int(d.get("limit", 0))}
    except Exception as e:
        print(f"❌ V2IpLimit: счётчик устройств {username}: {e}")
    return None


# ── Платёжка ───────────────────────────────────────────────────────────────────

# Ссылка возврата после оплаты — нейтральный сайт магазина (см. config).
RETURN_URL = config.PAYMENT_RETURN_URL


def _payment_auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(config.PAYMENT_SHOP_ID, config.PAYMENT_SECRET)


def _payment_session() -> aiohttp.ClientSession:
    """Сессия для запросов к Платёжке: привязка исходящего адреса к RU-IP
    (см. config.PAYMENT_LOCAL_ADDR) + таймаут, чтобы при сбое падать быстро."""
    connector = None
    if config.PAYMENT_LOCAL_ADDR:
        connector = aiohttp.TCPConnector(local_addr=(config.PAYMENT_LOCAL_ADDR, 0))
    return aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=20))


def _http(headers=None) -> aiohttp.ClientSession:
    """ClientSession с таймаутом для Marzban/V2IpLimit — чтобы при их недоступности
    (например, упавший NL-тоннель) хендлер падал быстро (20с), а не висел дефолтные 5 минут aiohttp."""
    return aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=20))


async def create_payment(user_id: int, idx: int, username: str, method: str = None) -> tuple[str, str]:
    """Создаёт платёж в Платёжке. Возвращает (payment_id, confirmation_url).
    method="sbp" — сразу СБП-страница (QR на десктопе, список банков на мобиле)."""
    t = config.TARIFFS[idx]
    order_no = db.next_order_number()
    payload = {
        "amount": {"value": f"{t['price']}.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": RETURN_URL},
        "description": f"Заказ №{order_no}",
        "metadata": {"user_id": str(user_id), "tariff_idx": str(idx), "username": username},
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


async def get_payment(payment_id: str) -> dict:
    """Встречный запрос статуса платежа (источник истины, не доверяем телу webhook)."""
    async with _payment_session() as s:
        r = await s.get(f"{config.PAYMENT_API_URL}/payments/{payment_id}", auth=_payment_auth())
        return await r.json()


async def issue_subscription(user_id: int) -> tuple[bool, str]:
    """Создаёт пользователя Marzban по pending-заявке и шлёт ссылку. Общая логика для
    webhook и кнопки «Проверить оплату». Возвращает (успех, username|причина)."""
    pending = db.get_pending(user_id)
    if not pending or pending.get("is_renewal"):
        return False, "no_pending"
    idx = pending["tariff_idx"]
    t = config.TARIFFS[idx]
    username = pending["marzban_username"]

    result = await create_user(username, t)
    if "detail" in result:
        raise Exception(result["detail"])

    sub_url = result.get("subscription_url", f"{config.MARZNESHIN_URL}/sub/{username}")
    db.add_subscription(user_id, username, t)
    db.delete_pending(user_id)

    ip_limit = t.get("ip_limit", 0)
    if ip_limit and ip_limit > 0:
        await set_v2iplimit(username, ip_limit)

    await bot.send_message(
        user_id,
        f"✅ *Оплата получена!*\n\n"
        f"📦 {t['name']} | {gb_str(t)} | {t['days']} дней\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔗 *Ссылка на подписку:*\n`{sub_url}`\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Вставь ссылку в приложение *AxiOm* или любой другой совместимый клиент.\n\n"
        "Используй /me чтобы следить за трафиком.",
        parse_mode="Markdown",
    )

    # Оплаченная покупка приглашённого → награда пригласившему за этот платёж.
    # Плюс welcome-бонус самому покупателю (за первую покупку по реф-ссылке).
    # Плюс применяем собственные накопленные бонусные дни этого покупателя.
    await maybe_reward_referral(user_id, referral_bonus_for(t))
    await maybe_reward_buyer(user_id, username, referral_bonus_for(t))
    await apply_pending_bonus(user_id, notify=True)
    return True, username


async def payment_webhook(request: web.Request) -> web.Response:
    """Принимает уведомление payment.succeeded, верифицирует через API, выдаёт подписку."""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400)

    if body.get("event") != "payment.succeeded":
        return web.Response(status=200)

    obj = body.get("object") or {}
    payment_id = obj.get("id")
    meta = obj.get("metadata") or {}
    raw_uid = meta.get("user_id")
    if not payment_id or not raw_uid:
        return web.Response(status=200)

    # Web-покупка через лендинг (user_id == "web")
    if raw_uid == "web":
        try:
            p = await get_payment(payment_id)
        except Exception as e:
            print(f"❌ webhook(web): не смог проверить {payment_id}: {e}")
            return web.Response(status=500)
        if p.get("status") == "succeeded" and p.get("paid"):
            try:
                await web_api.issue_web_subscription(payment_id)
                print(f"✅ webhook(web): выдана подписка по {payment_id}")
            except Exception as e:
                print(f"❌ webhook(web): issue_web_subscription: {e}")
                return web.Response(status=500)
        return web.Response(status=200)

    user_id = int(raw_uid)

    # Источник истины — встречный запрос к API (тело webhook не доверяем)
    try:
        p = await get_payment(payment_id)
    except Exception as e:
        print(f"❌ webhook: не смог проверить платёж {payment_id}: {e}")
        return web.Response(status=500)  # Платёжка повторит

    if p.get("status") != "succeeded" or not p.get("paid"):
        return web.Response(status=200)

    pending = db.get_pending(user_id)
    if pending and pending.get("payment_id") and pending["payment_id"] != payment_id:
        return web.Response(status=200)  # устаревшее уведомление

    try:
        await issue_subscription(user_id)
    except Exception as e:
        print(f"❌ webhook: ошибка выдачи подписки для {user_id}: {e}")
        return web.Response(status=500)  # Платёжка повторит

    return web.Response(status=200)


# ── Уведомления админам ──────────────────────────────────────────────────────

async def notify_admins(text: str, kb: InlineKeyboardMarkup = None) -> None:
    """Рассылает заявку всем админам. Ошибка отправки одному не блокирует остальных."""
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            print(f"❌ Не удалось отправить заявку админу {admin_id}: {e}")


# ── Marzneshin Helpers ───────────────────────────────────────────────────────

def _ts_to_iso(ts: int) -> str:
    """Unix timestamp → ISO 8601 дата для Marzneshin (UTC)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _tariff_to_service_ids(tariff: dict) -> list:
    """Маппинг тарифа → service_ids Marzneshin:
    тарифы с "VLESS WS" → [2] (Maximum), остальные → [1] (Standard)."""
    if "VLESS WS" in tariff.get("inbounds", []):
        return [2]
    return [1]


def _normalize_user(info: dict) -> dict:
    """Конвертирует ISO expire_date из Marzneshin в unix expire (для обратной совместимости)."""
    if not isinstance(info, dict) or "username" not in info:
        return info
    expire_date = info.pop("expire_date", None)
    if expire_date:
        try:
            dt = datetime.fromisoformat(expire_date.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            info["expire"] = int(dt.timestamp())
        except Exception:
            info["expire"] = 0
    # Marzneshin не отдаёт поле "status" (как Marzban) — синтезируем его из булевых
    # флагов, чтобы остальной код (/me, продление и т.д.) работал без изменений.
    if "status" not in info:
        if info.get("expired"):
            info["status"] = "expired"
        elif info.get("data_limit_reached"):
            info["status"] = "limited"
        elif info.get("is_active") or info.get("enabled"):
            info["status"] = "active"
        else:
            info["status"] = "disabled"
    return info


# ── Marzban API ──────────────────────────────────────────────────────────────

# Кэш админ-токена Marzban: чтобы не логиниться на КАЖДУЮ операцию (меньше нагрузки и
# уведомлений Telegram-логгера Marzban). Обновляется по exp из JWT (с запасом 5 мин).
_marzban_token = {"value": None, "exp": 0.0}


def _jwt_ttl(token: str, fallback: float = 3600.0) -> float:
    """Сколько секунд держать токен: до exp из JWT минус запас 5 мин, иначе fallback."""
    try:
        import base64, json
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        if exp:
            return max(60.0, float(exp) - time.time() - 300)
    except Exception:
        pass
    return fallback


async def get_token() -> str:
    now = time.time()
    if _marzban_token["value"] and now < _marzban_token["exp"]:
        return _marzban_token["value"]
    async with _http() as s:
        r = await s.post(
            f"{config.MARZNESHIN_URL}/api/admins/token",
            data={"username": config.MARZNESHIN_USERNAME, "password": config.MARZNESHIN_PASSWORD},
        )
        try:
            data = await r.json(content_type=None)
        except Exception:
            data = {}
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise Exception(f"Marzneshin не выдал токен (HTTP {r.status})")
    _marzban_token["value"] = token
    _marzban_token["exp"] = now + _jwt_ttl(token)
    return token


async def create_user(username: str, tariff: dict, expire_override: int = None) -> dict:
    token = await get_token()
    expire_ts = expire_override if expire_override is not None else int(time.time()) + tariff["days"] * 86400
    payload = {
        "username": username,
        "expire_strategy": "fixed_date",
        "expire_date": _ts_to_iso(expire_ts),
        "service_ids": _tariff_to_service_ids(tariff),
        "data_limit": 0 if tariff.get("gb", 0) == 0 else tariff["gb"] * 1024 ** 3,
        "data_limit_reset_strategy": "no_reset",
    }
    async with _http() as s:
        r = await s.post(
            f"{config.MARZNESHIN_URL}/api/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        return _normalize_user(await r.json())


async def get_marzban_user(username: str) -> dict:
    token = await get_token()
    async with _http() as s:
        r = await s.get(
            f"{config.MARZNESHIN_URL}/api/users/{username}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return _normalize_user(await r.json())


async def get_marzban_users(usernames: list) -> dict:
    """Параллельно получает пользователей Marzneshin одним токеном и сессией.
    Возвращает {username: info|None}. Заменяет N последовательных запросов на N параллельных."""
    if not usernames:
        return {}
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with _http(headers) as s:
        async def fetch_one(u: str):
            try:
                r = await s.get(f"{config.MARZNESHIN_URL}/api/users/{u}")
                return u, _normalize_user(await r.json())
            except Exception:
                return u, None
        results = await asyncio.gather(*(fetch_one(u) for u in usernames))
    return dict(results)


async def delete_user(username: str) -> int:
    """Удаляет пользователя из Marzneshin. Возвращает HTTP-статус (200 — удалён, 404 — уже нет)."""
    token = await get_token()
    async with _http() as s:
        r = await s.delete(
            f"{config.MARZNESHIN_URL}/api/users/{username}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status


async def renew_user(username: str, tariff: dict) -> dict:
    """Продлевает существующего пользователя: +дни к expire, сброс трафика, тот же тариф."""
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with _http() as s:
        r = await s.get(f"{config.MARZNESHIN_URL}/api/users/{username}", headers=headers)
        current = _normalize_user(await r.json())

        now = int(time.time())
        base = max(now, current.get("expire") or 0)
        payload = {
            "username": username,
            "expire_strategy": "fixed_date",
            "expire_date": _ts_to_iso(base + tariff["days"] * 86400),
            "service_ids": _tariff_to_service_ids(tariff),
            "data_limit": 0 if tariff.get("gb", 0) == 0 else tariff["gb"] * 1024 ** 3,
            "data_limit_reset_strategy": "no_reset",
        }
        r = await s.put(f"{config.MARZNESHIN_URL}/api/users/{username}", json=payload, headers=headers)
        result = _normalize_user(await r.json())
        # Новый expire в будущем уже снимает expired-статус. Если юзер был отключён —
        # явно включаем (поля 'status' в UserModify нет, для этого есть отдельный эндпоинт).
        await s.post(f"{config.MARZNESHIN_URL}/api/users/{username}/enable", headers=headers)
        # Обнуляем израсходованный трафик
        await s.post(f"{config.MARZNESHIN_URL}/api/users/{username}/reset", headers=headers)
    return result


async def extend_user_expire(username: str, add_days: int) -> int:
    """Добавляет дни к сроку подписки, не трогая трафик/тариф. Уважает кэп MAX_TOTAL_DAYS.
    Возвращает реально начисленное число дней (0 — если уже у потолка)."""
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with _http() as s:
        r = await s.get(f"{config.MARZNESHIN_URL}/api/users/{username}", headers=headers)
        current = _normalize_user(await r.json())
        if "username" not in current:
            return 0
        now = int(time.time())
        base = max(now, current.get("expire") or 0)
        cap = now + MAX_TOTAL_DAYS * 86400
        new_expire = min(base + add_days * 86400, cap)
        if new_expire <= base:
            return 0  # уже у потолка — не начисляем
        await s.put(
            f"{config.MARZNESHIN_URL}/api/users/{username}",
            json={
                "username": username,
                "expire_strategy": "fixed_date",
                "expire_date": _ts_to_iso(new_expire),
            },
            headers=headers,
        )
    return int((new_expire - base) / 86400)


async def set_user_expire(username: str, expire_ts: int) -> None:
    """Жёстко выставляет срок подписки (в отличие от extend — не прибавляет к текущему).
    Нужно для привязки 2ч-триала: ставим ровно полный срок триала, а не 2ч + 3 дня."""
    token = await get_token()
    async with _http() as s:
        await s.put(
            f"{config.MARZNESHIN_URL}/api/users/{username}",
            json={
                "username": username,
                "expire_strategy": "fixed_date",
                "expire_date": _ts_to_iso(int(expire_ts)),
            },
            headers={"Authorization": f"Bearer {token}"},
        )


async def get_all_marzban_users(search: str = "", offset: int = 0, limit: int = 20) -> dict:
    """Список пользователей Marzneshin (для админ-панели). search фильтрует по username.
    Возвращает {'users': [...], 'total': N}. Конвертирует offset/limit → page/size."""
    token = await get_token()
    page = offset // limit + 1 if limit > 0 else 1
    params = {"page": page, "size": limit}
    if search:
        params["username"] = search  # Marzneshin фильтрует по 'username' (подстрока); 'search' игнорируется
    async with _http() as s:
        r = await s.get(
            f"{config.MARZNESHIN_URL}/api/users",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        data = await r.json()
    if isinstance(data, dict):
        users = data.get("users", data.get("items", []))
        data["users"] = [_normalize_user(u) for u in users]
    return data if isinstance(data, dict) else {"users": [], "total": 0}


async def admin_adjust_user(username: str, add_days: int = 0, add_gb: int = 0) -> dict:
    """Админ-правка подписки: ±дни (expire) и ±ГБ (data_limit). Возвращает обновлённого
    юзера или {'error': ...}. ГБ редактируются только у лимитных тарифов (0 = безлимит)."""
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with _http() as s:
        r = await s.get(f"{config.MARZNESHIN_URL}/api/users/{username}", headers=headers)
        cur = _normalize_user(await r.json())
        if "username" not in cur:
            return {"error": "Пользователь не найден"}
        payload = {"username": username}
        if add_days:
            base = cur.get("expire") or int(time.time())
            new_expire = max(0, int(base) + add_days * 86400)
            payload["expire_strategy"] = "fixed_date"
            payload["expire_date"] = _ts_to_iso(new_expire)
        if add_gb:
            cur_lim = cur.get("data_limit") or 0
            if cur_lim == 0:
                return {"error": "Тариф безлимитный (∞) — ГБ не редактируются."}
            new_lim = cur_lim + add_gb * 1024 ** 3
            if new_lim <= 0:
                return {"error": "Лимит не может стать ≤ 0 ГБ."}
            payload["data_limit"] = new_lim
        if len(payload) == 1:  # только username — нечего менять
            return cur
        r2 = await s.put(f"{config.MARZNESHIN_URL}/api/users/{username}", json=payload, headers=headers)
        return _normalize_user(await r2.json())


# ── Реферальная система ──────────────────────────────────────────────────────

async def apply_pending_bonus(user_id: int, notify: bool = False) -> None:
    """Применяет накопленные бонусные дни к самой долгой активной подписке пользователя.
    Если активной подписки нет — дни остаются в банке. notify=True → уведомить пользователя."""
    pending = db.get_ref_bonus_days(user_id)
    if pending <= 0:
        return

    subs = db.get_subscriptions(user_id)
    best_username = None
    best_expire = -1
    if subs:
        infos = await get_marzban_users([s["marzban_username"] for s in subs])
        for s in subs:
            info = infos.get(s["marzban_username"])
            # Только активные подписки: иначе бонус «оживил» бы просроченную/исчерпанную.
            # Нет активных → best_username останется None, дни сохранятся в банке.
            if isinstance(info, dict) and info.get("status") == "active":
                exp = info.get("expire") or 0
                if exp > best_expire:
                    best_expire, best_username = exp, s["marzban_username"]

    if best_username is None:
        # Активной подписки нет — оставляем в банке, при желании уведомляем
        if notify:
            await _safe_send(
                user_id,
                f"🎁 *Реферальный бонус: +{pending} дн.*\n\n"
                "Дни будут начислены к подписке автоматически, как только она у тебя появится "
                "(после покупки или продления).",
            )
        return

    granted = await extend_user_expire(best_username, pending)
    if granted > 0:
        db.consume_ref_bonus_days(user_id, granted)
    if notify:
        if granted > 0:
            await _safe_send(
                user_id,
                f"🎉 *Реферальный бонус начислен!*\n\n"
                f"К твоей подписке добавлено *+{granted} дн.* за приглашённого друга.\n"
                "Спасибо, что делишься AxiOm! Проверить срок — /me",
            )
        else:
            await _safe_send(
                user_id,
                f"🎁 *Реферальный бонус: +{pending} дн.*\n\n"
                "Сейчас срок подписки у максимума (2 года), поэтому бонус сохранён "
                "и начислится позже.",
            )


def referral_bonus_for(tariff: dict) -> int:
    """Сколько дней получает пригласивший за оплату приглашённым этого тарифа.
    Годовой тариф (≥ 365 дн.) → REFERRAL_BONUS_DAYS_YEAR, иначе (месячный) → REFERRAL_BONUS_DAYS."""
    if tariff.get("days", 0) >= 365:
        return config.REFERRAL_BONUS_DAYS_YEAR
    return config.REFERRAL_BONUS_DAYS


async def maybe_reward_referral(referred_id: int, bonus_days: int) -> None:
    """Вызывается после КАЖДОЙ оплаты приглашённого (покупка и продление). Начисляет
    bonus_days пригласившему (referrer); размер зависит от тарифа (см. referral_bonus_for).
    На триал не вызывается. Идемпотентность на платёж обеспечивают вызывающие: pending
    удаляется до вызова, так что на один платёж reward срабатывает ровно один раз."""
    ref = db.get_referral(referred_id)
    if not ref:
        return
    referrer_id = ref["referrer_id"]
    db.mark_referral_rewarded(referred_id)  # для статистики «оплатили» (идемпотентно)
    db.add_ref_bonus_days(referrer_id, bonus_days)
    await apply_pending_bonus(referrer_id, notify=True)


async def maybe_reward_buyer(user_id: int, username: str, bonus_days: int) -> None:
    """Welcome-бонус самому приглашённому: если покупатель пришёл по реферальной ссылке,
    за ПЕРВУЮ оплаченную покупку начисляет ему такой же бонус (7/30 дн. по тарифу), как
    и пригласившему, прямо к только что купленной подписке (username).
    Идемпотентно — ровно один раз (флаг referrals.buyer_rewarded). Вызывать только из
    точек покупки (не продления), чтобы бонус давался именно за первую покупку."""
    if not db.get_referral(user_id):
        return  # пришёл не по реф-ссылке — бонуса нет
    if not db.mark_buyer_rewarded(user_id):
        return  # welcome-бонус уже был выдан ранее
    granted = await extend_user_expire(username, bonus_days)
    if granted > 0:
        await _safe_send(
            user_id,
            f"🎁 *Бонус за покупку по приглашению: +{granted} дн.!*\n\n"
            "Тебе начислено столько же дней, сколько получает пригласивший друг — "
            "спасибо, что пришёл в AxiOm по рекомендации!\n"
            "Проверить срок — /me",
        )


async def _safe_send(user_id: int, text: str) -> None:
    """Отправка с проглатыванием ошибок (пользователь мог заблокировать бота)."""
    try:
        await bot.send_message(user_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"❌ Не удалось отправить сообщение {user_id}: {e}")


# ── /start ───────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext, command: CommandObject):
    await state.clear()
    user_id = message.from_user.id
    is_new = not db.user_exists(user_id)
    db.add_user(user_id, message.from_user.first_name or "")

    # Deep-link привязки веб-покупки: t.me/<bot>?start=claim_<token>
    args = (command.args or "").strip()
    if args.startswith("claim_"):
        await claim_web_subscription(message, user_id, args[len("claim_"):])
        return

    # Реферальная ссылка: t.me/<bot>?start=ref_<referrer_id>.
    # Атрибуция только для нового пользователя, не на самого себя и при существующем реферере.
    if args.startswith("ref_") and is_new:
        try:
            referrer_id = int(args[len("ref_"):])
        except ValueError:
            referrer_id = None
        if referrer_id and referrer_id != user_id and db.user_exists(referrer_id):
            if db.set_referrer(user_id, referrer_id):
                await _safe_send(
                    referrer_id,
                    "👥 *По твоей ссылке пришёл новый пользователь!*\n\n"
                    f"Когда он оплатит подписку, ты получишь бонусные дни к своей: "
                    f"*+{config.REFERRAL_BONUS_DAYS} дн.* за месячный тариф или "
                    f"*+{config.REFERRAL_BONUS_DAYS_YEAR} дн.* за годовой.",
                )

    await message.answer(
        WELCOME_TEXT, parse_mode="Markdown",
        reply_markup=main_menu_kb(show_trial=not db.get_trial_used(user_id)),
    )


async def claim_web_subscription(message: types.Message, user_id: int, token: str):
    """Привязывает веб-покупку (оплаченную на лендинге) к Telegram-аккаунту,
    добавляя её в общую таблицу subscriptions — после этого она видна в /me."""
    wp = db.get_web_payment_by_token(token)
    if not wp:
        await message.answer("⚠️ Ссылка привязки недействительна или устарела.")
        return
    if wp.get("status") != "succeeded":
        await message.answer(
            "⏳ Оплата ещё не завершена. Заверши оплату на сайте и снова нажми "
            "кнопку «Привязать к Telegram»."
        )
        return

    idx = wp.get("tariff_idx")
    if not (isinstance(idx, int) and 0 <= idx < len(config.TARIFFS)):
        await message.answer(
            "⚠️ Тариф этой покупки больше недоступен. Напиши в поддержку: "
            "@SUPPORT_BOT",
            parse_mode="Markdown",
        )
        return

    claimed_by = wp.get("claimed_by")
    if claimed_by and claimed_by != user_id:
        await message.answer("⛔️ Эта подписка уже привязана к другому Telegram-аккаунту.")
        return
    if claimed_by == user_id:
        await message.answer("✅ Эта подписка уже привязана к твоему аккаунту.")
        await show_cabinet(user_id, message, edit=False)
        return

    # Атомарно закрепляем за этим пользователем (защита от гонки/двойного клика)
    if not db.claim_web_payment(wp["payment_id"], user_id):
        await message.answer("✅ Эта подписка уже привязана.")
        await show_cabinet(user_id, message, edit=False)
        return

    t = config.TARIFFS[idx]
    db.add_subscription(user_id, wp["marzban_username"], t)
    await message.answer(
        "✅ *Подписка привязана к твоему аккаунту!*\n\n"
        f"📦 {t['name']}\n\n"
        "Теперь она в личном кабинете — трафик, срок, продление и удаление.",
        parse_mode="Markdown",
    )
    await show_cabinet(user_id, message, edit=False)


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        WELCOME_TEXT, parse_mode="Markdown",
        reply_markup=main_menu_kb(show_trial=not db.get_trial_used(callback.from_user.id)),
    )


# ── Пробный период ───────────────────────────────────────────────────────────

@dp.callback_query(F.data == "get_trial")
async def get_trial(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    db.add_user(user_id, callback.from_user.first_name or "")

    if db.get_trial_used(user_id):
        await callback.answer("Вы уже получали пробный период.", show_alert=True)
        try:
            await callback.message.edit_text(
                WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb(show_trial=False))
        except TelegramBadRequest:
            pass
        return

    t = config.TRIAL
    tg_username = callback.from_user.username
    if tg_username:
        base = re.sub(r'[^a-zA-Z0-9_]', '_', tg_username)[:20]
    else:
        base = f"user_{user_id}"
    # Marzneshin приводит username к нижнему регистру при создании — генерируем сразу
    # в lowercase, иначе сохранённое в БД имя не совпадёт с панелью (/me, продление и т.д.)
    username = f"{base}_t{uuid.uuid4().hex[:4]}".lower()

    await callback.message.edit_text("⏳ Активирую пробный период...")
    try:
        result = await create_user(username, t)
        if "detail" in result:
            raise Exception(result["detail"])

        sub_url = result.get("subscription_url", f"{config.MARZNESHIN_URL}/sub/{username}")
        db.add_subscription(user_id, username, t)
        db.set_trial_used(user_id)

        ip_limit = t.get("ip_limit", 0)
        if ip_limit and ip_limit > 0:
            await set_v2iplimit(username, ip_limit)

        await callback.message.edit_text(
            "🎁 *Пробный период активирован!*\n\n"
            f"📦 {t['name']}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🔗 *Ссылка на подписку:*\n`{sub_url}`\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Вставь ссылку в приложение *AxiOm* или любой совместимый клиент.\n"
            f"Через {t['days']} дн. подписка истечёт — оформи полноценный тариф в «Купить».\n\n"
            "Используй /me чтобы следить за подпиской.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_menu")]
            ]),
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Не удалось активировать пробный период: {e}", reply_markup=back_kb())


# ── /help ────────────────────────────────────────────────────────────────────

@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(HELP_TEXT, parse_mode="Markdown", reply_markup=back_kb())


@dp.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    await callback.message.edit_text(HELP_TEXT, parse_mode="Markdown", reply_markup=back_kb())


# ── Сравнение тарифов ────────────────────────────────────────────────────────

def build_tariffs_info() -> str:
    lines = ["📋 *Все тарифы AxiOm VPN*\n"]
    for i, t in enumerate(config.TARIFFS):
        gb = gb_str(t)
        features = "\n".join(f"  {f}" for f in t.get("features", []))
        lines.append(
            f"*{i + 1}. {t['name']}*\n"
            f"{features}\n"
            f"  📦 Трафик: {gb}\n"
            f"  💰 Цена: *{t['price']} ₽*"
        )
    return "\n\n".join(lines)


@dp.callback_query(F.data == "tariffs_info")
async def tariffs_info_callback(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить", callback_data="buy")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
    ])
    await callback.message.edit_text(
        build_tariffs_info(),
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ── Покупка — категории ──────────────────────────────────────────────────────

BUY_MENU_TEXT = (
    "🛒 *Покупка подписки*\n\n"
    "Выбери тип тарифа:\n\n"
    "📱 *Лимит по устройствам* — безлимитный трафик, "
    "фиксированное число одновременных подключений\n\n"
    "📊 *Лимит по трафику* — без ограничения по устройствам, "
    "ограничен ежемесячный объём данных"
)


@dp.callback_query(F.data == "buy")
async def buy_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(BUY_MENU_TEXT, parse_mode="Markdown", reply_markup=buy_category_kb())


@dp.callback_query(F.data == "cat_ip")
async def buy_cat_ip(callback: types.CallbackQuery):
    tariffs = [(i, t) for i, t in enumerate(config.TARIFFS) if t.get("ip_limit", 0) > 0]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 {t['name']} — {t['price']} ₽", callback_data=f"buy_{i}")]
        for i, t in tariffs
    ] + [[InlineKeyboardButton(text="◀️ Назад", callback_data="buy")]])
    await callback.message.edit_text(
        "📱 *Тарифы с лимитом по устройствам*\n\nТрафик безлимитный, ограничено число одновременных подключений:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "cat_traffic")
async def buy_cat_traffic(callback: types.CallbackQuery):
    tariffs = [(i, t) for i, t in enumerate(config.TARIFFS) if t.get("ip_limit", 0) == 0]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 {t['name']} — {t['price']} ₽", callback_data=f"buy_{i}")]
        for i, t in tariffs
    ] + [[InlineKeyboardButton(text="◀️ Назад", callback_data="buy")]])
    await callback.message.edit_text(
        "📊 *Тарифы с лимитом по трафику*\n\nБез ограничения по числу устройств, ограничен ежемесячный объём:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ── Личный кабинет ───────────────────────────────────────────────────────────

@dp.message(Command("me"))
async def me_command(message: types.Message):
    await show_cabinet(message.from_user.id, message, edit=False)


@dp.callback_query(F.data == "cabinet")
async def cabinet_callback(callback: types.CallbackQuery):
    await show_cabinet(callback.from_user.id, callback.message, edit=True)


async def show_cabinet(user_id: int, msg, edit=False):
    subs = db.get_subscriptions(user_id)

    if not subs:
        text = (
            "👤 *Личный кабинет*\n\n"
            "У тебя пока нет подписок.\n"
            "Выбери тариф и начни пользоваться VPN!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Купить подписку", callback_data="goto_buy")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
        ])
        try:
            if edit:
                await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            else:
                await msg.answer(text, parse_mode="Markdown", reply_markup=kb)
        except TelegramBadRequest:
            pass
        return

    lines = ["👤 *Личный кабинет*\n"]
    rows = []
    shown = 0
    infos = await get_marzban_users([sub["marzban_username"] for sub in subs])
    for sub in subs:
        info = infos.get(sub["marzban_username"])

        # Подписка удалена из Marzban — не показываем её (скрываем «сироты»)
        if isinstance(info, dict) and "username" not in info:
            detail = str(info.get("detail", "")).lower()
            if info == {} or "not found" in detail:
                continue
            info = None  # иная ошибка — покажем мягко, без кнопок

        shown += 1
        if info is None:
            lines.append(f"*{shown}.* ⚠️ {sub['tariff_name']} — не удалось получить статус")
            continue

        status = info.get("status", "unknown")
        expire = info.get("expire")
        used = info.get("used_traffic") or 0
        limit = info.get("data_limit") or 0

        icon = {"active": "✅", "expired": "⏰", "limited": "🚫"}.get(status, "❌")

        used_gb = round(used / 1024 ** 3, 2)
        limit_str = f"{round(limit / 1024 ** 3)} GB" if limit else "∞"

        if expire:
            d_left = max(0, int((expire - time.time()) / 86400))
            if d_left == 0:
                expire_str = "⚠️ истекает сегодня"
            elif d_left <= 3:
                expire_str = f"⚠️ {d_left} дн."
            else:
                expire_str = f"{d_left} дн."
        else:
            expire_str = "∞"

        dev = await get_device_count(sub["marzban_username"])
        device_line = ""
        if dev is not None:
            limit_disp = dev["limit"] if dev["limit"] > 0 else "∞"
            device_line = f"\n   📱 Устройства: {dev['connected']} / {limit_disp}"

        lines.append(
            f"*{shown}.* {icon} *{sub['tariff_name']}*\n"
            f"   📊 Трафик: {used_gb} / {limit_str}\n"
            f"   📅 Осталось: {expire_str}"
            f"{device_line}"
        )
        rows.append([
            InlineKeyboardButton(text=f"🔗 Ссылка {shown}", callback_data=f"link_{sub['id']}"),
            InlineKeyboardButton(text=f"🔄 Продлить {shown}", callback_data=f"renew_{sub['id']}"),
            InlineKeyboardButton(text=f"🗑 Удалить {shown}", callback_data=f"del_{sub['id']}"),
        ])

    if shown == 0:
        text = (
            "👤 *Личный кабинет*\n\n"
            "У тебя нет активных подписок.\n"
            "Выбери тариф и начни пользоваться VPN!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Купить подписку", callback_data="goto_buy")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
        ])
        try:
            if edit:
                await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            else:
                await msg.answer(text, parse_mode="Markdown", reply_markup=kb)
        except TelegramBadRequest:
            pass
        return

    text = "\n\n".join(lines)
    rows += [
        [InlineKeyboardButton(text="🛒 Купить ещё", callback_data="goto_buy")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="cabinet")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        if edit:
            await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        else:
            await msg.answer(text, parse_mode="Markdown", reply_markup=kb)
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data == "goto_buy")
async def goto_buy(callback: types.CallbackQuery):
    await callback.message.edit_text(BUY_MENU_TEXT, parse_mode="Markdown", reply_markup=buy_category_kb())


def referral_text(user_id: int) -> str:
    link = f"https://t.me/{config.BOT_USERNAME}?start=ref_{user_id}"
    web_link = f"{config.LANDING_URL}/?ref={user_id}"
    code = db.get_or_create_referral_code(user_id)
    stats = db.referral_stats(user_id)
    pending = db.get_ref_bonus_days(user_id)
    extra = f"\n🎁 Бонусных дней в ожидании начисления: *{pending}*" if pending > 0 else ""
    return (
        "👥 *Пригласи друга — получи дни VPN*\n\n"
        f"За *каждую* оплату приглашённого (покупку и продление) "
        f"ты получишь бонусные дни к своей подписке: "
        f"*+{config.REFERRAL_BONUS_DAYS} дн.* за месячный тариф, "
        f"*+{config.REFERRAL_BONUS_DAYS_YEAR} дн.* за годовой. "
        "Друг тоже получит такой же бонус за первую покупку.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎟 *Твой реферальный код:*\n"
        f"`{code}`\n"
        "_Друг вводит его в поле промокода при покупке на сайте._\n\n"
        "🔗 *Ссылка на бота:*\n"
        f"`{link}`\n\n"
        "🌐 *Ссылка на сайт:*\n"
        f"`{web_link}`\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Приглашено: *{stats['total']}*\n"
        f"✅ Оплатили хотя бы раз: *{stats['rewarded']}*"
        f"{extra}"
    )


@dp.callback_query(F.data == "referral")
async def referral_callback(callback: types.CallbackQuery):
    await callback.message.edit_text(
        referral_text(callback.from_user.id), parse_mode="Markdown", reply_markup=back_kb())


@dp.callback_query(F.data.startswith("link_"))
async def show_link(callback: types.CallbackQuery):
    """Повторно выдаёт ссылку на подписку (если пользователь её потерял)."""
    sub_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    sub = db.get_subscription(sub_id, user_id)
    if not sub:
        await callback.answer("Подписка не найдена.", show_alert=True)
        return

    username = sub["marzban_username"]
    info = await get_marzban_user(username)
    if "username" not in info:
        await callback.answer("Не удалось получить ссылку. Попробуй позже.", show_alert=True)
        return

    sub_url = info.get("subscription_url", f"{config.MARZNESHIN_URL}/sub/{username}")
    await callback.message.answer(
        "🔗 *Ссылка на подписку*\n\n"
        f"📦 {sub['tariff_name']}\n\n"
        "━━━━━━━━━━━━━━━━\n"
        f"`{sub_url}`\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "Вставь ссылку в приложение *AxiOm* или любой совместимый клиент.",
        parse_mode="Markdown",
        reply_markup=back_kb(),
    )
    await callback.answer()


# ── Покупка ──────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("buy_"))
async def buy(callback: types.CallbackQuery):
    idx = int(callback.data.split("_")[1])
    t = config.TARIFFS[idx]
    user_id = callback.from_user.id

    if db.get_pending(user_id):
        await callback.answer("⏳ У тебя уже есть незавершённая заявка. Заверши или отмени её.", show_alert=True)
        return

    # Username для Marzban: ник Telegram как основа, fallback — user_{id}
    tg_username = callback.from_user.username
    if tg_username:
        base = re.sub(r'[^a-zA-Z0-9_]', '_', tg_username)[:20]
    else:
        base = f"user_{user_id}"
    # Marzneshin приводит username к нижнему регистру при создании — см. коммент в триале.
    username = f"{base}_{uuid.uuid4().hex[:4]}".lower()

    await callback.message.edit_text("⏳ Создаю ссылку на оплату...")
    try:
        payment_id, pay_url = await create_payment(user_id, idx, username)
    except Exception as e:
        print(f"❌ buy: не удалось создать платёж для {user_id}: {e}")
        await callback.message.edit_text(f"❌ Не удалось создать платёж: {e}", reply_markup=back_kb())
        return

    db.save_pending(user_id, username, idx, is_renewal=0, payment_id=payment_id)

    features_text = "\n".join(t.get("features", []))
    text = (
        f"📦 *{t['name']}*\n\n"
        f"{features_text}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Сумма: *{t['price']} ₽*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Нажми *«Оплатить»* и заверши оплату на странице Платёжкы.\n"
        "2️⃣ Подписка придёт автоматически.\n\n"
        "Если ссылка не пришла за минуту — нажми *«Проверить оплату»*."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
        [InlineKeyboardButton(text="📲 Оплатить по СБП", callback_data=f"sbp_{user_id}")],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_{user_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancelreq_{user_id}")],
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)


@dp.callback_query(F.data.startswith("sbp_"))
async def pay_sbp(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    pending = db.get_pending(user_id)
    if not pending or pending.get("is_renewal"):
        await callback.answer("Активной заявки нет.", show_alert=True)
        return

    idx = pending["tariff_idx"]
    username = pending["marzban_username"]
    try:
        payment_id, pay_url = await create_payment(user_id, idx, username, method="sbp")
    except Exception as e:
        print(f"❌ pay_sbp: не удалось создать СБП-платёж для {user_id}: {e}")
        await callback.answer(f"СБП сейчас недоступен: {e}", show_alert=True)
        return

    db.save_pending(user_id, username, idx, is_renewal=0, payment_id=payment_id)

    t = config.TARIFFS[idx]
    text = (
        f"📲 *Оплата по СБП*\n\n"
        f"📦 {t['name']}\n"
        f"💰 Сумма: *{t['price']} ₽*\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ Нажми *«Открыть СБП»* — отсканируй QR или выбери банк.\n"
        "2️⃣ Подписка придёт автоматически.\n\n"
        "Если ссылка не пришла за минуту — нажми *«Проверить оплату»*."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Открыть СБП", url=pay_url)],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_{user_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancelreq_{user_id}")],
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)


@dp.callback_query(F.data.startswith("check_"))
async def check_payment(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    pending = db.get_pending(user_id)
    if not pending or pending.get("is_renewal") or not pending.get("payment_id"):
        await callback.answer("Активной заявки нет.", show_alert=True)
        return

    try:
        p = await get_payment(pending["payment_id"])
    except Exception as e:
        print(f"❌ check_payment: проверка платежа {user_id}: {e}")
        await callback.answer(f"Не удалось проверить: {e}", show_alert=True)
        return

    if p.get("status") != "succeeded" or not p.get("paid"):
        await callback.answer("Оплата ещё не поступила. Если ты только что заплатил — подожди минуту.", show_alert=True)
        return

    try:
        ok, _ = await issue_subscription(user_id)
    except Exception as e:
        print(f"❌ check_payment: выдача подписки {user_id}: {e}")
        await callback.answer(f"Ошибка выдачи: {e}", show_alert=True)
        return

    if ok:
        await callback.answer("Оплата найдена ✅ Ссылка отправлена.", show_alert=True)
    else:
        await callback.answer("Заявка уже обработана.", show_alert=True)


@dp.callback_query(F.data.startswith("confirm_"))
async def confirm(callback: types.CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    user_id = int(parts[1])
    idx = int(parts[2])
    t = config.TARIFFS[idx]

    # Берём username из базы (переживает перезапуск бота)
    pending = db.get_pending(user_id)
    if not pending:
        await callback.answer("Заявка не найдена (отменена пользователем?).", show_alert=True)
        await callback.message.edit_text("⚠️ Заявка уже обработана или отменена.")
        return
    username = pending["marzban_username"]

    try:
        await callback.message.edit_text("⏳ Создаю подписку...")
        result = await create_user(username, t)

        if "detail" in result:
            raise Exception(result["detail"])

        sub_url = result.get("subscription_url", f"{config.MARZNESHIN_URL}/sub/{username}")
        gb = gb_str(t)

        db.add_subscription(user_id, username, t)
        db.delete_pending(user_id)

        # Устанавливаем лимит одновременных IP в V2IpLimit (0 = без ограничений)
        ip_limit = t.get("ip_limit", 0)
        if ip_limit and ip_limit > 0:
            await set_v2iplimit(username, ip_limit)

        # Формируем текст сообщения
        msg_text = (
            f"✅ *Оплата подтверждена!*\n\n"
            f"📦 {t['name']} | {gb} | {t['days']} дней\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🔗 *Ссылка на подписку:*\n`{sub_url}`\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Вставь ссылку в приложение *AxiOm* или любой другой совместимый клиент.\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Используй /me чтобы следить за трафиком."
        )

        await bot.send_message(
            user_id,
            msg_text,
            parse_mode="Markdown",
        )

        # Реферальная награда пригласившему + welcome-бонус покупателю + банк бонусов
        await maybe_reward_referral(user_id, referral_bonus_for(t))
        await maybe_reward_buyer(user_id, username, referral_bonus_for(t))
        await apply_pending_bonus(user_id, notify=True)

        await callback.message.edit_text(
            f"✅ *Готово!*\n\nUsername: `{username}`\nПодписка активирована.",
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"❌ confirm: ошибка создания подписки для {user_id}: {e}")
        await callback.message.edit_text(f"❌ Ошибка при создании: {e}")


# ── Продление подписки ───────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("renew_"))
async def renew_start(callback: types.CallbackQuery):
    sub_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    if db.get_pending(user_id):
        await callback.answer("⏳ У тебя уже есть заявка в обработке.", show_alert=True)
        return

    sub = db.get_subscription(sub_id, user_id)
    if not sub:
        await callback.answer("Подписка не найдена.", show_alert=True)
        return

    idx = tariff_index_for_sub(sub)
    if idx is None:
        await callback.message.edit_text(
            "⚠️ Тариф этой подписки больше недоступен для продления.\n"
            "Напиши в поддержку: @SUPPORT_BOT",
            parse_mode="Markdown", reply_markup=back_kb())
        return
    t = config.TARIFFS[idx]

    info = await get_marzban_user(sub["marzban_username"])
    if "username" not in info:
        await callback.message.edit_text(
            "⚠️ Не удалось получить данные подписки. Попробуй позже.",
            reply_markup=back_kb())
        return

    remaining = days_left(info)
    if remaining + t["days"] > MAX_TOTAL_DAYS:
        await callback.message.edit_text(
            "❌ *Продление невозможно*\n\n"
            "Суммарный срок подписки не может превышать 2 года (730 дней).\n\n"
            f"Сейчас осталось: *{remaining} дн.*\n"
            f"Этот тариф добавляет: *{t['days']} дн.* — итог превысит лимит.\n\n"
            "Дождись, пока срок уменьшится, и попробуй снова.",
            parse_mode="Markdown", reply_markup=back_kb())
        return

    features_text = "\n".join(t.get("features", []))
    text = (
        "🔄 *Продление подписки*\n\n"
        f"📦 *{t['name']}*\n"
        f"{features_text}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📅 Сейчас осталось: *{remaining} дн.*\n"
        f"➕ Продление: *{t['days']} дн.* → итого *{remaining + t['days']} дн.*\n"
        "♻️ Трафик будет обнулён\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Сумма: *{t['price']} ₽*\n\n"
        "💳 *Реквизиты для оплаты:*\n"
        f"`{config.CARD_NUMBER}`\n"
        f"Получатель: *{config.CARD_HOLDER}*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "После перевода нажми кнопку ниже 👇"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data=f"paidrenew_{user_id}_{sub_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="cabinet")],
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)


@dp.callback_query(F.data.startswith("paidrenew_"))
async def paid_renew(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    user_id = int(parts[1])
    sub_id = int(parts[2])

    if db.get_pending(user_id):
        await callback.answer("⏳ Твоя заявка уже отправлена, ожидай подтверждения.", show_alert=True)
        return

    sub = db.get_subscription(sub_id, user_id)
    if not sub:
        await callback.answer("Подписка не найдена.", show_alert=True)
        return
    idx = tariff_index_for_sub(sub)
    if idx is None:
        await callback.answer("Тариф недоступен.", show_alert=True)
        return
    username = sub["marzban_username"]

    await state.set_state(PayStates.waiting_fio)
    await state.update_data(kind="renew", idx=idx, username=username, sub_id=sub_id)
    await callback.message.edit_text(FIO_PROMPT, parse_mode="Markdown", reply_markup=fio_cancel_kb())


@dp.callback_query(F.data.startswith("confirmrenew_"))
async def confirm_renew(callback: types.CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    user_id = int(callback.data.split("_")[1])
    pending = db.get_pending(user_id)
    if not pending or not pending.get("is_renewal"):
        await callback.answer("Заявка на продление не найдена.", show_alert=True)
        return

    username = pending["marzban_username"]
    idx = pending["tariff_idx"]
    t = config.TARIFFS[idx]

    try:
        await callback.message.edit_text("⏳ Продлеваю подписку...")

        info = await get_marzban_user(username)
        if "username" not in info:
            raise Exception("пользователь не найден в Marzban")

        remaining = days_left(info)
        if remaining + t["days"] > MAX_TOTAL_DAYS:
            db.delete_pending(user_id)
            await callback.message.edit_text(
                f"❌ Продление отклонено: суммарный срок превысит 2 года "
                f"(осталось {remaining} дн. + {t['days']} дн.)."
            )
            return

        result = await renew_user(username, t)
        if "detail" in result:
            raise Exception(result["detail"])

        db.delete_pending(user_id)

        ip_limit = t.get("ip_limit", 0)
        if ip_limit and ip_limit > 0:
            await set_v2iplimit(username, ip_limit)

        sub_url = result.get("subscription_url", f"{config.MARZNESHIN_URL}/sub/{username}")
        new_left = days_left(result)

        await bot.send_message(
            user_id,
            "✅ *Подписка продлена!*\n\n"
            f"📦 {t['name']} | {gb_str(t)} | +{t['days']} дней\n"
            f"📅 Теперь осталось: *{new_left} дн.*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🔗 *Ссылка на подписку (не изменилась):*\n`{sub_url}`\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Трафик обнулён. Используй /me чтобы следить за подпиской.",
            parse_mode="Markdown",
        )

        # Оплата (продление) приглашённого → награда пригласившему за этот платёж.
        # Плюс применяем накопленные реферальные бонусы к свежепродлённой подписке.
        await maybe_reward_referral(user_id, referral_bonus_for(t))
        await apply_pending_bonus(user_id, notify=True)

        await callback.message.edit_text(
            f"✅ *Готово!*\n\nUsername: `{username}`\nПодписка продлена (+{t['days']} дн.).",
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"❌ confirm_renew: ошибка продления для {user_id}: {e}")
        await callback.message.edit_text(f"❌ Ошибка при продлении: {e}")


# ── Удаление подписки ────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("delcfm_"))
async def delete_confirmed(callback: types.CallbackQuery):
    sub_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    sub = db.get_subscription(sub_id, user_id)
    if not sub:
        await callback.answer("Подписка не найдена.", show_alert=True)
        return

    await callback.message.edit_text("⏳ Удаляю подписку...")
    try:
        st = await delete_user(sub["marzban_username"])
        if st not in (200, 404):
            raise Exception(f"Marzban HTTP {st}")
        db.delete_subscription(sub_id, user_id)
    except Exception as e:
        await callback.message.edit_text(f"❌ Не удалось удалить: {e}", reply_markup=back_kb())
        return

    await callback.answer("Подписка удалена ✅", show_alert=False)
    await show_cabinet(user_id, callback.message, edit=True)


@dp.callback_query(F.data.startswith("del_"))
async def delete_prompt(callback: types.CallbackQuery):
    sub_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    sub = db.get_subscription(sub_id, user_id)
    if not sub:
        await callback.answer("Подписка не найдена.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delcfm_{sub_id}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="cabinet")],
    ])
    await callback.message.edit_text(
        "🗑 *Удаление подписки*\n\n"
        f"📦 {sub['tariff_name']}\n\n"
        "Подписка будет *удалена безвозвратно*: "
        "VPN перестанет работать, ссылка станет недействительной.\n\n"
        "Подтвердить удаление?",
        parse_mode="Markdown", reply_markup=kb)


# ── Ввод ФИО плательщика и обработка заявок ──────────────────────────────────

async def _submit_request(message: types.Message, kind: str, idx: int, username: str, fio: str):
    """Сохраняет заявку (покупка/продление), уведомляет админов, отвечает пользователю."""
    user_id = message.from_user.id
    t = config.TARIFFS[idx]
    is_renewal = 1 if kind == "renew" else 0
    db.save_pending(user_id, username, idx, is_renewal=is_renewal, fio=fio)

    if is_renewal:
        confirm_btn = InlineKeyboardButton(text="✅ Подтвердить продление", callback_data=f"confirmrenew_{user_id}")
        title = "🔄 *Заявка на ПРОДЛЕНИЕ!*"
        days_line = f"📊 {gb_str(t)} | +{t['days']} дней"
        action = "продлит подписку"
    else:
        confirm_btn = InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"confirm_{user_id}_{idx}")
        title = "💰 *Новая заявка на оплату!*"
        days_line = f"📊 {gb_str(t)} | {t['days']} дней"
        action = "активирует подписку"

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [confirm_btn],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{user_id}")],
    ])
    await notify_admins(
        f"{title}\n\n"
        f"👤 User ID: `{user_id}`\n"
        f"🧾 ФИО плательщика: {md_escape(fio)}\n"
        f"🔑 Username: `{username}`\n"
        f"📦 {t['name']}\n"
        f"{days_line}\n"
        f"💵 {t['price']} ₽",
        admin_kb,
    )
    await message.answer(
        "⏳ *Заявка отправлена!*\n\n"
        f"🧾 Плательщик: {md_escape(fio)}\n\n"
        f"Администратор проверит платёж и {action}.\n"
        "Обычно это занимает до 15 минут.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"cancelreq_{user_id}")],
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_menu")],
        ]),
    )


@dp.message(PayStates.waiting_fio)
async def receive_fio(message: types.Message, state: FSMContext):
    fio = (message.text or "").strip()
    if not fio or fio.startswith("/"):
        await message.answer("Пожалуйста, отправьте ФИО текстом или нажмите «Отменить».")
        return
    if len(fio) < 3 or len(fio) > 100 or not any(ch.isalpha() for ch in fio):
        await message.answer("Похоже, это не ФИО. Введите имя владельца карты, например: Иван Иванович И.")
        return

    data = await state.get_data()
    await state.clear()
    kind = data.get("kind")
    idx = data.get("idx")
    username = data.get("username")
    if kind is None or idx is None or not username:
        await message.answer("Сессия истекла. Начните оформление заново через /start.")
        return

    if db.get_pending(message.from_user.id):
        await message.answer("⏳ У тебя уже есть заявка в обработке.")
        return

    await _submit_request(message, kind, idx, username, fio)


@dp.callback_query(F.data == "cancelfio")
async def cancel_fio(callback: types.CallbackQuery, state: FSMContext):
    if await state.get_state() != PayStates.waiting_fio.state:
        await callback.answer("Это действие уже неактуально.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        WELCOME_TEXT, parse_mode="Markdown",
        reply_markup=main_menu_kb(show_trial=not db.get_trial_used(callback.from_user.id)),
    )


@dp.callback_query(F.data.startswith("cancelreq_"))
async def cancel_request(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    pending = db.get_pending(user_id)
    if not pending:
        await callback.answer("Заявка уже обработана или отменена.", show_alert=True)
        return
    db.delete_pending(user_id)

    idx = pending.get("tariff_idx")
    tname = config.TARIFFS[idx]["name"] if isinstance(idx, int) and 0 <= idx < len(config.TARIFFS) else "?"
    kind = "продление" if pending.get("is_renewal") else "покупка"
    fio = pending.get("fio") or "—"
    await notify_admins(
        "🚫 *Пользователь отменил заявку*\n\n"
        f"👤 User ID: `{user_id}`\n"
        f"🧾 ФИО: {md_escape(fio)}\n"
        f"📦 {tname} ({kind})"
    )

    await callback.message.edit_text(
        "❌ *Заявка отменена.*\n\n"
        "⚠️ Если вы уже перевели деньги — вернуть их можно только по заявке "
        "в поддержку: @SUPPORT_BOT",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_menu")]
        ]),
    )


@dp.callback_query(F.data.startswith("reject_"))
async def reject_request(callback: types.CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split("_")[1])
    pending = db.get_pending(user_id)
    if not pending:
        await callback.answer("Заявка не найдена (уже обработана?).", show_alert=True)
        await callback.message.edit_text("⚠️ Заявка уже обработана или отменена.")
        return
    db.delete_pending(user_id)
    try:
        await bot.send_message(
            user_id,
            "❌ *Ваша заявка отклонена администратором.*\n\n"
            "⚠️ Если вы переводили деньги — вернуть их можно только по заявке "
            "в поддержку: @SUPPORT_BOT",
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"reject: не смог уведомить {user_id}: {e}")
    await callback.message.edit_text("🚫 Заявка отклонена, пользователь уведомлён.")


# ── Промокоды (админ) ────────────────────────────────────────────────────────

@dp.message(Command("promo_add"))
async def promo_add_cmd(message: types.Message, command: CommandObject):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    args = (command.args or "").split()
    if len(args) < 2:
        await message.answer(
            "Использование: `/promo_add КОД ПРОЦЕНТ [ЛИМИТ] [ГГГГ-ММ-ДД]`\n"
            "Пример: `/promo_add SALE20 20 100 2026-12-31`\n"
            "ЛИМИТ = 0 или пропуск — без лимита.",
            parse_mode="Markdown")
        return
    code = args[0].upper()
    if not code.isalnum():
        await message.answer("Код должен состоять только из букв и цифр.")
        return
    try:
        percent = int(args[1])
    except ValueError:
        await message.answer("Процент должен быть числом 1–100.")
        return
    if not (1 <= percent <= 100):
        await message.answer("Процент должен быть в диапазоне 1–100.")
        return
    max_uses = 0
    if len(args) >= 3:
        try:
            max_uses = int(args[2])
            if max_uses < 0:
                raise ValueError
        except ValueError:
            await message.answer("Лимит использований — целое число ≥ 0 (0 = без лимита).")
            return
    expires_at = None
    if len(args) >= 4:
        import datetime as _dt
        try:
            _dt.date.fromisoformat(args[3])
            expires_at = args[3]
        except ValueError:
            await message.answer("Дата в формате ГГГГ-ММ-ДД.")
            return
    if not db.add_promo(code, percent, max_uses, expires_at):
        await message.answer(f"Промокод `{code}` уже существует.", parse_mode="Markdown")
        return
    lim = "без лимита" if max_uses == 0 else f"{max_uses} использований"
    await message.answer(
        f"✅ Промокод создан:\n`{code}` — −{percent}%, {lim}, до {expires_at or 'бессрочно'}",
        parse_mode="Markdown")


@dp.message(Command("promo_list"))
async def promo_list_cmd(message: types.Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    promos = db.list_promos()
    if not promos:
        await message.answer("Промокодов нет.")
        return
    lines = ["📋 *Промокоды:*"]
    for p in promos:
        mu = p["max_uses"] or 0
        used = f"{p['used_count']}/{mu}" if mu else f"{p['used_count']}/∞"
        st = "✅" if p["active"] else "⛔️"
        lines.append(f"{st} `{p['code']}` — −{p['percent']}% · {used} · до {p['expires_at'] or 'бессрочно'}")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@dp.message(Command("promo_del"))
async def promo_del_cmd(message: types.Message, command: CommandObject):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Использование: `/promo_del КОД`", parse_mode="Markdown")
        return
    if db.delete_promo(code):
        await message.answer(f"🗑 Промокод `{code}` удалён.", parse_mode="Markdown")
    else:
        await message.answer(f"Промокод `{code}` не найден.", parse_mode="Markdown")


# ── Админ: просмотр и редактирование подписок ────────────────────────────────

_ST_EMOJI = {"active": "🟢", "expired": "🔴", "limited": "🟠", "disabled": "⚪️", "on_hold": "⏸"}


def _fmt_admin_card(info: dict) -> str:
    import datetime as _dt
    u = info.get("username", "?")
    status = info.get("status", "—")
    expire = info.get("expire") or 0
    if expire:
        srok = f"{days_left(info)} дн. (до {_dt.datetime.fromtimestamp(expire).strftime('%d.%m.%Y')})"
    else:
        srok = "бессрочно"
    used = (info.get("used_traffic") or 0) / 1024 ** 3
    lim = info.get("data_limit") or 0
    lim_s = f"{lim / 1024 ** 3:.1f} ГБ" if lim else "∞ (безлимит)"
    return (f"👤 `{u}`\n{_ST_EMOJI.get(status, '•')} Статус: `{status}`\n"
            f"📅 Срок: *{srok}*\n📊 Трафик: *{used:.2f} ГБ* / {lim_s}")


def _admin_user_kb(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Дни", callback_data=f"aedt|d+|{username}"),
         InlineKeyboardButton(text="➖ Дни", callback_data=f"aedt|d-|{username}")],
        [InlineKeyboardButton(text="➕ ГБ", callback_data=f"aedt|g+|{username}"),
         InlineKeyboardButton(text="➖ ГБ", callback_data=f"aedt|g-|{username}")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"asub|{username}")],
    ])


async def _show_subs_list(message: types.Message, query: str):
    try:
        data = await get_all_marzban_users(search=query, limit=20)
    except Exception as e:
        await message.answer(f"❌ Ошибка Marzban: {e}")
        return
    users = data.get("users", [])
    total = data.get("total", 0)
    if not users:
        await message.answer("Ничего не найдено. Попробуй другой запрос или /subs.")
        return
    rows = []
    for u in users:
        srok = f"{days_left(u)}д" if u.get("expire") else "∞"
        rows.append([InlineKeyboardButton(
            text=f"{_ST_EMOJI.get(u.get('status'), '•')} {u['username']} · {srok}",
            callback_data=f"asub|{u['username']}")])
    head = f"📋 Показано {len(users)} из {total}" + (f" по «{query}»" if query else "")
    await message.answer(head, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.message(Command("subs"))
async def subs_cmd(message: types.Message, command: CommandObject, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    q = (command.args or "").strip()
    if q:
        await state.clear()
        await _show_subs_list(message, q)
    else:
        await state.set_state(AdminStates.waiting_search)
        await message.answer(
            "🔎 *Подписки.* Введи username (или часть) для поиска.\n"
            "Или отправь `-`, чтобы показать первые 20.", parse_mode="Markdown")


@dp.message(AdminStates.waiting_search)
async def subs_search_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    q = (message.text or "").strip()
    await state.clear()
    await _show_subs_list(message, "" if q == "-" else q)


@dp.callback_query(F.data.startswith("asub|"))
async def subs_detail(callback: types.CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    username = callback.data.split("|", 1)[1]
    info = await get_marzban_user(username)
    if "username" not in info:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    try:
        await callback.message.edit_text(_fmt_admin_card(info), parse_mode="Markdown",
                                         reply_markup=_admin_user_kb(username))
    except Exception:
        await callback.message.answer(_fmt_admin_card(info), parse_mode="Markdown",
                                      reply_markup=_admin_user_kb(username))
    await callback.answer()


@dp.callback_query(F.data.startswith("aedt|"))
async def subs_edit_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, fld, username = callback.data.split("|", 2)
    field = "days" if fld[0] == "d" else "gb"
    sign = 1 if fld[1] == "+" else -1
    await state.set_state(AdminStates.waiting_amount)
    await state.update_data(username=username, field=field, sign=sign)
    unit = "дней" if field == "days" else "ГБ"
    op = "добавить" if sign > 0 else "убавить"
    await callback.message.answer(
        f"Сколько {unit} {op} для `{username}`? Введи положительное число "
        f"(или /start для отмены).", parse_mode="Markdown")
    await callback.answer()


@dp.message(AdminStates.waiting_amount)
async def subs_edit_amount(message: types.Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    try:
        amt = int((message.text or "").strip())
        if amt <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Нужно положительное целое число. Попробуй ещё раз или /start для отмены.")
        return
    d = await state.get_data()
    await state.clear()
    username, field, sign = d["username"], d["field"], d["sign"]
    delta = sign * amt
    kw = {"add_days": delta} if field == "days" else {"add_gb": delta}
    res = await admin_adjust_user(username, **kw)
    if res.get("error"):
        await message.answer(f"❌ {res['error']}")
        return
    unit = "дней" if field == "days" else "ГБ"
    await message.answer(f"✅ {'+' if sign > 0 else '−'}{amt} {unit} для `{username}` применено.",
                         parse_mode="Markdown")
    info = await get_marzban_user(username)
    if "username" in info:
        await message.answer(_fmt_admin_card(info), parse_mode="Markdown",
                             reply_markup=_admin_user_kb(username))


# ── Напоминания об окончании подписки ────────────────────────────────────────

REMINDER_THRESHOLDS = (3, 1)   # за сколько дней до конца напоминать
REMINDER_INTERVAL = 3600       # частота проверки (секунды)


async def _send_expiry_reminder(sub: dict, days_left: int) -> None:
    if days_left <= 0:
        when = "истекает *сегодня*"
    elif days_left == 1:
        when = "истекает *завтра* — остался 1 день"
    else:
        when = f"истекает через *{days_left} дн.*"
    text = (
        "⏰ *Подписка скоро закончится*\n\n"
        f"📦 {sub['tariff_name']}\n"
        f"📅 {when}\n\n"
        "Продли заранее, чтобы не остаться без VPN 👇"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продлить", callback_data=f"renew_{sub['id']}")],
        [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="cabinet")],
    ])
    try:
        await bot.send_message(sub["telegram_id"], text, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        print(f"reminder: не смог отправить {sub['telegram_id']}: {e}")
    await asyncio.sleep(0.05)  # лёгкий троттлинг Telegram


async def send_expiry_reminders() -> None:
    """Один проход: за 3 и 1 день до конца шлёт напоминание с кнопкой «Продлить».
    Триал исключён. Дубли исключены таблицей sent_reminders (ключ включает expire,
    поэтому после продления напоминания нового периода приходят заново)."""
    trial_code = config.TRIAL.get("code")
    subs = [s for s in db.get_all_subscriptions() if s.get("tariff_code") != trial_code]
    if not subs:
        return
    infos = await get_marzban_users([s["marzban_username"] for s in subs])
    now = time.time()
    for s in subs:
        info = infos.get(s["marzban_username"])
        if not (isinstance(info, dict) and "username" in info):
            continue  # удалён из Marzban / ошибка связи
        expire = info.get("expire") or 0
        if not expire:
            continue  # без срока (безлимит по времени)
        days_left = int((expire - now) // 86400)
        if days_left < 0:
            continue  # уже истекла
        # Достигнутые пороги (для days_left=1 это [3,1]). Помечаем все достигнутые,
        # но шлём одно сообщение. Так при пропущенном окне (бот лежал) не уйдёт
        # устаревший «через 3 дн.» — пользователь получит актуальный остаток.
        reached = [t for t in REMINDER_THRESHOLDS if days_left <= t]
        newly = False
        for t in reached:
            if db.mark_reminder_sent(s["id"], t, expire):
                newly = True
        if newly:
            await _send_expiry_reminder(s, days_left)


async def reminder_loop() -> None:
    await asyncio.sleep(15)  # дать боту подняться
    while True:
        try:
            await send_expiry_reminders()
        except Exception as e:
            print(f"❌ reminder_loop: {e}")
        await asyncio.sleep(REMINDER_INTERVAL)


WEB_PAYMENT_POLL_INTERVAL = 600   # как часто добирать застрявшие веб-оплаты (секунды)


async def web_payment_reconcile() -> None:
    """Страховка от недоставленного webhook'а: добирает веб-оплаты, застрявшие
    в pending дольше 15 мин. Сверяет статус в Платёжке и довыдаёт оплаченные
    (issue_web_subscription идемпотентен — гонку с webhook разводит CAS)."""
    for payment_id in db.get_stale_pending_web_payments(15):
        try:
            p = await get_payment(payment_id)
        except Exception as e:
            print(f"❌ reconcile: не смог проверить {payment_id}: {e}")
            continue
        status = p.get("status")
        if status == "succeeded" and p.get("paid"):
            try:
                await web_api.issue_web_subscription(payment_id)
                print(f"✅ reconcile: довыдана веб-подписка по {payment_id} (webhook не дошёл)")
            except Exception as e:
                print(f"❌ reconcile: выдача {payment_id}: {e}")
        elif status == "canceled":
            db.update_web_payment(payment_id, "canceled")


async def web_payment_poll_loop() -> None:
    await asyncio.sleep(30)  # дать боту подняться
    while True:
        try:
            await web_payment_reconcile()
        except Exception as e:
            print(f"❌ web_payment_poll_loop: {e}")
        await asyncio.sleep(WEB_PAYMENT_POLL_INTERVAL)


# ── Запуск ───────────────────────────────────────────────────────────────────

async def start_webhook_server():
    app = web.Application()
    app.router.add_post(config.PAYMENT_WEBHOOK_PATH, payment_webhook)
    web_api.register_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.PAYMENT_WEBHOOK_HOST, config.PAYMENT_WEBHOOK_PORT)
    await site.start()
    print(f"🌐 webhook Платёжкы: {config.PAYMENT_WEBHOOK_HOST}:{config.PAYMENT_WEBHOOK_PORT}{config.PAYMENT_WEBHOOK_PATH}")


async def main():
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="me",    description="Личный кабинет — трафик и подписки"),
        BotCommand(command="help",  description="Помощь и инструкция"),
    ])
    await start_webhook_server()
    asyncio.create_task(reminder_loop())
    asyncio.create_task(web_payment_poll_loop())
    print(f"🚀 {config.BOT_NAME} запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
