import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Обязательная переменная окружения '{name}' не задана. Проверь .env")
    return value


# ==================== ОСНОВНЫЕ НАСТРОЙКИ ====================

BOT_TOKEN = _require("BOT_TOKEN")

# @username бота — для deep-link привязки веб-покупок (t.me/<BOT_USERNAME>?start=claim_...)
BOT_USERNAME = os.getenv("BOT_USERNAME", "BUY_BOT")

ADMIN_IDS = [int(x.strip()) for x in _require("ADMIN_IDS").split(",")]

BOT_NAME = "AxiOm VPN"

WELCOME_TEXT = (
    "Серверы в 🇳🇱 Нидерландах, 🇫🇷 Франции, 🇵🇱 Польше и 🇷🇺 России.\n\n"
    "🔒 *Два протокола на выбор:*\n"
    "• *TCP / Reality* — трафик неотличим от обычного HTTPS\n"
    "• *WS / WebSocket* — туннель через Cloudflare, обходит любую блокировку\n\n"
    "🇷🇺 *Российский сервер:*\n"
    "• Международный трафик скрыт за российским IP\n"
    "• Российские сайты и сервисы работают из-за рубежа\n"
    "• Умная маршрутизация: .ru и российские IP идут напрямую, остальное — в туннель\n\n"
    "📱 *Собственные приложения:*\n"
    "• *AxiOm* — VPN-клиент со сплит-туннелированием: сам выбираешь, какие приложения идут через VPN, а какие напрямую\n"
    "• *Tejar* — форк Telegram со встроенным прокси"
)

SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "SUPPORT_USERNAME")

# ==================== ОПЛАТА ====================

CARD_NUMBER = _require("CARD_NUMBER")
CARD_HOLDER = _require("CARD_HOLDER")

# ==================== ТАРИФЫ ====================

TARIFFS = [
    # ── Стандарт — 1 месяц ──────────────────────────────────────────────────
    {
        "name":     "Стандарт — 1 месяц · 5 устройств",
        "code":     "std_1m_dev",
        "days":     30,
        "gb":       0,
        "price":    200,
        "inbounds": ["VLESS Reality"],
        "ip_limit": 5,
        "features": [
            "🌍 Серверы: NL, FR, PL",
            "🔒 Протокол: TCP / Reality",
            "♾ Безлимитный трафик",
            "🛡 WARP-режим",
            "📱 До 5 устройств одновременно",
        ],
    },
    {
        "name":     "Стандарт — 1 месяц · 210 ГБ",
        "code":     "std_1m_gb",
        "days":     30,
        "gb":       210,
        "price":    200,
        "inbounds": ["VLESS Reality"],
        "ip_limit": 0,
        "features": [
            "🌍 Серверы: NL, FR, PL",
            "🔒 Протокол: TCP / Reality",
            "📊 Трафик: 210 ГБ",
            "🛡 WARP-режим",
            "📱 Без ограничения по устройствам",
        ],
    },
    # ── Стандарт — 1 год ────────────────────────────────────────────────────
    {
        "name":     "Стандарт — 1 год · 5 устройств",
        "code":     "std_1y_dev",
        "days":     365,
        "gb":       0,
        "price":    1920,
        "inbounds": ["VLESS Reality"],
        "ip_limit": 5,
        "features": [
            "🌍 Серверы: NL, FR, PL",
            "🔒 Протокол: TCP / Reality",
            "♾ Безлимитный трафик",
            "🛡 WARP-режим",
            "📱 До 5 устройств одновременно",
            "💰 160 ₽/мес — экономия 20%",
        ],
    },
    {
        "name":     "Стандарт — 1 год · 210 ГБ/мес",
        "code":     "std_1y_gb",
        "days":     365,
        "gb":       2520,
        "price":    1920,
        "inbounds": ["VLESS Reality"],
        "ip_limit": 0,
        "features": [
            "🌍 Серверы: NL, FR, PL",
            "🔒 Протокол: TCP / Reality",
            "📊 Трафик: 210 ГБ/мес",
            "🛡 WARP-режим",
            "📱 Без ограничения по устройствам",
            "💰 160 ₽/мес — экономия 20%",
        ],
    },
    # ── Максимальный — 1 месяц ──────────────────────────────────────────────
    {
        "name":     "Максимальный — 1 месяц · 15 устройств",
        "code":     "max_1m_dev",
        "days":     30,
        "gb":       0,
        "price":    400,
        "inbounds": ["VLESS WS", "VLESS Reality"],
        "ip_limit": 15,
        "features": [
            "🌍 Серверы: NL, FR, PL + 🇷🇺 RU",
            "🔒 Протоколы: WS + TCP / Reality",
            "♾ Безлимитный трафик",
            "🛡 WARP-режим",
            "☁️ Маскировка трафика через Cloudflare",
            "🔐 Дополнительная анонимность",
            "📱 До 15 устройств одновременно",
        ],
    },
    {
        "name":     "Максимальный — 1 месяц · 1000 ГБ",
        "code":     "max_1m_gb",
        "days":     30,
        "gb":       1000,
        "price":    400,
        "inbounds": ["VLESS WS", "VLESS Reality"],
        "ip_limit": 0,
        "features": [
            "🌍 Серверы: NL, FR, PL + 🇷🇺 RU",
            "🔒 Протоколы: WS + TCP / Reality",
            "📊 Трафик: 1000 ГБ",
            "🛡 WARP-режим",
            "☁️ Маскировка трафика через Cloudflare",
            "🔐 Дополнительная анонимность",
            "📱 Без ограничения по устройствам",
        ],
    },
    # ── Максимальный — 1 год ────────────────────────────────────────────────
    {
        "name":     "Максимальный — 1 год · 15 устройств",
        "code":     "max_1y_dev",
        "days":     365,
        "gb":       0,
        "price":    3840,
        "inbounds": ["VLESS WS", "VLESS Reality"],
        "ip_limit": 15,
        "features": [
            "🌍 Серверы: NL, FR, PL + 🇷🇺 RU",
            "🔒 Протоколы: WS + TCP / Reality",
            "♾ Безлимитный трафик",
            "🛡 WARP-режим",
            "☁️ Маскировка трафика через Cloudflare",
            "🔐 Дополнительная анонимность",
            "📱 До 15 устройств одновременно",
            "💰 320 ₽/мес — экономия 20%",
        ],
    },
    {
        "name":     "Максимальный — 1 год · 1000 ГБ/мес",
        "code":     "max_1y_gb",
        "days":     365,
        "gb":       12000,
        "price":    3840,
        "inbounds": ["VLESS WS", "VLESS Reality"],
        "ip_limit": 0,
        "features": [
            "🌍 Серверы: NL, FR, PL + 🇷🇺 RU",
            "🔒 Протоколы: WS + TCP / Reality",
            "📊 Трафик: 1000 ГБ/мес",
            "🛡 WARP-режим",
            "☁️ Маскировка трафика через Cloudflare",
            "🔐 Дополнительная анонимность",
            "📱 Без ограничения по устройствам",
            "💰 320 ₽/мес — экономия 20%",
        ],
    },
]

# ==================== ПРОБНЫЙ ПЕРИОД ====================

TRIAL = {
    "name":     "Пробный период — 3 дня (Максимальный)",
    "code":     "trial_3d",
    "days":     3,
    "gb":       0,
    "price":    0,
    "inbounds": ["VLESS WS", "VLESS Reality"],
    "ip_limit": 3,
    "features": [
        "🌍 Серверы: NL, FR, PL + 🇷🇺 RU",
        "🔒 Протоколы: WS + TCP / Reality",
        "♾ Безлимитный трафик",
        "📱 До 3 устройств одновременно",
        "⏳ Срок: 3 дня",
    ],
}

# ==================== MARZBAN ====================

MARZBAN_URL      = os.getenv("MARZBAN_URL", "https://vpn.DOMAIN")
MARZBAN_USERNAME = os.getenv("MARZBAN_USERNAME", "API_USER")
MARZBAN_PASSWORD = _require("MARZBAN_PASSWORD")

# ==================== MARZNESHIN ====================

MARZNESHIN_URL      = os.getenv("MARZNESHIN_URL", "https://fr.DOMAIN")
MARZNESHIN_USERNAME = os.getenv("MARZNESHIN_USERNAME", "admin")
MARZNESHIN_PASSWORD = _require("MARZNESHIN_PASSWORD")

# ==================== V2IPLIMIT (V3IpLimit на FR) ====================

V2IPLIMIT_API_URL = os.getenv("V2IPLIMIT_API_URL", "https://fr.DOMAIN/devices")
V2IPLIMIT_API_KEY = _require("V2IPLIMIT_API_KEY")

# ==================== ПЛАТЁЖКА ====================

PAYMENT_SHOP_ID = _require("PAYMENT_SHOP_ID")
PAYMENT_SECRET  = _require("PAYMENT_SECRET")
PAYMENT_API_URL = os.getenv("PAYMENT_API_URL", "https://PAYMENT_API_HOST/v3")

PAYMENT_WEBHOOK_HOST = "127.0.0.1"
PAYMENT_WEBHOOK_PORT = int(os.getenv("PAYMENT_WEBHOOK_PORT", "8080"))
PAYMENT_WEBHOOK_PATH = "/payment/webhook"

PAYMENT_RETURN_URL = os.getenv("PAYMENT_RETURN_URL", "https://design.DOMAIN/?paid=1")
# Origin сайта design — для CORS веб-API (берётся из PAYMENT_RETURN_URL)
DESIGN_ORIGIN = PAYMENT_RETURN_URL.split("?")[0].rstrip("/")

# Привязка исходящего адреса при запросах к Платёжке (source-IP bind).
# На RU-сервере локальный трафик без fwmark уходит в NL-тоннель, а Платёжка режет
# иностранные IP → connection timeout. Бинд к RU-адресу заставляет идти напрямую.
# None — без привязки (например, для локального запуска).
PAYMENT_LOCAL_ADDR = os.getenv("PAYMENT_LOCAL_ADDR")  # None если не на RU

# ==================== РЕФЕРАЛЬНАЯ СИСТЕМА ====================

# Лендинг — для веб-реферальной ссылки вида {LANDING_URL}/?ref=<id> (см. referral_text).
LANDING_URL = os.getenv("LANDING_URL", "https://axiom.DOMAIN")

# Сколько дней начисляется пригласившему за оплату приглашённым МЕСЯЧНОГО тарифа.
REFERRAL_BONUS_DAYS = 7
# То же за ГОДОВОЙ тариф (срок тарифа ≥ 365 дней).
REFERRAL_BONUS_DAYS_YEAR = 30
