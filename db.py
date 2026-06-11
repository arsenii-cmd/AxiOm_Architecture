import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                first_name  TEXT,
                joined_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id      INTEGER,
                marzban_username TEXT,
                tariff_name      TEXT,
                days             INTEGER,
                gb               INTEGER,
                price            INTEGER,
                created_at       TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_payments (
                telegram_id      INTEGER PRIMARY KEY,
                marzban_username TEXT,
                tariff_idx       INTEGER,
                created_at       TEXT DEFAULT (datetime('now'))
            )
        """)
        # Сквозной счётчик номеров заказов для description в Платёжке
        conn.execute("CREATE TABLE IF NOT EXISTS order_seq (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        # Миграции
        pcols = [r[1] for r in conn.execute("PRAGMA table_info(pending_payments)").fetchall()]
        if "is_renewal" not in pcols:
            conn.execute("ALTER TABLE pending_payments ADD COLUMN is_renewal INTEGER DEFAULT 0")
        if "fio" not in pcols:
            conn.execute("ALTER TABLE pending_payments ADD COLUMN fio TEXT")
        if "payment_id" not in pcols:
            conn.execute("ALTER TABLE pending_payments ADD COLUMN payment_id TEXT")
        scols = [r[1] for r in conn.execute("PRAGMA table_info(subscriptions)").fetchall()]
        if "tariff_code" not in scols:
            conn.execute("ALTER TABLE subscriptions ADD COLUMN tariff_code TEXT")
        ucols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "trial_used" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN trial_used INTEGER DEFAULT 0")
        if "ref_bonus_days" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN ref_bonus_days INTEGER DEFAULT 0")
        # Реферальная атрибуция: одна запись на приглашённого (referred_id — PK)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referred_id INTEGER PRIMARY KEY,
                referrer_id INTEGER,
                rewarded    INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        # Бонус самому приглашённому за ПЕРВУЮ оплаченную покупку (welcome-бонус).
        # Отдельный флаг от rewarded (тот про награду рефереру). Идемпотентность выдачи.
        rcols = [r[1] for r in conn.execute("PRAGMA table_info(referrals)").fetchall()]
        if "buyer_rewarded" not in rcols:
            conn.execute("ALTER TABLE referrals ADD COLUMN buyer_rewarded INTEGER DEFAULT 0")
        # Отправленные напоминания об окончании подписки: (sub_id, threshold, expire) без повторов.
        # expire в ключе ⇒ после продления (новый expire) напоминания приходят заново.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_reminders (
                sub_id    INTEGER,
                threshold INTEGER,
                expire    INTEGER,
                sent_at   TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (sub_id, threshold, expire)
            )
        """)
        conn.commit()


def add_user(telegram_id: int, first_name: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, first_name) VALUES (?, ?)",
            (telegram_id, first_name)
        )
        conn.commit()


def user_exists(telegram_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return row is not None


def get_trial_used(telegram_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT trial_used FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return bool(row[0]) if row and row[0] else False


def set_trial_used(telegram_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET trial_used = 1 WHERE telegram_id = ?", (telegram_id,))
        conn.commit()


def next_order_number() -> int:
    """Возвращает следующий сквозной номер заказа (для description в Платёжке)."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("INSERT INTO order_seq DEFAULT VALUES")
        conn.commit()
        return cur.lastrowid


def save_pending(telegram_id: int, marzban_username: str, tariff_idx: int, is_renewal: int = 0, fio: str = None, payment_id: str = None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO pending_payments (telegram_id, marzban_username, tariff_idx, is_renewal, fio, payment_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (telegram_id, marzban_username, tariff_idx, is_renewal, fio, payment_id))
        conn.commit()


def get_pending(telegram_id: int) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM pending_payments WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()
        return dict(row) if row else {}


def delete_pending(telegram_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM pending_payments WHERE telegram_id = ?", (telegram_id,))
        conn.commit()


def add_subscription(telegram_id: int, marzban_username: str, tariff: dict):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO subscriptions (telegram_id, marzban_username, tariff_name, days, gb, price, tariff_code) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, marzban_username, tariff["name"], tariff["days"], tariff.get("gb", 0), tariff["price"], tariff.get("code"))
        )
        conn.commit()


def get_subscription(sub_id: int, telegram_id: int) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE id = ? AND telegram_id = ?",
            (sub_id, telegram_id)
        ).fetchone()
        return dict(row) if row else {}


def delete_subscription(sub_id: int, telegram_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM subscriptions WHERE id = ? AND telegram_id = ?",
            (sub_id, telegram_id)
        )
        conn.commit()


def get_subscriptions(telegram_id: int) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE telegram_id = ? ORDER BY created_at DESC",
            (telegram_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_subscriptions() -> list:
    """Все подписки всех пользователей — для фоновых напоминаний об окончании."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM subscriptions").fetchall()
        return [dict(r) for r in rows]


def mark_reminder_sent(sub_id: int, threshold: int, expire: int) -> bool:
    """Помечает напоминание (sub_id, threshold, expire) отправленным.
    True только при первой отметке — защита от повторной рассылки."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO sent_reminders (sub_id, threshold, expire) VALUES (?, ?, ?)",
            (sub_id, threshold, expire)
        )
        conn.commit()
        return cur.rowcount == 1


# ── Web-payments (оплата через лендинг) ──────────────────────────────────────

def init_web_payments():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_payments (
                payment_id       TEXT PRIMARY KEY,
                tariff_idx       INTEGER,
                marzban_username TEXT,
                status           TEXT DEFAULT 'pending',
                sub_url          TEXT,
                created_at       TEXT DEFAULT (datetime('now'))
            )
        """)
        # Миграции: токен привязки к Telegram (deep-link) и кто привязал
        wcols = [r[1] for r in conn.execute("PRAGMA table_info(web_payments)").fetchall()]
        if "claim_token" not in wcols:
            conn.execute("ALTER TABLE web_payments ADD COLUMN claim_token TEXT")
        if "claimed_by" not in wcols:
            conn.execute("ALTER TABLE web_payments ADD COLUMN claimed_by INTEGER")
        if "promo_code" not in wcols:
            conn.execute("ALTER TABLE web_payments ADD COLUMN promo_code TEXT")
        # Реферал: кто пригласил (telegram_id реферера из ?ref=<id>) и был ли уже начислен бонус
        if "referrer_id" not in wcols:
            conn.execute("ALTER TABLE web_payments ADD COLUMN referrer_id INTEGER")
        if "bonus_granted" not in wcols:
            conn.execute("ALTER TABLE web_payments ADD COLUMN bonus_granted INTEGER DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_web_claim_token ON web_payments(claim_token)")
        conn.commit()


def save_web_payment(payment_id: str, tariff_idx: int, marzban_username: str, claim_token: str = None, promo_code: str = None, referrer_id: int = None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO web_payments (payment_id, tariff_idx, marzban_username, claim_token, promo_code, referrer_id) VALUES (?, ?, ?, ?, ?, ?)",
            (payment_id, tariff_idx, marzban_username, claim_token, promo_code, referrer_id)
        )
        conn.commit()


def get_web_payment_by_token(claim_token: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM web_payments WHERE claim_token = ?", (claim_token,)
        ).fetchone()
        return dict(row) if row else {}


def claim_web_payment(payment_id: str, telegram_id: int) -> bool:
    """Атомарно закрепляет веб-оплату за telegram_id. Возвращает True, если привязка
    выполнена этим вызовом (claimed_by был пуст); False, если уже привязана ранее."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE web_payments SET claimed_by = ? WHERE payment_id = ? AND claimed_by IS NULL",
            (telegram_id, payment_id)
        )
        conn.commit()
        return cur.rowcount == 1


def mark_web_bonus_granted(payment_id: str) -> bool:
    """Атомарно помечает реферальный бонус по веб-оплате выданным. True только при первом
    вызове (bonus_granted был 0) — защита от двойного начисления (webhook + опрос статуса)."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE web_payments SET bonus_granted = 1 WHERE payment_id = ? AND bonus_granted = 0",
            (payment_id,)
        )
        conn.commit()
        return cur.rowcount == 1


def claim_web_issue(payment_id: str) -> bool:
    """Атомарно «застолбляет» выдачу подписки по веб-оплате (pending → issuing).
    True только у первого вызова — защита от гонки webhook + опрос статуса,
    когда оба одновременно зовут issue_web_subscription (второй получал 409)."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE web_payments SET status = 'issuing' WHERE payment_id = ? AND status = 'pending'",
            (payment_id,)
        )
        conn.commit()
        return cur.rowcount == 1


def get_web_payment(payment_id: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM web_payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        return dict(row) if row else {}


def get_stale_pending_web_payments(min_age_minutes: int = 15) -> list[str]:
    """payment_id веб-оплат, застрявших в pending дольше min_age_minutes.
    Страховка на случай недоставленного webhook'а — фоновый поллинг добирает их."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT payment_id FROM web_payments WHERE status = 'pending' "
            "AND created_at <= datetime('now', ?)",
            (f"-{int(min_age_minutes)} minutes",)
        ).fetchall()
        return [r[0] for r in rows]


def update_web_payment(payment_id: str, status: str, sub_url: str = None):
    with sqlite3.connect(DB_PATH) as conn:
        if sub_url:
            conn.execute(
                "UPDATE web_payments SET status = ?, sub_url = ? WHERE payment_id = ?",
                (status, sub_url, payment_id)
            )
        else:
            conn.execute(
                "UPDATE web_payments SET status = ? WHERE payment_id = ?",
                (status, payment_id)
            )
        conn.commit()


# ── Промокоды ─────────────────────────────────────────────────────────────────

def init_promos():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code       TEXT PRIMARY KEY,         -- хранится в UPPER
                percent    INTEGER NOT NULL,         -- 1..100
                max_uses   INTEGER DEFAULT 0,        -- 0 = без лимита
                used_count INTEGER DEFAULT 0,
                expires_at TEXT,                     -- 'YYYY-MM-DD' или NULL
                active     INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()


def add_promo(code: str, percent: int, max_uses: int = 0, expires_at: str = None) -> bool:
    """Создаёт промокод. False, если код с таким именем уже есть."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO promo_codes (code, percent, max_uses, expires_at) VALUES (?, ?, ?, ?)",
                (code.upper(), percent, max_uses, expires_at)
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_promo(code: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM promo_codes WHERE code = ?", (code.upper(),)
        ).fetchone()
        return dict(row) if row else {}


def list_promos() -> list:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM promo_codes ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_promo(code: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM promo_codes WHERE code = ?", (code.upper(),))
        conn.commit()
        return cur.rowcount > 0


def incr_promo_use(code: str) -> bool:
    """Атомарно засчитывает одно использование, если код активен и лимит не исчерпан.
    Возвращает True, если использование засчитано."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 "
            "WHERE code = ? AND active = 1 AND (max_uses = 0 OR used_count < max_uses)",
            (code.upper(),)
        )
        conn.commit()
        return cur.rowcount == 1


# ── Рефералы ──────────────────────────────────────────────────────────────────

def set_referrer(referred_id: int, referrer_id: int) -> bool:
    """Закрепляет, что referred_id пришёл от referrer_id. Одна атрибуция на приглашённого
    (INSERT OR IGNORE). Возвращает True, если запись создана этим вызовом."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO referrals (referred_id, referrer_id) VALUES (?, ?)",
            (referred_id, referrer_id)
        )
        conn.commit()
        return cur.rowcount == 1


def get_referral(referred_id: int) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM referrals WHERE referred_id = ?", (referred_id,)
        ).fetchone()
        return dict(row) if row else {}


def mark_referral_rewarded(referred_id: int) -> bool:
    """Атомарно помечает награду выданной. Возвращает True только при первом вызове
    (rewarded был 0) — защита от двойного начисления."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE referrals SET rewarded = 1 WHERE referred_id = ? AND rewarded = 0",
            (referred_id,)
        )
        conn.commit()
        return cur.rowcount == 1


def mark_buyer_rewarded(referred_id: int) -> bool:
    """Атомарно помечает выданным welcome-бонус самому приглашённому (за первую покупку).
    Возвращает True только при первом вызове (buyer_rewarded был 0) — защита от повтора."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE referrals SET buyer_rewarded = 1 WHERE referred_id = ? AND buyer_rewarded = 0",
            (referred_id,)
        )
        conn.commit()
        return cur.rowcount == 1


def referral_stats(referrer_id: int) -> dict:
    """Сколько всего приглашено этим пользователем и сколько из них принесли награду."""
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (referrer_id,)
        ).fetchone()[0]
        rewarded = conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND rewarded = 1", (referrer_id,)
        ).fetchone()[0]
        return {"total": total, "rewarded": rewarded}


def add_ref_bonus_days(telegram_id: int, days: int):
    """Кладёт бонусные дни в «банк» пользователя (применятся, когда будет активная подписка)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET ref_bonus_days = COALESCE(ref_bonus_days, 0) + ? WHERE telegram_id = ?",
            (days, telegram_id)
        )
        conn.commit()


def get_ref_bonus_days(telegram_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT ref_bonus_days FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return int(row[0]) if row and row[0] else 0


def consume_ref_bonus_days(telegram_id: int, days: int):
    """Списывает применённые бонусные дни из банка (не уходя ниже нуля)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET ref_bonus_days = MAX(0, COALESCE(ref_bonus_days, 0) - ?) WHERE telegram_id = ?",
            (days, telegram_id)
        )
        conn.commit()


# ── Веб-сессии (Telegram-вход на сайте axiom.DOMAIN) ─────────────────
# Личность = telegram_id (тот же аккаунт, что в боте). Сессия — случайный opaque-токен
# в cookie; хранится в БД, поэтому отдельный SESSION_SECRET не нужен.

def init_web_sessions():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_sessions (
                token       TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                expires_at  TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_websess_tg ON web_sessions(telegram_id)")
        conn.commit()


def create_web_session(token: str, telegram_id: int, ttl_days: int = 30):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO web_sessions (token, telegram_id, expires_at) "
            "VALUES (?, ?, datetime('now', ?))",
            (token, telegram_id, f"+{int(ttl_days)} days")
        )
        conn.commit()


def get_web_session(token: str) -> dict:
    """{'telegram_id': ...} если сессия валидна и не истекла, иначе {}."""
    if not token:
        return {}
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT telegram_id FROM web_sessions WHERE token = ? AND expires_at > datetime('now')",
            (token,)
        ).fetchone()
        return {"telegram_id": row[0]} if row else {}


def delete_web_session(token: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM web_sessions WHERE token = ?", (token,))
        conn.commit()


def cleanup_web_sessions():
    """Чистка протухших сессий (вызывать периодически)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM web_sessions WHERE expires_at <= datetime('now')")
        conn.commit()


# ── 2-часовой пробный доступ (bootstrap для тех, у кого нет VPN) ───────────────
# Выдаётся анонимно на 2ч; при входе через Telegram продлевается до полного триала
# и привязывается к telegram_id. status: unbound → bound.

def init_web_trials():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_trials (
                claim_token      TEXT PRIMARY KEY,
                marzban_username TEXT,
                ip               TEXT,
                telegram_id      INTEGER,
                status           TEXT DEFAULT 'unbound',
                created_at       TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_webtrial_ip ON web_trials(ip, created_at)")
        conn.commit()


def create_web_trial(claim_token: str, marzban_username: str, ip: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO web_trials (claim_token, marzban_username, ip) VALUES (?, ?, ?)",
            (claim_token, marzban_username, ip)
        )
        conn.commit()


def count_recent_trials_by_ip(ip: str, hours: int = 24) -> int:
    """Сколько 2ч-триалов выдано на этот IP за последние `hours` (анти-спам)."""
    if not ip:
        return 0
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM web_trials WHERE ip = ? AND created_at > datetime('now', ?)",
            (ip, f"-{int(hours)} hours")
        ).fetchone()[0]


def get_web_trial(claim_token: str) -> dict:
    if not claim_token:
        return {}
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM web_trials WHERE claim_token = ?", (claim_token,)
        ).fetchone()
        return dict(row) if row else {}


def bind_web_trial(claim_token: str, telegram_id: int) -> bool:
    """Атомарно привязывает 2ч-триал к telegram_id (unbound → bound). True только при
    первой привязке — защита от гонки/повторов."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE web_trials SET telegram_id = ?, status = 'bound' "
            "WHERE claim_token = ? AND status = 'unbound'",
            (telegram_id, claim_token)
        )
        conn.commit()
        return cur.rowcount == 1


# ── Реферальные коды (рефералка как промокод) ─────────────────────────────────
# Личный код юзера (code → telegram_id). Вводится в поле промо на сайте; в боте
# по-прежнему ссылка. Отличается от promo_codes (скидки): код только привязывает реферера.

# Без похожих символов (0/O, 1/I/L) — чтобы код легко диктовался/набирался.
_REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def init_referral_codes():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_codes (
                code        TEXT PRIMARY KEY,
                telegram_id INTEGER UNIQUE,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()


def get_or_create_referral_code(telegram_id: int) -> str:
    """Возвращает личный реф-код пользователя, создавая его при первом обращении."""
    import secrets
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT code FROM referral_codes WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if row:
            return row[0]
        # генерируем уникальный код (на коллизии — повтор)
        for _ in range(20):
            code = "".join(secrets.choice(_REF_ALPHABET) for _ in range(7))
            try:
                conn.execute(
                    "INSERT INTO referral_codes (code, telegram_id) VALUES (?, ?)",
                    (code, telegram_id)
                )
                conn.commit()
                return code
            except sqlite3.IntegrityError:
                continue  # коллизия по code — пробуем снова
        raise RuntimeError("не удалось сгенерировать реф-код")


def get_referrer_by_code(code: str) -> int | None:
    """telegram_id владельца реф-кода, либо None. Регистр игнорируется (коды в верхнем)."""
    if not code:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT telegram_id FROM referral_codes WHERE code = ?", (code.strip().upper(),)
        ).fetchone()
        return row[0] if row else None
