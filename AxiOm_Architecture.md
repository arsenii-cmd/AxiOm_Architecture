# AxiOm — Полная архитектура и структура

> Документ составлен на основе всех файлов проекта. Отражает реальное состояние системы.

> ⚠️ **08.06.2026 — переезд на Marzneshin завершён.** Панель теперь **Marzneshin на FR**
> (`fr.DOMAIN`), ноды — **marznode** (Xray + sing-box, протокол **Hysteria2** добавлен).
> Бот продаж и лимитер устройств переведены на новую панель; старый Marzban оставлен
> остановленным как откат. **Актуальное состояние системы — в `MARZNESHIN_STATE.md`**;
> разделы ниже про «Marzban» сохранены как описание прежней схемы и для понимания внутренних
> имён (функции `get_marzban_user`, столбец `marzban_username` намеренно не переименовывались).
> Сводка изменений — в §17 (запись 08.06.2026).

---

## 1. Экосистема и продукты

AxiOm — инфраструктура приватности. Два самостоятельных продукта, разделяющих одну инфраструктуру:

| Продукт | Суть | Аудитория |
|---------|------|-----------|
| **AxiOm VPN** | VPN-клиент (форк Hiddify) со сплит-туннелированием | Технически подготовленные пользователи |
| **Tejar** | Форк Telegram со встроенным прокси | Широкая аудитория, не знакомая с VPN |

Оба продукта работают на одних серверах, используют один протокол (VLESS) и управляются через одну панель (Marzban).

---

## 2. Серверная инфраструктура

### 2.1 Серверы

| Сервер | IP | SSH порт | Роль |
|--------|----|----------|------|
| **France (FR)** | `IP_FR` | 22 | **Панель Marzneshin** (`fr.DOMAIN`) + локальная нода marznode + боевой **V3IpLimit** (device-API). Лендинг `axiom.DOMAIN` |
| **Netherlands (NL)** | `IP_NL` | 22 | Нода marznode (WS+Reality+HY2). Бот поддержки. **Панель Marzban — остановлена-как-откат** (ещё Up). Шим `sub-redirect` для старых ссылок. WG-сервер для RU |
| **Poland (PL)** | `IP_PL` | 22 | Нода marznode (WS+Reality+HY2) |
| **Russia (RU)** | `IP_RU` | **2222** | Нода marznode (**только WS**, WireGuard → NL, умная маршрутизация .ru). **VPN-бот покупок** (`vpn-bot.service`, на Marzneshin с 08.06.2026). Дизайн-сайт `design.DOMAIN` |

> Прежняя схема (до 08.06.2026): NL — главный с панелью Marzban; FR/PL — ноды `marzban-node`;
> RU — нода через WG. Теперь панель на FR, все ноды — marznode. Подробности — `MARZNESHIN_STATE.md`.

> ⚠️ С 29.05.2026 **бот покупок (`vpn-bot.service`) работает на RU**, а не на NL. Лимит устройств он ставит по HTTP на v3iplimit (NL:7070), а не записью в файл. Подробнее — §5.1 и §6.7.

> Все серверы: BBR congestion control включён (`net.ipv4.tcp_congestion_control=bbr`, `net.core.default_qdisc=fq`).

### 2.2 DNS (Cloudflare)

Полный список A-записей домена `DOMAIN`:

| Субдомен | IP | Proxy | Назначение |
|----------|----|-------|------------|
| `DOMAIN` (корень) | `IP_PL` | ☁️ Proxied | Основной сайт — ⚠️ **origin не обслуживается** (на PL Caddy только `pl.*`), отдаёт **HTTP 525** (проверено 04.06.2026) |
| `www` | `IP_PL` | ☁️ Proxied | Сайт (www) — ⚠️ то же, **HTTP 525** |
| `vpn` | `IP_NL` | DNS only | Главная точка VPN: Marzban панель + подписки |
| `dash` | `IP_NL` | DNS only | Алиас NL сервера (дашборд) |
| `fr` | `IP_FR` | DNS only | France нода |
| `pl` | `IP_PL` | DNS only | Poland нода |
| `ru` | `IP_RU` | DNS only | Russia нода |
| `axiom` | `IP_RU` | DNS only | Лендинг AxiOm VPN — **на RU** (перенесён с FR, проверено 04.06.2026). Caddy RU: статика `/var/www/landing` + `handle /api/* → :8080` (web_api бота) |
| `design` | `IP_RU` | DNS only | Дизайн-сайт (внутренний) |
| `pay` | `IP_RU` | DNS only | Webhook Платёжкы → бот покупок (RU). Caddy на RU → `localhost:8080` |
| `cloud` | `IP_FR` | DNS only | Nextcloud/WebDAV на FR (Caddy → `:8765`, отвечает 401-auth) |

> ⚠️ `direct.DOMAIN` — запись **удалена** (NXDOMAIN, нигде не обслуживается; проверено 04.06.2026). Ранее указывала на PL.
> ⚠️ Корневой сайт `DOMAIN`/`www` отдаёт **525** — Cloudflare не может установить SSL к origin (на PL нет Caddy-блока для корня). Требует внимания.

### 2.3 Топология сети

```
Пользователь
    │
    ▼
Cloudflare (DNS)
    │
    ▼
vpn.DOMAIN ──► NL сервер (IP_NL)
                              │
                    ┌─────────┼─────────┐
                    │         │         │
                 Caddy    Marzban   support-бот
                 :443      :8000    + v3iplimit
                                    (бот покупок → RU)
                    │
          ┌─────────┼──────────┐
          │         │          │
       /vless*   /devices/*  default
       :8001      :7070       :8000
       (Xray WS) (V3IpLimit) (Marzban)
```

### 2.4 Caddy (reverse proxy на NL сервере)

```caddy
vpn.DOMAIN, dash.DOMAIN {
    handle /vless*    { reverse_proxy localhost:8001 }  # VLESS WS
    handle /vmess*    { reverse_proxy localhost:8002 }  # VMess WS
    handle /trojan*   { reverse_proxy localhost:8003 }  # Trojan WS
    handle_path /devices/* { reverse_proxy localhost:7070 }  # V3IpLimit REST API
    handle            { reverse_proxy localhost:8000 }  # Marzban
}
```

### 2.5 Russia сервер — специфика

Умная маршрутизация через WireGuard-туннель:
- `.ru` домены и российские IP идут **напрямую** (без туннеля)
- Остальной трафик → туннель NL → выход через нидерландский IP
- WireGuard: `wg0`, peer `10.0.0.1` (NL), local `10.0.0.2`
- Маршрутизация через `fwmark 100`, таблица `100`, policy routing

---

## 3. VPN-панель Marzban → Marzneshin

> ⚠️ **С 08.06.2026 панель — Marzneshin на FR** (`fr.DOMAIN`), сервисы Standard/Maximum,
> ноды marznode (Xray + sing-box/HY2). Раздел ниже описывает прежнюю Marzban-схему; актуальная
> модель панели/нод/подписок — в `MARZNESHIN_STATE.md` §2–§3.

### 3.1 Роль

Marzban управляет всеми пользователями и подписками VPN. Бот взаимодействует с ней через REST API.

**Адрес панели:** `https://vpn.DOMAIN/dashboard`  
**API пользователь:** `API_USER`

> **Telegram-логгер (@LOGGER_BOT):** встроенный бот Marzban, настроен в `/opt/marzban/.env` (`TELEGRAM_API_TOKEN`, `TELEGRAM_LOGGER_CHANNEL_ID`). Шлёт в канал события (входы админов, создание/изменение/удаление юзеров). `LOGIN_NOTIFY_WHITE_LIST = "IP_NL"` глушит уведомления об успешном входе с IP сервера (автоматизации `iplimit`/`API_USER`/`Device`). Подробнее — §17.

### 3.2 Протоколы и инбаунды

| Тег | Протокол | Порт (localhost) | Транспорт | Доступ снаружи |
|-----|----------|------------------|-----------|----------------|
| `VLESS Reality` | VLESS | direct | TCP Reality | порт 443/тип reality |
| `VLESS WS` | VLESS | 8001 | WebSocket через Caddy | `vpn.DOMAIN/vless` |
| `VMess WS` | VMess | 8002 | WebSocket через Caddy | `vpn.DOMAIN/vmess` |
| `Trojan WS` | Trojan | 8003 | WebSocket через Caddy | `vpn.DOMAIN/trojan` |

### 3.3 Подписки

Каждый пользователь получает URL подписки вида:
```
https://vpn.DOMAIN/sub/{username}
```
AxiOm импортирует этот URL — получает все серверы и протоколы автоматически.

### 3.4 Ноды

```
Marzban (NL) ──gRPC──► marzban-node (FR, :62050)
             ──gRPC──► marzban-node (PL)
             ──WireGuard──► Xray на RU (через туннель NL)
```

---

## 4. Тарифная система

Всего **8 тарифов**: Стандарт / Максимальный × Месяц / Год × 5 устройств / трафик.

| Название | Дней | Трафик | Цена | Инбаунды | IP-лимит |
|----------|------|--------|------|----------|----------|
| Стандарт — 1 мес · 5 устройств | 30 | ∞ | 200 ₽ | VLESS Reality | 5 |
| Стандарт — 1 мес · 210 ГБ | 30 | 210 ГБ | 200 ₽ | VLESS Reality | 0 (∞) |
| Стандарт — 1 год · 5 устройств | 365 | ∞ | 1920 ₽ | VLESS Reality | 5 |
| Стандарт — 1 год · 210 ГБ/мес | 365 | 2520 ГБ | 1920 ₽ | VLESS Reality | 0 (∞) |
| Максимальный — 1 мес · 15 устройств | 30 | ∞ | 400 ₽ | WS + Reality | 15 |
| Максимальный — 1 мес · 1000 ГБ | 30 | 1000 ГБ | 400 ₽ | WS + Reality | 0 (∞) |
| Максимальный — 1 год · 15 устройств | 365 | ∞ | 3840 ₽ | WS + Reality | 15 |
| Максимальный — 1 год · 1000 ГБ/мес | 365 | 12000 ГБ | 3840 ₽ | WS + Reality | 0 (∞) |

**Логика IP-лимита:** `ip_limit = 0` означает безлимит (показывается как `∞`, enforcement пропускается). Лимит записывается в `SPECIAL_LIMIT` в `/root/config.json`; если запись отсутствует — применяется `GENERAL_LIMIT`.

---

## 5. Боты Telegram

### 5.1 VPN-бот (бот покупок)

**Файл:** `bot.py`  
**Сервис:** `vpn-bot.service`  
**Сервер:** RU (`IP_RU`, SSH порт **2222**) — перенесён с NL 29.05.2026  
**Рабочая директория:** `/opt/vpn-bot/`  
**Python окружение:** venv `/opt/vpn-bot/venv/` (запуск `/opt/vpn-bot/venv/bin/python`, юнит с `PYTHONUNBUFFERED=1`)

> ⚠️ На NL юнит `vpn-bot` **удалён** (проверено 04.06.2026: `systemctl is-enabled vpn-bot` → not-found). Активен только на RU. У бота long-polling с одним `BOT_TOKEN` — два инстанса одновременно дают Telegram **409 Conflict**, поэтому при любом переносе сначала стоп старого, потом старт нового.

#### Поток покупки

> 💳 **С 31.05.2026 покупка идёт через Платёжку** (не реквизиты карты + ручное подтверждение).
> `buy_{idx}` создаёт платёж в Платёжке → пользователь платит по ссылке/СБП → webhook
> `payment.succeeded` (через `pay.DOMAIN`) или кнопка «🔄 Проверить оплату» →
> автовыдача через `issue_subscription()`. Магазин Платёжкы привязан к `design.DOMAIN`,
> в платёж уходит только `Заказ №N` — **никаких упоминаний VPN/AxiOm**. Кнопки админа
> `confirm_`/`reject_` оставлены как резерв (активно используются флоу продления).
> Детали — `PAYMENT_INTEGRATION.md`. Схема ниже описывает прежний ручной флоу (актуален
> для **продления**, которое пока на реквизитах карты + подтверждении админом).

```
/start
  │
  ▼
Главное меню
  [🎁 Получить пробный период]*  [🛒 Купить] [📋 Сравнить тарифы] [👤 Кабинет] [❓ Помощь]
  * кнопка показывается, только если users.trial_used = 0

  ├─ Получить пробный период (get_trial) ──────────────────────►
  │    Создаёт пользователя Marzban по config.TRIAL (Максимальный,
  │    3 устройства, безлимит трафика, 3 дня), ставит ip_limit=3,
  │    выдаёт ссылку, ставит trial_used=1 → кнопка исчезает.
  │    Один раз на Telegram ID. Без оплаты/подтверждения админом.
  │
  ├─ Купить ──────────────────────────────────────────────────►
  │    │
  │    ▼
  │  Выбор категории
  │    [📱 Лимит по устройствам]  [📊 Лимит по трафику]
  │    │                           │
  │    ▼                           ▼
  │  4 тарифа с ip_limit > 0    4 тарифа с ip_limit = 0
  │    (5 / 15 устройств)          (210 ГБ / 1000 ГБ)
  │    │
  │    ▼ buy_{idx}
  │  Страница тарифа + реквизиты карты
  │    │
  │    ▼ "Я оплатил(а)"
  │  Бот спрашивает ФИО владельца карты (FSM-состояние PayStates.waiting_fio)
  │  Пользователь вводит ФИО текстом  [❌ Отменить]
  │    │
  │    ▼  Заявка → pending_payments (с fio)
  │  Уведомление ВСЕМ из config.ADMIN_IDS: [✅ Подтвердить] [❌ Отклонить]
  │  Пользователю: "Заявка отправлена" [❌ Отменить заявку]
  │    │
  │    ▼ confirm_{user_id}_{idx}  (любой админ из ADMIN_IDS)
  │  1. create_user() → Marzban API
  │  2. db.add_subscription()
  │  3. await set_v2iplimit() → HTTP POST на v3iplimit (NL:7070 /api/set_limit)
  │  4. Отправляет пользователю ссылку подписки
  │
  └─ Сравнить тарифы ─────────────────────────────────────────►
       Текст со всеми 8 тарифами (характеристики и цены)
       [🛒 Купить]  [◀️ Назад]
```

#### Поток продления (добавлено 29.05.2026)

Точка входа — кнопка **«🔄 Продлить»** у каждой подписки в личном кабинете.

```
Личный кабинет → "🔄 Продлить: {tariff_name}"  (renew_{sub_id})
  │
  ▼  Продление возможно ТОЛЬКО тем же тарифом, что был куплен
  │  (тариф ищется по tariff_name в config.TARIFFS)
  │  Проверка лимита: остаток_дней + дни_тарифа ≤ 730 (2 года), иначе блок
  │
  ▼ "Я оплатил(а)"  (paidrenew_{user_id}_{sub_id})
  │  Заявка → pending_payments (is_renewal=1)
  │  Уведомление ВСЕМ из ADMIN_IDS с кнопкой "Подтвердить продление"
  │
  ▼ confirmrenew_{user_id}  (любой админ)
  │  renew_user(): GET текущего юзера → PUT expire = max(now, expire)+дни,
  │    тот же тариф (трафик/устройства/протоколы), status=active → POST /reset
  │  set_v2iplimit() (если ip_limit > 0)
  │  Ссылка подписки НЕ меняется; новая строка в subscriptions НЕ создаётся
```

> Лимит «не более 2 лет» (`MAX_TOTAL_DAYS = 730` в `bot.py`) проверяется дважды: при показе тарифа и при подтверждении админом. Трафик при продлении обнуляется (`POST /api/user/{username}/reset`).

#### Команды

| Команда | Действие |
|---------|----------|
| `/start` | Главное меню |
| `/me` | Личный кабинет (статус подписок, трафик, дни) |
| `/help` | Инструкция по подключению |
| `/subs` | **Только админы.** Просмотр/поиск всех подписок Marzban + правка ±дни/±ГБ (05.06.2026) |

#### Личный кабинет

Подписки загружаются из Marzban **пакетно и параллельно** (`get_marzban_users`: один токен + `asyncio.gather` вместо 2N последовательных `get_token`+`GET` — заметно ускоряет `/me` при нескольких подписках). Для каждой показывает:
- Статус (active / expired / limited)
- Трафик использован / лимит
- Дней осталось (с предупреждением при ≤3)

Под каждой подпиской — три кнопки: **«🔗 Ссылка N»** (повторно выдаёт ссылку подписки из Marzban — если пользователь её потерял; хендлер `link_{sub_id}`, проверяет принадлежность подписки), **«🔄 Продлить N»** (см. «Поток продления») и **«🗑 Удалить N»**. Ниже — «🛒 Купить ещё», «🔄 Обновить».

- **Скрытие «сирот» (A):** если Marzban на `GET /api/user/{username}` вернул 404 (пользователь удалён из панели), подписка в кабинете **не показывается** и кнопок к ней нет. При временной ошибке связи показывается «не удалось получить статус» без кнопок.
- **Удаление (B):** `del_{sub_id}` → подтверждение → `delcfm_{sub_id}`: `DELETE /api/user/{username}` в Marzban (200 или 404 = успех) + `db.delete_subscription()`. Затем кабинет перерисовывается. Запись в `SPECIAL_LIMIT` v3iplimit не удаляется (безвредный остаток — имени больше нет, ни с кем не совпадёт).

#### Реферальная система (добавлена 03.06.2026)

Кнопка **«🎁 Пригласить друга»** в главном меню + промо-блок в приветствии и `/help`. Числа берутся из `config` (`REFERRAL_BONUS_DAYS`, `REFERRAL_BONUS_DAYS_YEAR`), тексты не разъезжаются с логикой.

**Параметры** (`config.py`):
- `REFERRAL_BONUS_DAYS = 7` — рефереру за оплату приглашённым **месячного** тарифа.
- `REFERRAL_BONUS_DAYS_YEAR = 30` — за **годовой** тариф (срок ≥ 365 дн.).

**БД** (`bot.db`): таблица `referrals(referred_id PK, referrer_id, rewarded, created_at)` (одна привязка на приглашённого) + колонка `users.ref_bonus_days` («банк» невыданных дней реферера).

**Поток:**
1. У каждого юзера — ссылка `t.me/BUY_BOT?start=ref_<его_id>` (экран `referral_text` со статистикой).
2. **Атрибуция** (`start`): привязка пишется в `referrals` только если человек открыл бота **впервые** по ссылке (`is_new`), не на себя, реферер существует. Существующих/повторных не привязывает. Триал у приглашённого — обычный (3 дня; бонуса «за переход» нет).
3. **Награда** (`maybe_reward_referral`): вызывается после **каждой** оплаты приглашённого (покупка Платёжка, подтверждение админом, продление). Размер по тарифу платежа (`referral_bonus_for`: ≥365 дн. → 30, иначе → 7). Идемпотентность на платёж — `pending` удаляется до вызова.
4. **Начисление** (`apply_pending_bonus`): дни идут к самой долгой **активной** подписке реферера (`extend_user_expire`, кэп `MAX_TOTAL_DAYS = 730`). Нет активной → копятся в банке и применяются при его следующей покупке/продлении (просроченные подписки бонусом **не оживают** — фикс 03.06.2026).

5. **Welcome-бонус приглашённому** (`maybe_reward_buyer`): сам приглашённый за ПЕРВУЮ оплаченную покупку получает такой же бонус (7/30 дн. по тарифу) к купленной подписке. Идемпотентно (флаг `referrals.buyer_rewarded`), только в точках покупки (не продления).

**Анти-абьюз:** одна привязка на приглашённого (PK + `INSERT OR IGNORE`); самоприглашение заблокировано; награда только за реальную оплату (не за триал).

**Рефералы при оплате на САЙТЕ (добавлено 04.06.2026):** веб-покупки с лендинга тоже начисляют бонусы (как в боте). Реферер делится веб-ссылкой `https://axiom.DOMAIN/?ref=<его_id>` (показывается в экране «Пригласить друга» рядом с Telegram-ссылкой). Лендинг запоминает `?ref=<id>` в `localStorage` (переживает редирект Платёжкы) и шлёт его в `POST /api/web/payment`. На оплаченной выдаче (`issue_web_subscription` → `_grant_referral_bonus`): **покупателю** +7/30 дн. к купленной веб-подписке, **рефереру** +7/30 дн. к его подписке/в банк + уведомление в Telegram (через `apply_pending_bonus`). Ровно один раз на платёж (CAS `web_payments.bonus_granted`, защита от гонки webhook+опрос). Бесплатные покупки (промо 100%) бонус не дают (минуют `issue_web_subscription`). Самоприглашение на вебе не детектируется (покупатель анонимен), но требует реальной оплаты. Веб-покупатели в статистику `referrals` реферера не попадают (привязка по Telegram ID не создаётся).

**Реферальный КОД (добавлено 05.06.2026):** у каждого юзера личный код (`referral_codes`, 7 символов из безопасного алфавита без `0/O/1/I/L`), показывается в экране «Пригласить друга» в боте и в веб-кабинете. Друг вводит код **в поле промокода на сайте** — то же поле принимает и скидочные промокоды, и реф-коды (`web_api`: сначала `_validate_promo`, иначе `db.get_referrer_by_code`). Реф-код **скидки не даёт**, только привязывает реферера (приоритетнее `?ref`) → бонусы через `_grant_referral_bonus`. Самоприглашение блокируется для залогиненных (если `referrer_id == telegram_id` сессии). В боте код не вводится — там по-прежнему ссылка (один тап). `/api/web/promo` возвращает `type: "discount"|"referral"` для корректного сообщения на лендинге.

#### Напоминания об окончании подписки (добавлено 04.06.2026)

Фоновый цикл в процессе бота (`reminder_loop`, старт через `asyncio.create_task` в `main()`, проверка раз в час — `REMINDER_INTERVAL = 3600`). За **3 и 1 день** до конца подписки шлёт пользователю «⏰ Подписка скоро закончится» с кнопками **«🔄 Продлить»** (тот же флоу `renew_{sub_id}`) и «👤 Личный кабинет». Пороги — `REMINDER_THRESHOLDS = (3, 1)`.

- **Источник срока:** `expire` из Marzban (батч `get_marzban_users` по всем подпискам). **Триал исключён** (`tariff_code == config.TRIAL["code"]`); «сироты» (нет в Marzban) и уже истёкшие — пропускаются.
- **Без дублей:** таблица `sent_reminders(sub_id, threshold, expire)` (PK). `expire` в ключе ⇒ после продления (новый `expire`) напоминания нового периода приходят заново. При пропущенном окне (бот лежал) уходит актуальный остаток дней, а не устаревший текст.
- Ошибки отправки (пользователь заблокировал бота) ловятся и не роняют цикл.

### 5.2 Бот поддержки

**Файл:** `support_bot.py`  
**Сервис:** `support-bot.service` (на NL)  
**База:** `support.db`  
**Админы:** `support_config.ADMIN_IDS = [ADMIN_A, ADMIN_B]` (отдельный список от бота покупок; `support_bot.py` читает его как `ADMINS = set(config.ADMIN_IDS)`). Все админы получают обращения и могут отвечать.

#### Схема работы

```
Пользователь пишет боту
  │
  ▼
Сообщение пересылается всем ADMIN_IDS (в виде 3 сообщений: шапка + копия + кнопка)
Маппинг admin_msg_id → user_id сохраняется в message_map
  │
  ▼ Admin отвечает (reply) на любое из 3 сообщений
Бот находит user_id по admin_msg_id из message_map
Копирует ответ пользователю
  │
  ▼ "Закрыть обращение"
Пользователю уходит уведомление о закрытии
```

Поддерживает любые типы медиа: текст, фото, видео, файлы, голосовые.

---

## 6. V3IpLimit (форк V2IpLimit)

**Репозиторий:** `D:\Creativ\AxiOm (1)\V2IpLimit` (локально)  
**Сервис:** `v3iplimit.service`  
**Расположение на сервере:** `/opt/v3iplimit/`  
**Python окружение:** `/opt/v3iplimit/venv/`  
**WorkingDirectory:** `/root` (использует `/root/config.json`)  
**REST API порт:** `7070` (внутренний), доступен снаружи через Caddy: `/devices/*`

> **Заменяет** оригинальный `V2IpLimit` (бинарник в screen-сессии) и `devices_api.py` — один сервис делает всё.

### 6.1 Что делает

| Функция | Описание |
|---------|----------|
| **IP enforcement** | Мониторит XRay логи через WebSocket, каждые `CHECK_INTERVAL` секунд отключает пользователей при превышении лимита |
| **REST API** | Отдаёт данные о подключениях для Flutter-приложения |
| **Telegram-бот** | Пассивный мониторинг: каждые `CHECK_INTERVAL` секунд пишет отчёт, `/status` по запросу |

### 6.2 REST API эндпоинты

```
GET /api/devices?key=...
→ все активные пользователи

GET /api/devices/{username_or_token}?key=...
→ по username или subscription-токену (авто-определение)

GET /api/devices/sub/{token}?key=...
→ явный резолв по subscription-токену

POST /api/set_limit?key=...   body: {"username": "...", "limit": N}
→ устанавливает SPECIAL_LIMIT[username] в /root/config.json
   (вызывается ботом покупок с RU при подтверждении оплаты)

GET /health
→ {"status": "ok", "tracked_users": N}
```

**Авто-резолв токена** (два метода):
1. Быстрый O(1): base64-декодирование токена (username зашит внутри Marzban-токена)
2. Резервный O(N): перебор всех пользователей через Marzban API

> ✅ **Баг `_looks_like_token` исправлен и задеплоен (02.06.2026).** В `api/rest_api.py` regex теперь `^[A-Za-z0-9+/=\-_]{28,}$` (с `_`), а проверка `"_" not in value` убрана. Subscription-токены Marzban (url-safe base64 с `_`/`-`) корректно распознаются — приложение показывает реальный лимит устройств. Прод (NL) и локальный репозиторий синхронизированы.

**Пример ответа:**
```json
{
  "username": "alex_a1b2",
  "connected": 2,
  "limit": 5
}
```

> `ips` намеренно не возвращается клиенту — достаточно счётчика. Список IP доступен только через Telegram-бот `/status`.

### 6.3 Модель данных и логика подсчёта IP

`ACTIVE_USERS` наполняется в реальном времени через WebSocket к потоку логов XRay. Каждый раз когда XRay принимает соединение — обновляется временная метка для IP:

```python
ACTIVE_USERS[username].ips[ip] = time.monotonic()  # ip → время последнего появления
```

**Два TTL-окна** с разными целями:

| Окно | Значение | Назначение |
|------|----------|-----------|
| `DISPLAY_TTL` | 60 сек | REST API → приложение. IP активен если появился в последние 60 сек |
| `ENFORCE_TTL` | 240 сек | Enforcement-цикл. IP считается для проверки лимита если появился в последние 240 сек |

Устаревшие записи автоматически удаляются через 480 сек (2 × `ENFORCE_TTL`) — `ACTIVE_USERS` не очищается целиком, только стейл-записи.

**Enforcement-цикл** каждые `CHECK_INTERVAL` секунд:
1. Атомарный снимок `ACTIVE_USERS` (синхронно, до любых `await`)
2. Для каждого пользователя: берёт IP из снимка за последние 240 сек
3. Если `limit > 0` и `кол-во IP > limit` → отключает через Marzban API
4. Чистит устаревшие записи из памяти

> `limit = 0` означает **безлимит**: и в отображении (показывает `∞`), и в enforcement-цикле (проверка пропускается).

### 6.4 Конфиг (`/root/config.json`)

```json
{
  "BOT_TOKEN": "...",
  "ADMINS": [ADMIN_A],
  "PANEL_DOMAIN": "vpn.DOMAIN:443",
  "PANEL_USERNAME": "iplimit",
  "PANEL_PASSWORD": "...",
  "GENERAL_LIMIT": 100,
  "SPECIAL_LIMIT": {
    "username_abc1": 5,
    "username_def2": 15,
    "admin_user": 0
  },
  "CHECK_INTERVAL": 240,
  "IP_LOCATION": "None"
}
```

- `GENERAL_LIMIT` — лимит по умолчанию для всех не перечисленных в `SPECIAL_LIMIT`
- `SPECIAL_LIMIT` — индивидуальные лимиты; `0` = безлимит; устанавливаются ботом покупок при подтверждении оплаты
- `CHECK_INTERVAL` — интервал enforcement-цикла и Telegram-отчёта (секунды)
- `IP_LOCATION` — фильтр по стране (`"None"` = без фильтра)

Изменения подхватываются **без перезапуска** сервиса (конфиг кешируется по mtime файла).

### 6.5 Ключевые файлы на сервере

| Файл | Назначение |
|------|-----------|
| `/opt/v3iplimit/utils/types.py` | `UserType`: `ips: dict[str, float]` (ip → last_seen) |
| `/opt/v3iplimit/utils/parse_logs.py` | Парсит XRay-логи, обновляет `ACTIVE_USERS[u].ips[ip] = now` |
| `/opt/v3iplimit/utils/check_usage.py` | TTL-хелперы, атомарный снимок, enforcement-цикл |
| `/opt/v3iplimit/api/rest_api.py` | FastAPI: эндпоинты, token-резолвер, DISPLAY_TTL-фильтрация |
| `/opt/v3iplimit/utils/get_logs.py` | WebSocket-подключения к XRay (панель + ноды) |
| `/opt/v3iplimit/telegram_bot/main.py` | Telegram-бот, команда `/status` |

### 6.6 Telegram-бот команды

| Команда | Действие |
|---------|----------|
| `/start` | Список команд |
| `/status` | Текущие активные подключения и IP (ENFORCE_TTL окно) |
| `/setup` | Настройка панели (домен, логин, пароль) |
| `/country_code` | Фильтр по стране для точности подсчёта IP |
| `/add_admin` | Добавить admin по chat ID |
| `/remove_admin` | Удалить admin |
| `/admins_list` | Список adminов |
| `/backup` | Скачать config.json |

### 6.7 Интеграция с ботом покупок

Бот покупок работает на **RU**, а v3iplimit и `/root/config.json` — на **NL**. Поэтому прямая запись в файл невозможна; с 29.05.2026 лимит ставится по HTTP. При подтверждении оплаты `confirm()` вызывает `await set_v2iplimit(username, ip_limit)`:
```python
async def set_v2iplimit(username: str, ip_limit: int) -> None:
    # HTTPS POST {V2IPLIMIT_API_URL}/api/set_limit?key={V2IPLIMIT_API_KEY}
    # body: {"username": username, "limit": ip_limit}
    # V2IPLIMIT_API_URL = "https://vpn.DOMAIN/devices"  (через Caddy → localhost:7070)
    # V2IPLIMIT_API_KEY — из .env (config.py: _require("V2IPLIMIT_API_KEY"))
```
> ⚠️ С 02.06.2026 бот ходит на `set_limit` через **HTTPS-домен** `https://vpn.DOMAIN/devices` (Caddy `handle_path /devices/*` → `localhost:7070`), а не на голый `http://IP_NL:7070` — порт 7070 закрыт фаерволом (см. §6.8). Ключ больше не летит открытым текстом по интернету.

На стороне NL эндпоинт `/api/set_limit` обновляет `SPECIAL_LIMIT[username]` в `/root/config.json`. V3IpLimit подхватывает изменения автоматически (кэш конфига инвалидируется по mtime файла).

### 6.8 Безопасность

- **Порт 7070 закрыт снаружи (02.06.2026):** uvicorn слушает `127.0.0.1:7070` + iptables `DROP` на 7070 для всех, кроме localhost. Доступ только через Caddy (`/devices/*` → `localhost:7070`). Ранее слушал `0.0.0.0:7070` без фаервола — был доступен из интернета.
- API-ключ берётся из `.env` (`V2IPLIMIT_API_KEY`), передаётся query-параметром `?key=`. **Не захардкожен** в коде.
- CORS разрешён для всех Origins (GET+POST); риск низкий, т.к. API за localhost+фаерволом.
- Список IP-адресов пользователей **не передаётся** клиенту — только счётчик `connected`

---

## 8. База данных бота

**Файл:** `bot.db` (SQLite)

### Таблицы

#### `users`
| Поле | Тип | Описание |
|------|-----|----------|
| `telegram_id` | INTEGER PK | Telegram ID пользователя |
| `first_name` | TEXT | Имя |
| `joined_at` | TEXT | Дата регистрации |
| `trial_used` | INTEGER | `1` — пробный период уже получен (кнопка скрыта). Добавлено 29.05.2026 |
| `ref_bonus_days` | INTEGER | «Банк» невыданных реферальных бонусных дней (добавлено 03.06.2026, см. §5.1) |

#### `subscriptions`
| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER PK | |
| `telegram_id` | INTEGER | Кто купил |
| `marzban_username` | TEXT | Логин в Marzban (формат: `tgusername_xxxx`) |
| `tariff_name` | TEXT | Название тарифа |
| `days` | INTEGER | Срок |
| `gb` | INTEGER | Трафик (0 = безлимит) |
| `price` | INTEGER | Цена в рублях |
| `created_at` | TEXT | Дата покупки |
| `tariff_code` | TEXT | Стабильный код тарифа (`std_1m_dev` и т.п.) — привязка для продления, не зависит от названия (добавлено 29.05.2026) |

#### `pending_payments`
| Поле | Тип | Описание |
|------|-----|----------|
| `telegram_id` | INTEGER PK | Кто ожидает подтверждения |
| `marzban_username` | TEXT | Логин: новый (покупка) или существующий (продление) |
| `tariff_idx` | INTEGER | Индекс тарифа в config.TARIFFS |
| `created_at` | TEXT | |
| `is_renewal` | INTEGER | `0` — покупка, `1` — продление (добавлено 29.05.2026) |
| `fio` | TEXT | ФИО владельца карты-плательщика (вводит пользователь перед отправкой заявки) |
| `payment_id` | TEXT | ID платежа Платёжкы (для сверки webhook/«Проверить оплату», добавлено 31.05.2026) |

#### `referrals` (реферальная атрибуция, добавлено 03.06.2026 — см. §5.1)
| Поле | Тип | Описание |
|------|-----|----------|
| `referred_id` | INTEGER PK | Приглашённый (одна привязка на человека) |
| `referrer_id` | INTEGER | Кто пригласил |
| `rewarded` | INTEGER | `1` — приглашённый хоть раз оплатил (для статистики) |
| `created_at` | TEXT | |

#### `sent_reminders` (антидубль напоминаний об окончании, добавлено 04.06.2026 — см. §5.1)
| Поле | Тип | Описание |
|------|-----|----------|
| `sub_id` + `threshold` + `expire` | INTEGER (составной PK) | Одно напоминание на (подписка, порог 3/1 дн., срок). `expire` в ключе ⇒ после продления напоминания приходят заново |
| `sent_at` | TEXT | |

#### `web_payments` (веб-покупки через лендинг — см. §15, web_api.py)
| Поле | Тип | Описание |
|------|-----|----------|
| `payment_id` | TEXT PK | ID платежа Платёжкы |
| `tariff_idx` / `marzban_username` / `status` / `sub_url` / `created_at` | | Данные веб-покупки |
| `claim_token` / `claimed_by` / `promo_code` | | Deep-link привязки к Telegram (`?start=claim_…`) и применённый промокод |
| `referrer_id` | INTEGER | Telegram ID реферера из `?ref=<id>` на лендинге (для реф-бонуса, 04.06.2026) |
| `bonus_granted` | INTEGER | `1` — реферальный бонус по этой веб-оплате уже начислен (CAS-идемпотентность) |

#### `promo_codes` (промокоды — команды `/promo_*`, веб-флоу)
| Поле | Тип | Описание |
|------|-----|----------|
| `code` PK · `percent` · `max_uses` · `used_count` · `active` · `expires_at` | | Скидочные коды |

#### `referral_codes` (личные реф-коды — 05.06.2026)
| Поле | Тип | Описание |
|------|-----|----------|
| `code` | TEXT PK | Личный код (7 симв., алфавит без `0/O/1/I/L`); вводится в поле промо на сайте |
| `telegram_id` | INTEGER UNIQUE | Владелец кода (реферер) |

#### `web_sessions` (веб-авторизация через Telegram — см. web_auth.py, 05.06.2026)
| Поле | Тип | Описание |
|------|-----|----------|
| `token` | TEXT PK | Случайный opaque-токен сессии (в HttpOnly-cookie `axiom_session`) |
| `telegram_id` | INTEGER | Чей аккаунт (тот же ID, что в `users` и боте) |
| `created_at` / `expires_at` | TEXT | Создана / истекает (TTL 30 дней) |

#### `web_trials` (2-часовой пробный доступ с сайта — bootstrap, 05.06.2026)
| Поле | Тип | Описание |
|------|-----|----------|
| `claim_token` | TEXT PK | Токен триала (в cookie `axiom_trial`); при входе через Telegram продлевает 2ч→3дня |
| `marzban_username` / `ip` | | Кому выдан + IP (анти-спам: 1 на IP в сутки) |
| `telegram_id` / `status` | | К кому привязан + `unbound`→`bound` (CAS) |

#### `order_seq`
Сквозной счётчик номеров заказов (`next_order_number()`) для нейтрального `description = "Заказ №N"` в Платёжке.

---

## 9. Мобильное приложение AxiOm

**База:** форк Hiddify (Flutter/Dart)  
**Разработчик:** Claude Code (отдельная сессия)  
**applicationId (Android):** `app.axiom.vpn`  
**Версия:** `4.1.2+40102` (versionName/versionCode в `android/local.properties`)

### Репозитории / клоны на диске

| Путь | Назначение |
|------|-----------|
| `D:\Creativ\AxiOm_APP\hiddify-app` | **v1** — исходный форк (плоский список прокси, как в Hiddify) |
| `D:\Creativ\AxiOm_APP_v2\hiddify-app` | **v2** — фирменный UI выбора серверов (дропдауны + авто). Переименован из `AxiOm_APP(1)` — скобки `()` ломали Gradle-сборку на Windows |

> ⚠️ Папка проекта **не должна** содержать круглые скобки `()` в пути — batch-обёртки Gradle обрезают путь на скобке и сборка падает (`assembleRelease failed`). Поэтому клон переименован в `AxiOm_APP_v2`.

### Ключевые особенности форка

- Ребрендинг: AxiOm вместо Hiddify
- Встроенный запрос к Devices API: показывает `N / MAX устройств` в интерфейсе
- Сплит-туннелирование: выбор приложений, которые идут через VPN
- **(v2) Фирменный выбор сервера** — вместо плоского списка прокси: два выпадающих списка «Страна» и «Транспорт» + режим «Авто» (см. ниже)

### (v2) Выбор сервера: страна + транспорт + авто

Реализован поверх существующего движка singbox — **сетевой слой не тронут**, только UI поверх `changeProxy(groupTag, outboundTag)`.

- **Источник данных:** один селектор-`OutboundGroup` из подписки. Все связки «сервер × транспорт» приходят отдельными аутбаундами. Доступны только при запущенном ядре (до подключения карточка показывает «Подключитесь, чтобы выбрать сервер»).
- **Парсинг имён** (`lib/features/proxy/model/server_option.dart`): remark формата `{Страна} (Arco) [{ws|tcp}]` (regex `^(.*?)\s*\(Arco\)\s*\[(ws|tcp)\]\s*$`). `ws` → «WebSocket», `tcp` → «Reality (TCP)». Флаг страны — из имени (ISO-таблица), **не** из geo-IP (`ipinfo.countryCode` для RU с умной маршрутизацией показывает NL).
- **Виджет** (`lib/features/home/widget/server_selector_card.dart`): дропдаун «Страна» (с флагом и лучшим пингом по стране), дропдаун «Транспорт» (или лейбл, если транспорт один), строка «Пинг», кнопка обновления url-test.
- **Режим «Авто»** (первый пункт дропдауна стран, ⚡): сам гоняет url-test и переключается на сервер с минимальным `urlTestDelay`. Балансировщики Marzban (`lowest`/`balance`) при этом отбрасываются парсером — «Авто» выбирает быстрейший реальный сервер. Логика умнее серверного балансировщика (выбор по фактическому пингу клиента).
- **Старый UI заменён:** экран `proxies` (route `'proxies'`) теперь рендерит тот же `ServerSelectorCard(expanded: true)`; старый `proxy_tile.dart` больше не используется (мёртвый код, не удалён).

### Локальные порты (Hiddify-based)

| Порт | Тип | Назначение |
|------|-----|------------|
| `12334` | Смешанный (HTTP+SOCKS5) | Основной прокси для внешних приложений |
| `12337` | Direct | Прямой (обход VPN) |

Для настройки внешних программ (Radmin VPN, браузер и т.д.) использовать `127.0.0.1:12334`.

### Сборка релиза

Прод-точка входа — `lib/main_prod.dart` (без неё собирается dev-канал `lib/main.dart`). Подпись уже настроена (`android/key.properties`), Android-ядро вкоммичено как `android/app/libs/hiddify-core.aar`.

```powershell
cd "D:\Creativ\AxiOm_APP_v2\hiddify-app"
flutter pub get
dart run build_runner build --delete-conflicting-outputs   # если менялись @riverpod/freezed
flutter build apk --release --target lib/main_prod.dart      # → build\app\outputs\flutter-apk\
flutter build windows --release --target lib/main_prod.dart  # desktop (нужен make windows-prepare один раз для ядра)
```

APK с `splits{abi}`: `app-arm64-v8a-release.apk` (~93 МБ, основной), `app-release.apk` (универсальный ~280 МБ), плюс armeabi-v7a / x86_64.

### Готовые сборки

Каталог `D:\Creativ\AxiOm (1)\Apps`:

| Папка | Содержимое | Источник |
|-------|-----------|----------|
| `AxiOm_APP_v1` | `AxiOm-v1-arm64-v8a.apk` + universal | `AxiOm_APP` (старый UI) |
| `AxiOm_APP_v2` | `AxiOm-v2-arm64-v8a.apk` + universal | `AxiOm_APP_v2` (дропдауны + авто) |
| `AxiOm_APP_Windows` | `AxiOm.exe` + DLL + `data/` (portable, всю папку держать вместе) | `AxiOm_APP_v2` (Windows desktop) |
| `Tejar` | `Tejar-standalone.apk` | `D:\Creativ\TG\telegram-android` (см. §10) |

---

## 10. Tejar

**База:** форк Telegram (клиент)  
**Исходники:** `D:\Creativ\TG\telegram-android` (форк со встроенным `vpn-core`). Другие копии (`D:\Creativ\TG2`, `D:\Creativ\TGDesktop`) — не основные.  
**Сборка APK:** `TMessagesProj_AppStandalone/build/outputs/apk/afat/standalone/app.apk` (standalone, универсальный ~374 МБ).

Встроенный прокси работает на инфраструктуре AxiOm. При покупке подписки пользователь получает ссылку подписки — её можно вставить и в Tejar.

> **Примечание:** ранее бот отправлял отдельную прямую VLESS-ссылку NL сервера специально для Tejar. Это убрано (28.05.2026) — теперь выдаётся только одна ссылка подписки. `NL_HOST` в `config.py` — мёртвый код, можно удалить.

---

## 11. Схема полного потока: покупка → подключение

```
1. Пользователь → @BUY_BOT → /start
2. Нажимает "Купить" → выбирает категорию (по устройствам / по трафику)
3. Выбирает конкретный тариф → видит реквизиты карты
4. Переводит деньги → нажимает "Я оплатил" → вводит ФИО владельца карты
5. Бот уведомляет всех админов (config.ADMIN_IDS: ADMIN_A, ADMIN_B) — с ФИО, кнопки [Подтвердить] [Отклонить]
6. Любой админ нажимает "Подтвердить"  (бот на RU)
   ├─ Marzban API: POST /api/user → создаёт пользователя
   ├─ SQLite: INSERT INTO subscriptions
   └─ V3IpLimit: HTTP POST RU→NL /api/set_limit → SPECIAL_LIMIT[username] в /root/config.json
7. Пользователь получает ссылку подписки для AxiOm или любого совместимого клиента
8. Пользователь вставляет ссылку в AxiOm → подключается
9. AxiOm периодически запрашивает /devices/{username}
   └─ Показывает "2 / 5 устройств"
```

---

## 12. Деплой и операции

### Сервисы на NL сервере (`IP_NL`, SSH 22)

| Сервис | Рабочая директория | Описание |
|--------|--------------------|----------|
| `support-bot.service` | `/opt/vpn-bot/` | Бот поддержки |
| `v3iplimit.service` | `/root` (конфиг) · `/opt/v3iplimit/` (код) | IP-лимитер + REST API (:7070) + Telegram-мониторинг. **Это REST, который использует приложение** (через Caddy `/devices/*`) |

> 🧹 29.05.2026 с NL удалены мёртвые сервисы: `devices-api.service` (:8765, приложением не использовался) и `vpn-bot.service` (бот уехал на RU). Вместе с ними удалены файлы `devices_api.py`, `bot.py`, `config.py`, `db.py`, `bot.db` из `/opt/vpn-bot/`. На NL в `/opt/vpn-bot/` остались только файлы бота поддержки. Бэкап — `D:\Creativ\AxiOm (1)\BackUP\nl-20260529\`.

### Сервисы на RU сервере (`IP_RU`, SSH **2222**)

| Сервис | Рабочая директория | Описание |
|--------|--------------------|----------|
| `vpn-bot.service` | `/opt/vpn-bot/` | **VPN-бот покупок (активен)** + aiohttp-webhook Платёжкы на `127.0.0.1:8080` (за Caddy → `pay.DOMAIN/payment/webhook`). Запуск через venv `/opt/vpn-bot/venv/bin/python`, `PYTHONUNBUFFERED=1` |

> ⚠️ Активен только на RU; на NL юнит `vpn-bot` **удалён** (проверено 04.06.2026). Два инстанса одного `BOT_TOKEN` = Telegram 409.

### Управление сервисами

```bash
# NL: статус
systemctl status support-bot v3iplimit

# NL: перезапуск / логи
systemctl restart support-bot
systemctl restart v3iplimit
journalctl -u v3iplimit -f

# RU (ssh -p 2222 root@IP_RU): бот покупок
systemctl restart vpn-bot
journalctl -u vpn-bot -f
```

### Деплой файлов (с Windows)

```powershell
# VPN-бот покупок → RU (порт 2222!)
scp -P 2222 "D:/Creativ/AxiOm (1)/vpn-bot/bot.py" "D:/Creativ/AxiOm (1)/vpn-bot/config.py" root@IP_RU:/opt/vpn-bot/ ; ssh -p 2222 root@IP_RU "systemctl restart vpn-bot"

# Бот поддержки → NL
scp "D:/Creativ/AxiOm (1)/support-bot/support_bot.py" "D:/Creativ/AxiOm (1)/support-bot/support_config.py" root@IP_NL:/opt/vpn-bot/ ; ssh root@IP_NL "systemctl restart support-bot"

# V3IpLimit (IP-лимитер + REST API)
scp -r "D:/Creativ/AxiOm (1)/V2IpLimit" root@IP_NL:/opt/v3iplimit ; ssh root@IP_NL "systemctl restart v3iplimit"
```

### Marzban

```bash
cd /opt/marzban && docker compose restart   # перезапуск
cd /opt/marzban && docker compose logs -f   # логи
```

---

## 13. Секреты и конфиги

| Параметр | Значение | Где хранится |
|----------|----------|--------------|
| Админы бота покупок | `ADMIN_IDS = [ADMIN_A, ADMIN_B]` | `config.py` (RU). Им шлются заявки на оплату/продление; любой может подтвердить |
| Marzban URL | `https://vpn.DOMAIN` | `config.py` |
| Marzban user | `API_USER` | `config.py` |
| V3IpLimit API key | `V2IPLIMIT_API_KEY` | из `.env` на обоих концах: бот (`config.py`, `_require(...)`, RU) и v3iplimit (NL). Не в коде |
| V2IpLimit API URL (для бота) | `https://vpn.DOMAIN/devices` | `config.py` (`V2IPLIMIT_API_URL`, на RU). Через Caddy, не голый `:7070` |
| V3IpLimit config | `/root/config.json` | на NL сервере |
| Xray access log | `/var/lib/marzban/access.log` | на NL сервере |
| Bot DB | `/opt/vpn-bot/bot.db` | **на RU сервере** (перенесён 29.05.2026) |
| Support DB | `/opt/vpn-bot/support.db` | на NL сервере |
| Design-форма: токен бота + admin ID | в `Environment=` юнита `design-backend.service` | RU (`/etc/systemd/system/design-backend.service`). Уведомления о заявках идут на ID ADMIN_A |
| Платёжка shopId (**боевой**) | `SHOP_ID` (магазин `design.DOMAIN`) | из `.env` (RU, `PAYMENT_SHOP_ID` через `_require`). В коде не хранится |
| Платёжка секретный ключ (**боевой**) | `live_...` | из `.env` (RU, `PAYMENT_SECRET` через `_require`). В коде не хранится. Перевыпускался 03.06.2026 (старый стал невалиден → `invalid_credentials`) |
| Платёжка source-IP bind | `PAYMENT_LOCAL_ADDR = "IP_RU"` | `config.py` (RU). Привязка исходящего сокета к RU-IP при запросах к Платёжке — см. §5.1/§17 (03.06.2026). `None` для запуска вне RU |
| Платёжка webhook URL | `https://pay.DOMAIN/payment/webhook` | задаётся вручную в ЛК Платёжкы (API `/v3/webhooks` секретным ключом не работает) |
| Платёжка return_url | `https://design.DOMAIN/?paid=1` | `config.py` (RU). Нейтральный. Сайт по `?paid=1` клиентски редиректит на `axiom.DOMAIN` (Платёжка этого не видит) |

---

## 14. Исследовательские концепции

### TG Email Bridge

Концептуальный проект: использование SMTP/IMAP как транспортного туннеля для MTProto пакетов — на случай полной блокировки Telegram при сохранении доступа к email.

Схема: `[TG форк] → MTProto → base64(AES) → SMTP → [Bridge-сервер] → TCP → Telegram`

Статус: концепция, не реализована.

---

## 15. Веб-сайты

| Сайт | URL | Сервер | Файлы |
|------|-----|--------|-------|
| Лендинг AxiOm VPN | `https://axiom.DOMAIN` | **RU** (`IP_RU:2222`) — перенесён с FR (проверено 04.06.2026) | `/var/www/landing/index.html` + Caddy `handle /api/* → :8080` (web_api бота: веб-покупки) |
| Дизайн-сайт | `https://design.DOMAIN` | RU (`IP_RU:2222`) | `/var/www/design/` |
| Webhook Платёжкы | `https://pay.DOMAIN/payment/webhook` | RU (`IP_RU:2222`) | Caddy → `localhost:8080` (внутри `vpn-bot`) |
| Nextcloud/WebDAV | `https://cloud.DOMAIN` | FR (`IP_FR`) | Caddy → `:8765` (отвечает 401-auth) |

> ⚠️ На FR в Caddy остался **неиспользуемый** блок `axiom.DOMAIN` (старая копия `/var/www/landing`, ~130 КБ) — DNS на него больше не указывает.

Все сайты обслуживаются Caddy с автоматическими Let's Encrypt сертификатами.

**Деплой лендинга (теперь на RU):**
```powershell
scp -P 2222 "D:/Creativ/AxiOm (1)/Landing/AxiOm Landing.html" root@IP_RU:/var/www/landing/index.html
```

**Деплой дизайн-сайта:**
```powershell
scp -P 2222 "D:/Creativ/AxiOm (1)/Design Master (1)/index.html" "D:/Creativ/AxiOm (1)/Design Master (1)/offer.html" root@IP_RU:/var/www/design/
```

## 16. Каналы и сообщество

| Канал | Назначение |
|-------|------------|
| `@COMMUNITY` | Основное сообщество Telegram |
| `@SUPPORT_BOT` | Бот поддержки |
| `@SUPPORT_USERNAME` | Поддержка (прямой контакт) |

---

*Документ отражает состояние системы по состоянию на июнь 2026 (обновлён 04.06.2026).*

---

## 17. История изменений

### 08.06.2026 — Миграция Marzban → Marzneshin завершена

Полный переезд VPN-инфраструктуры на **Marzneshin** (подробности — `MARZNESHIN_STATE.md`):

- **Панель Marzneshin на FR** (`fr.DOMAIN`), 4 ноды **marznode** (FR/PL/NL — WS+Reality+HY2,
  RU — только WS). Добавлен протокол **Hysteria2** (UDP 9444, TLS + Salamander). Сервисы
  Standard (Reality) / Maximum (WS+Reality+HY2 +RU WS).
- **Бот продаж переведён на Marzneshin API** (cutover на RU `vpn-bot.service`): username в нижнем
  регистре, `_normalize_user` синтезирует `status`, `expire_strategy=fixed_date`, маппинг
  тариф→`service_ids` (WS→Maximum[2], иначе Standard[1]). Имена функций и столбец `marzban_username`
  сохранены. `bot.db` единоразово приведён к lowercase.
- **V3IpLimit перенесён на FR** и стал **мульти-нодовым**: `log-forwarder.service` на каждой ноде
  пушит access-логи Xray+sing-box на FR `/devices/api/ingest` → глобальный подсчёт устройств.
  Старый лимитер на NL (`v3iplimit.service` + бот `@IPLIMIT_BOT`) остановлен.
- **Непрерывность ссылок:** `sub-redirect.service` на NL (`:8011`) — старые Marzban-ссылки
  → 302 на Marzneshin (переустановка клиента не нужна). Caddy+шим на NL держать живыми.
- **Миграция юзеров:** скрипт `/opt/migrate_user.py` на NL; все живые юзеры перенесены и сверены.
- **Старый Marzban:** ноды (PL/RU) остановлены; панель на NL оставлена остановленной-как-откат
  на грейс-период. План полного вывода — в `MARZNESHIN_STATE.md` §7.

### 05.06.2026 — Aurora-фон на login/cabinet

Перенёс «живой» анимированный градиент из hero лендинга (`.hero::before` — три радиальных блоба + `blur` + `auroraDrift` 22с) на страницы `login.html` и `cabinet.html` (`body::before`), точечная сетка — поверх (`body::after`). Уважает `prefers-reduced-motion`. Чисто визуально.

### 05.06.2026 — Админ-панель подписок в боте (`/subs`)

Команда `/subs` (только для `config.ADMIN_IDS`): просмотр и редактирование всех подписок Marzban.

- **Поиск:** `/subs <часть_username>` или интерактивно (FSM `AdminStates.waiting_search`); `-` показывает первые 20. Источник — все пользователи Marzban (`GET /api/users?search=&limit=`), не только бот-таблица.
- **Карточка:** статус, срок (дни + дата), трафик использовано/лимит (`0 = ∞`).
- **Правка:** кнопки ➕/➖ Дни и ➕/➖ ГБ → ввод положительного числа (`AdminStates.waiting_amount`) → `admin_adjust_user()` правит `expire`/`data_limit` в Marzban. ГБ — только у лимитных тарифов (безлимит `0` не трогаем; лимит не уходит ≤ 0). Дни могут уводить в «истёк».
- **`bot.py`:** новые `get_all_marzban_users()`, `admin_adjust_user()`, класс `AdminStates`, хендлеры `subs_*`. Отмена правки — `/start` (сбрасывает FSM).

### 05.06.2026 — Реферальный код (рефералка как промокод)

Альтернатива/дополнение к реф-ссылкам: у каждого юзера личный реф-код, который друг вводит в поле промокода на сайте.

- **`db.py`:** таблица `referral_codes` + `get_or_create_referral_code()` (генерация уникального 7-символьного кода, алфавит без похожих символов) + `get_referrer_by_code()` (регистронезависимо).
- **`web_api.py`:** поле промокода на сайте теперь двойного назначения — `handle_create_payment` и `handle_promo` сначала проверяют скидочный промо (`_validate_promo`), иначе — реф-код (`get_referrer_by_code`) → `referrer_id` (приоритетнее `?ref`), скидки нет. `/api/web/promo` отдаёт `type`. Самоприглашение блокируется для залогиненных (по сессии).
- **`bot.py`:** в «Пригласить друга» показывается личный код (рядом со ссылками); в боте ввод кода не добавляли — там ссылка.
- **`web_auth.py`:** `/api/web/me` отдаёт `referral_code`; **`cabinet.html`** показывает блок «Пригласи друга» с кодом и копированием.
- Бонусы — через готовый `_grant_referral_bonus` (рефереру + покупателю), без изменений в начислении. Решения: без скидки другу; ввод кода на сайте, в боте — ссылка.

### 05.06.2026 — Веб-авторизация через Telegram + кабинет + 2ч-триал (Фаза 1)

Аккаунты на сайте `axiom.DOMAIN` — **только через Telegram** (Login Widget бота @BUY_BOT, в BotFather задан `/setdomain axiom.DOMAIN`). Аккаунт = существующий `telegram_id`, единый с ботом. Без паролей/почты/Google (от них отказались ради простоты).

- **`web_auth.py` (новый):** проверка подписи Login Widget (HMAC-SHA256 по токену бота), сессии в БД + HttpOnly-cookie. Эндпоинты `POST /api/web/auth/telegram`, `GET /api/web/me`, `POST /api/web/logout`.
- **2ч-триал (bootstrap для тех, у кого нет VPN дойти до Telegram):** `POST /api/web/trial` выдаёт анонимный доступ на 2 часа (`bot.create_user(expire_override=...)`, лимит 1/IP/сутки). При входе через Telegram непривязанный триал продлевается до полного `config.TRIAL` (3 дня) и привязывается к `telegram_id`; дедуп по `trial_used`.
- **`db.py`:** таблицы `web_sessions`, `web_trials` + хелперы; `bot.create_user` получил `expire_override`.
- **Фронтенд:** в навигации лендинга кнопка «Войти» (после входа → «Кабинет»); новые страницы `login.html` (виджет Telegram + блок «2 часа») и `cabinet.html` (подписки из `/api/web/me`). Caddy на RU: `try_files {path} {path}.html` для чистых URL `/login`, `/cabinet`.
- **Покупка** на сайте осталась мгновенной (мягкий гейт): вход обязателен только для триала и реф-бонусов.
- Деплой из `main`; проверено: страницы 200, миграции, выдача 2ч-триала (Marzban expire ≈ 2ч). Живой вход через виджет Telegram проверяется вручную на боевом домене. Бэкап: `/opt/vpn-bot/bak-auth-*`, `Caddyfile` в нём же.
- ⏭️ Следующие шаги (не сделано): реф-промокод (вместо/вместе с `?ref`-ссылкой), привязка реф-«первая покупка» к аккаунту.

### 04.06.2026 — Реферальные бонусы при оплате на сайте (ветка `feature/web-referral-bonus`)

Раньше реферальная программа работала только в боте; веб-покупки бонусов не давали. Теперь — как в боте (и рефереру, и покупателю).

- **Лендинг** (`/var/www/landing/index.html`, RU): на загрузке запоминает `?ref=<id>` в `localStorage` (`axiom_ref`, переживает редирект Платёжкы), `getRef()` шлёт его в `POST /api/web/payment`.
- **`web_api.py`:** `_resolve_referrer()` (валидирует `?ref` как существующего юзера бота); `referrer_id` пишется в `web_payments`; `_grant_referral_bonus(wp)` на оплаченной выдаче (`issue_web_subscription`) начисляет покупателю и рефереру 7/30 дн. (по тарифу). CAS `db.mark_web_bonus_granted` — ровно один раз (защита от гонки webhook+опрос). Всё в try/except — не ломает выдачу подписки.
- **`db.py`:** колонки `web_payments.referrer_id`, `web_payments.bonus_granted` (миграции); `save_web_payment(..., referrer_id)`; `mark_web_bonus_granted()`.
- **`bot.py`:** в экран «Пригласить друга» добавлена веб-ссылка `{LANDING_URL}/?ref=<id>` рядом с Telegram-ссылкой.
- **`config.py`:** `LANDING_URL = "https://axiom.DOMAIN"`.
- Бесплатные покупки (промо 100%) бонус не дают. Самоприглашение на вебе не детектируется (покупатель анонимен), требует реальной оплаты. ⚠️ Лендинг — вне git-репо; его правка задеплоена на RU отдельно (`scp`), локальную копию `D:\Creativ\AxiOm (1)\Landing\` синхронизировать.
- Проверено: синтаксис всех файлов, миграции, CAS-идемпотентность (True→False), хранение `referrer_id`. Деплой на прод — после merge в `main`.

### 04.06.2026 — Сверка документации с реальной системой (аудит §1–§17)

Полный проход по доку с проверкой кода (локально) и живой системы на всех 4 серверах + DNS.

**Подтверждено (совпадает):** §4 тарифы и TRIAL (`config.py` ↔ таблица); §3.1/§13 секреты через `_require`, URL'ы, webhook `127.0.0.1:8080`; RU — `vpn-bot` active/enabled, SSH 2222, BBR, WG-туннель жив, ipset `russia`/`ai_tunnel`, SSH-хардненинг, `design-backend`; NL — `support-bot`/`v3iplimit` active, Marzban running, порты 7070(localhost)/443/8000-8003, iptables DROP 7070, `config.json` 600, Caddy-бэкенды, `devices-api`/8765 убраны; §6.2 фикс токена задеплоен, `/health` ok; §8 колонки совпадают; FR/PL `marzban-node` (docker) running.

**Исправлено в доке (дрейф):**
- **§2.2/§15:** `axiom.DOMAIN` перенесён с FR на **RU** (DNS + Caddy-блок: статика + `/api/* → :8080`). На FR остался неиспользуемый блок.
- **§2.2:** `direct.DOMAIN` — запись удалена (NXDOMAIN), нигде не обслуживается.
- **§2.2/§15:** корень `DOMAIN`/`www` отдаёт **HTTP 525** (на PL нет Caddy-блока для корня) — помечено как требующее внимания.
- **§2.2/§15:** добавлен `cloud.DOMAIN` (Nextcloud/WebDAV на FR, `:8765`).
- **§8:** добавлены ранее не перечисленные таблицы (`referrals`, `sent_reminders`, `web_payments`, `promo_codes`, `order_seq`) и колонки `ref_bonus_days`, `payment_id`.
- **§5.1/§12:** уточнено — на NL юнит `vpn-bot` **удалён** (не «disabled»).

**Замечено, не чинилось:** корневой сайт `DOMAIN` (525) — отдельная задача; `NL_HOST` в `config.py` — мёртвый код (§10); на FR неиспользуемый `axiom`-блок Caddy; на RU второй peer в `wg0` без хендшейка (туннель к NL при этом жив).

### 04.06.2026 — Напоминания об окончании подписки

Фоновый `reminder_loop` (раз в час, старт через `asyncio.create_task` в `main()`): за **3 и 1 день** до конца подписки шлёт «⏰ Подписка скоро закончится» с кнопкой «🔄 Продлить» (флоу `renew_{sub_id}`) — для всех тарифов, **кроме триала** (`tariff_code == TRIAL["code"]`). Срок берётся из Marzban (`expire`, батч `get_marzban_users`). Дубли исключает таблица `sent_reminders(sub_id, threshold, expire)` (PK с `expire` ⇒ после продления напоминания приходят заново). Сироты/истёкшие пропускаются; ошибки отправки не роняют цикл. БД: новая таблица + `get_all_subscriptions`/`mark_reminder_sent`. Подробности — §5.1 «Напоминания об окончании подписки». Бэкап: `bak-reminders-20260604-100233`.

### 04.06.2026 — Аудит на «подобные баги» + таймауты на HTTP-вызовы

По итогам фикса Платёжкы (03.06) проверена вся система бота покупок на два класса багов:

- **Маршрут (российский хост уходит в NL-тоннель):** проверены все исходящие хосты. Единственный «российский и обязан идти напрямую» — `PAYMENT_API_HOST` (уже исправлен). Остальное — `vpn.DOMAIN` (Marzban, V2IpLimit → NL) и `api.telegram.org` (заграница), для них тоннель корректен. **Других вызовов этого класса нет.**
- **Зависание (сессия без таймаута):** ⚠️ найдено. Все вызовы Marzban и V2IpLimit использовали голый `aiohttp.ClientSession()` без таймаута (дефолт aiohttp — `total = 5 мин`). При падении NL-тоннеля (см. §17, баг WireGuard) `/me`, покупка, триал, подтверждение, продление, выдача ссылки, удаление висели бы до 5 мин на каждом запросе. `get_token` зовётся на каждой операции Marzban → максимальный радиус поражения.
  - **Фикс:** общий хелпер `_http(headers=None)` → `ClientSession(timeout=ClientTimeout(total=20))`; им заменены 8 голых сессий в `bot.py` (`set_v2iplimit`, `get_token`, `create_user`, `get_marzban_user`, `get_marzban_users`, `delete_user`, `renew_user`, `extend_user_expire`). Платёжка не тронута (свой `_payment_session()` с таймаутом + bind). Веб-покупки покрыты — `web_api` переиспользует эти функции. Теперь при недоступности бэкенда хендлер падает за 20с с ошибкой, а не зависает.
  - Бэкап: `bak-timeouts-20260604-094459`.
- **Закрыто (04.06.2026):** `get_token()` теперь при не-JSON/ошибке Marzban (502/HTML в окно рестарта) бросает понятное исключение «Marzban не выдал токен (HTTP …)» вместо `KeyError`/падения парсинга. Ошибки платёжных хендлеров (`buy`, `pay_sbp`, `check_payment` ×2, `confirm`, `confirm_renew`) теперь пишутся в журнал (`print`), а не только в чат — диагностика больше не вслепую.

### 03.06.2026 — Кабинет, реферальная система и фикс оплаты Платёжкы

Все правки на боте покупок (RU, `/opt/vpn-bot/`), деплой по `scp` + `systemctl restart vpn-bot`. Бэкапы файлов/`.env`/`bot.db` снимались перед каждым шагом (`bak-*` в `/opt/vpn-bot/`).

**Личный кабинет:**
- **Кнопка «🔗 Ссылка N»** — повторная выдача ссылки подписки из Marzban (хендлер `link_{sub_id}`, проверка принадлежности через `db.get_subscription(sub_id, user_id)`). На случай, если пользователь потерял ссылку.
- **Ускорение `/me`:** подписки грузятся пакетно/параллельно `get_marzban_users` (один токен + `asyncio.gather`) вместо 2N последовательных `get_token`+`GET`. Логика скрытия «сирот» (404) и мягких ошибок сохранена.

**Реферальная система (см. §5.1 «Реферальная система»):**
- Кнопка «🎁 Пригласить друга», ссылка `?start=ref_<id>`, промо-блок в приветствии и `/help`.
- БД: таблица `referrals` + `users.ref_bonus_days` (миграция в `init_db`, идемпотентна).
- Награда рефереру за **каждую** оплату приглашённого; размер по тарифу: месяц → 7 дн., год → 30 дн. (`referral_bonus_for`). Начисляется к активной подписке или в банк (кэп 730 дн.).
- Бонус «за переход» (увеличенный триал новичку) **убран** — у приглашённого обычный триал 3 дня.
- Фикс: бонус идёт только к **активной** подписке реферера (раньше мог «оживить» просроченную).
- Анти-абьюз: одна привязка на приглашённого, только новые юзеры, не на себя, награда лишь за реальную оплату.

**🐛 Фикс оплаты Платёжкы (бот не создавал ссылку на оплату):**
- Симптом — `❌ Не удалось создать платёж` + долгое ожидание. Реальная причина — **connection timeout** к `PAYMENT_API_HOST` (скриншот `invalid_credentials` из браузера был ложным следом — это неавторизованный GET).
- Корень: на RU умная маршрутизация заворачивала локально-сгенерированный трафик бота к российским IP Платёжкы **в NL-тоннель** (выбор source-адреса цеплял IP тоннеля `10.0.0.2`), запрос выходил с нидерландского IP → Платёжка режет иностранные IP → таймаут. Прямой путь (бинд к `IP_RU`) отвечал за 70 мс (HTTP 401).
- **Фикс (только код, прод-сеть/iptables не трогались):** все запросы к Платёжке идут через `_payment_session()` с **привязкой исходящего адреса** к RU-IP (`config.PAYMENT_LOCAL_ADDR = "IP_RU"`) + таймаут 20с (чтобы падать быстро, а не висеть 5 мин). Поправлено в `bot.py` и `web_api.py`.
- Параллельно обновлён **секретный ключ Платёжкы** в `.env` (старый стал невалиден). Проверка: `GET /v3/payments` через `_payment_session()` → HTTP 200 (сеть + auth ок).
- ⚠️ Более фундаментальное решение — поправить маршрут локального трафика к `.ru` на сервере (чтобы бинд не требовался); осознанно НЕ делалось (вмешательство в прод-сеть RU, риск оборвать VPN).

### 02.06.2026 (вечер) — Харденинг SSH, обновление ядра и фикс WireGuard-туннеля RU

По итогам аудита уязвимостей на всех 4 серверах:

- **SSH-харденинг (все 4):** отключена авторизация по паролю (`PasswordAuthentication no`, `KbdInteractiveAuthentication no`) — был активный брутфорс на RU (`45.198.224.92`) и PL (`161.35.17.235`, 22k попыток). `PermitRootLogin` → `prohibit-password` (root только по ключу), `X11Forwarding no`. Задано через `/etc/ssh/sshd_config.d/99-hardening.conf` + правка основного конфига; бэкапы `sshd_config.bak.*`. ⚠️ **Вход теперь ТОЛЬКО по ключу `axiom-server`** — обязателен бэкап приватного ключа, иначе доступ лишь через консоль хостинга.
- **Права на секреты:** `/opt/marzban/.env` (NL) и `/opt/vpn-bot/bot.db`+`bot.py` (RU) переведены с `644` на `600` (были мир-читаемы).
- **Обновление ядра (FR/PL/RU):** серверы работали на старых ядрах (`unattended-upgrades` ставит патчи, но ядро грузится только после ребута). Перезагружены по очереди → все на `6.8.0-117`. NL уже был свежим.
- **🐛 Критичный баг WireGuard на RU (выявлен и исправлен при ребуте):** в `/etc/wireguard/wg0.conf` (PostUp/PostDown) и в `/usr/local/sbin/axiom-route-guard.sh` был **жёстко зашит интерфейс `eth2`**. После ребута на новом ядре NIC переименовался в `eth0` → `wg-quick@wg0` падал с `Cannot find device "eth2"` → туннель RU↔NL не поднимался (иностранный трафик клиентов вставал). Причина нестабильности имени: в netplan `50-cloud-init.yaml` имя пиннилось по **неверному MAC** (`fa:16:3e:a9:59:c7` вместо реального `fa:16:3e:8c:c2:fb`).
  - **Фикс:** `eth2`→`eth0` в `wg0.conf` и `route-guard.sh`; создан `/etc/systemd/network/10-pin-eth0.link` (пиннинг `eth0` по реальному MAC, `update-initramfs -u`). Контрольным ребутом подтверждено: имя стабильно `eth0`, `wg-quick` поднимается сам, egress иностранного трафика = `IP_NL` (NL), `ip rule`/`ipset russia` восстанавливаются, дублей правил нет. Бэкапы: `wg0.conf.bak.*`, `axiom-route-guard.sh.bak.*`.
- **Проверено — НЕ уязвимости:** `*:1080`/`*:8443` на всех серверах — это инбаунды Xray (VPN, защищены протокольной авторизацией; открытого SOCKS-релея нет). По одному SSH-ключу на сервер (бэкдоров нет), нет лишних shell-юзеров/SUID, `unattended-upgrades` активен везде.
- **Осознанно НЕ трогалось:** iptables-фаервол на ноды FR/PL/RU (Xray использует множество динамических UDP-портов — риск оборвать VPN); Marzban на `:latest` (не запинён) — кандидаты на отдельную задачу.

### 02.06.2026 — Закрытие Device API + деплой фикса токена + синхронизация исходников

По итогам аудита (инфраструктура + код):
- **V3IpLimit 7070 закрыт снаружи:** uvicorn переведён на `127.0.0.1:7070`, добавлено iptables-правило `DROP` на 7070 для всех, кроме localhost. Ранее слушал `0.0.0.0:7070` без фаервола — REST API (включая `POST /api/set_limit`) был доступен из интернета по статическому ключу. Доступ остался только через Caddy `/devices/*`.
- **Бот покупок (RU) переведён на HTTPS-путь:** `V2IPLIMIT_API_URL = "https://vpn.DOMAIN/devices"` (через Caddy → `localhost:7070`) вместо голого `http://IP_NL:7070`. Ключ больше не передаётся открытым текстом по интернету. Проверено: `/devices/health` отвечает с RU.
- **Фикс `_looks_like_token` задеплоен на NL:** regex `^[A-Za-z0-9+/=\-_]{28,}$` (с `_`), проверка `"_" not in value` убрана. Токены с `_` распознаются, приложение показывает реальный лимит устройств (а не «100»).
- **Синхронизированы локальные исходники с продом** (раньше правки делались только на серверах → деплой откатывал бы их): `config.py` (`V2IPLIMIT_API_URL`) и `V2IpLimit/api/rest_api.py` (regex + логика токена) приведены к боевому состоянию.
- **Секреты:** подтверждено, что `BOT_TOKEN`, `MARZBAN_PASSWORD`, `PAYMENT_SECRET`, `V2IPLIMIT_API_KEY` читаются из `.env` (`_require`), а не захардкожены. §13 актуализирован.
- **Замечено (не чинилось):** swap=0 на всех серверах (PL/RU по памяти впритык); Marzban на `:latest` (не запинён); потенциальная гонка двойной выдачи в `issue_subscription` (webhook + кнопка «Проверить оплату») — само-залечивается, но строго стоило бы атомарно «застолбить» pending.

### 31.05.2026 — Интеграция Платёжкы (оплата покупки) + СБП

- **Покупка переведена на Платёжку** (бот покупок на RU). `buy_{idx}` создаёт платёж и шлёт
  ссылку оплаты; выдача — автоматически по webhook `payment.succeeded` или по кнопке
  «🔄 Проверить оплату» (встречный `GET` статуса). Ручное подтверждение админом
  (`confirm_`/`reject_`) оставлено резервом; **продление пока на прежнем ручном флоу**
  (реквизиты карты + подтверждение).
- **Приватность:** магазин Платёжкы привязан к `design.DOMAIN` (логотипы). В платёж
  уходит только `description = "Заказ №N"` (сквозной счётчик `order_seq`), нейтральный
  `return_url = https://design.DOMAIN/?paid=1` (нейтральный параметр; сайт по нему
  клиентски редиректит на `axiom.DOMAIN` — Платёжка этого не видит), `metadata` (служебная).
  Чек 54-ФЗ не отправляется. **Никаких упоминаний VPN/AxiOm в данных Платёжкы** (проверено).
- **Инфраструктура (RU):** aiohttp-webhook внутри `bot.py` на `127.0.0.1:8080`; новый поддомен
  `pay.DOMAIN` (A → RU, Caddy-блок + Let's Encrypt) → `localhost:8080`. Минцифры-серты
  **не требуются** (RU штатно доверяет `PAYMENT_API_HOST`). SDK Платёжкы **не используется** —
  прямые запросы через `aiohttp.BasicAuth`, новых зависимостей нет.
- **СБП:** кнопка «📲 Оплатить по СБП» (`payment_method_data.type=sbp`, QR рисует Платёжка).
  На боевом магазине доступна (проверено); на тестовом была `Payment method is not available`
  (кнопка деградирует мягко).
- **БД:** `pending_payments.payment_id`, таблица `order_seq` + `next_order_number()`.
- **Webhook регистрируется вручную в ЛК** (`/v3/webhooks` секретным ключом недоступен).
- **Статус (01.06.2026): БОЕВОЙ режим** — магазин `SHOP_ID` (live), карта и СБП доступны,
  платежи реальные. Остался ручной шаг: прописать webhook в ЛК боевого магазина.
  Подробности и чеклист — `PAYMENT_INTEGRATION.md`.
- Бэкап прежней версии бота: `/opt/vpn-bot/bak-20260531-111505` (на RU).

### 30.05.2026 — Мобильное приложение v2: фирменный выбор сервера + сборки

- **Создан клон `AxiOm_APP_v2`** (из `AxiOm_APP(1)`; скобки `()` в пути ломали Gradle на Windows → переименован).
- **Новый UI выбора сервера** вместо плоского списка прокси Hiddify (§9):
  - `lib/features/proxy/model/server_option.dart` — парсер remark `{Страна} (Arco) [ws|tcp]`, флаги по ISO-таблице, лейблы транспорта, поиск быстрейшего (`fastest`).
  - `lib/features/home/widget/server_selector_card.dart` — карточка с дропдаунами «Страна»/«Транспорт», пингом, кнопкой url-test. Встроена в `home_page.dart` (между статами и SplitTunnelingCard).
  - Режим **«Авто» (⚡, самый быстрый)** — автотест пинга + автопереключение на минимальный `urlTestDelay`. Балансировщики `lowest`/`balance` отбрасываются (Авто выбирает реальный сервер по факт. пингу клиента).
  - Экран `proxies` переведён на `ServerSelectorCard(expanded: true)`; `proxy_tile.dart` — мёртвый код (не удалён).
  - Переключение через существующий `changeProxy(groupTag, outboundTag)` — сетевой/singbox слой не тронут.
  - ⚠️ Строки UI («Сервер», «Страна», «Транспорт», «Подключитесь…») захардкожены по-русски мимо системы переводов — кандидат на вынос в `assets/translations/*.json`.
- **Собраны релизы** (прод-канал `lib/main_prod.dart`, подпись `key.properties`) и разложены в `D:\Creativ\AxiOm (1)\Apps`: `AxiOm_APP_v1` (старый UI), `AxiOm_APP_v2` (новый UI, Android), `AxiOm_APP_Windows` (portable desktop из v2), `Tejar` (`Tejar-standalone.apk` из `D:\Creativ\TG`, сборка 27.05). Подробности — §9 «Готовые сборки», §10.
- **Замечено (не чинилось):** `flutter analyze` по `server_selector_card.dart` — 4 безвредных `warning` (лишний `!`) + 1 `info` (const); незадеплоенный серверный фикс `_looks_like_token()` по-прежнему даёт «100» вместо реального лимита устройств в приложении (см. §6.2).

### 29.05.2026 — Бэкенд формы заявок Design Master

- На сайте `design.DOMAIN` (RU, `/var/www/design`) форма заявки (имя, e-mail, бриф + выбранный тариф) теперь реально отправляется.
- Добавлен крошечный бэкенд: `/opt/design-backend/server.py` (Python stdlib, без зависимостей), сервис `design-backend.service`, слушает `127.0.0.1:8099`. Принимает `POST /api/order`, шлёт заявку в Telegram (бот-токен и admin ID ADMIN_A — в `Environment=` юнита, не во фронте).
- Caddy (RU): в блок `design.DOMAIN` добавлен `handle /api/* → reverse_proxy 127.0.0.1:8099`, остальное — статика. Конфиг провалидирован (`caddy validate`), reload без простоя; блок `ru.DOMAIN` (VLESS-нода) не тронут. Бэкап: `/etc/caddy/Caddyfile.bak.*`, `index.html.bak.*`.
- Фронт (`index.html`): submit-обработчик делает `fetch('/api/order')` с `{name,email,brief,tariff}`; на ошибку — alert, на успех — экран «Заявка принята». Локальная копия бэкенда: `D:\Creativ\AxiOm (1)\design-backend\server.py`.

### 29.05.2026 — Аудит серверов и чистка failed-юнитов

Проведён health-аудит NL/FR/PL/RU (всё работает; диски/память в норме, ноды подключены, туннель NL↔RU жив). Почищены упавшие systemd-юниты:

- **PL:** удалены легаси-серты `DOMAIN` и `direct.DOMAIN` (наследие старой панели 3x-UI) через `certbot delete`; отключён `certbot.timer` (Caddy ведёт серты сам, certbot не нужен); отключён `dnsmasq` (остаток старой внутр. сети 10.99.0.1, DNS держит systemd-resolved); `systemd-networkd-wait-online` — сброшен статус (может вернуться после ребута, чинится флагом `--any`). Бэкап сертов: `/root/letsencrypt.bak.*.tgz` на PL. Caddy и нода `pl.DOMAIN` не пострадали.
- **RU:** отключён `fwupd-refresh` (не нужен на VPS).
- Итог: `systemctl --failed` пуст на всех 4 серверах.

**Замечено для будущего (не чинилось):** на RU в `ip rule` нет правил fwmark→table для «умной маршрутизации» (метки в iptables mangle есть: ipset `russia`→direct, `ai_tunnel`→tunnel) — стоит проверить, применяются ли policy-routing правила после ребута. Нет автобэкапов и мониторинга. Marzban на `:latest` (не запинён). На FR крутится WebDAV/Nextcloud-контейнер.

### 29.05.2026 — Пробный период (3 дня)

- В главном меню бота покупок — кнопка «🎁 Получить пробный период (3 дня)». Видна, только если `users.trial_used = 0`.
- По нажатию (`get_trial`): создаётся пользователь Marzban по `config.TRIAL` (тариф «Максимальный, 3 устройства»: WS+Reality, все сервера вкл. RU, безлимит трафика, `ip_limit=3`, 3 дня), ставится лимит устройств, выдаётся ссылка, `trial_used=1` → кнопка пропадает. Без оплаты и без подтверждения админом.
- `config.TRIAL` (`code="trial_3d"`) вынесен отдельно от `TARIFFS` — в меню покупки не показывается. Триал не продлевается (кода нет в `TARIFFS` → «Продлить» в кабинете для него отдаёт «тариф недоступен»; пользователь покупает обычный тариф через «Купить»).
- БД: в `users` добавлена колонка `trial_used` (миграция через `PRAGMA`). Защита от повторной выдачи — по Telegram ID. Существующие пользователи (trial_used=0) тоже получат кнопку один раз.
- ⚠️ Анти-абьюз ограничен Telegram ID: разные аккаунты = разные триалы (вне рамок этой задачи).

### 29.05.2026 — Подавление спама входов в Telegram-логгер Marzban

- **@LOGGER_BOT** — это встроенный Telegram-логгер Marzban (`TELEGRAM_API_TOKEN` + `TELEGRAM_LOGGER_CHANNEL_ID` в `/opt/marzban/.env`). Он слал в канал уведомление на каждый успешный вход админа.
- Проблема: v3iplimit логинится под админом `iplimit` каждый цикл с IP `IP_NL` (сервер ходит на свой публичный IP через `lo`) → канал засыпало «входами».
- Решение (штатное, без правки кода): в `/opt/marzban/.env` добавлен `LOGIN_NOTIFY_WHITE_LIST = "IP_NL"` — для этого IP уведомления об **успешном** входе не шлются (см. `app/routers/admin.py`). Неудачные входы по-прежнему репортятся. Применяется через `docker compose up -d --force-recreate` (env читается при старте).
- ⚠️ Пересоздание контейнера = ~минута недоступности панели: в это время v3iplimit отдаёт `502/«Unexpected error getting token»` и переподключает воркеры нод — восстанавливается автоматически после старта Marzban.
- Решение переживает обновление образа (это env, не патч кода в контейнере). Бэкап `.env` — на сервере (`/opt/marzban/.env.bak.*`).

### 29.05.2026 — ФИО плательщика, отмена/отклонение заявок, надёжное продление (C + D)

- **ФИО плательщика:** после «Я оплатил» бот спрашивает ФИО владельца карты (FSM `PayStates.waiting_fio`, MemoryStorage по умолчанию). Заявка уходит админам только после ввода. ФИО хранится в `pending_payments.fio` и показывается в заявке админам. Касается и покупки, и продления. Ввод экранируется (`md_escape`) для Markdown.
- **D — отмена/отклонение (с взаимными уведомлениями):**
  - Пользователю в «Заявка отправлена» — кнопка «❌ Отменить заявку» (`cancelreq_{uid}`): удаляет `pending`, показывает предупреждение про возврат только через поддержку, **и уведомляет админов** об отмене (ID, ФИО, тариф).
  - Админам рядом с «Подтвердить» — «❌ Отклонить» (`reject_{uid}`): удаляет `pending` и уведомляет пользователя. Если заявки уже нет (пользователь сам отменил) — сообщение пользователю НЕ шлётся.
  - Защита: `confirm()` теперь не создаёт подписку, если `pending` отсутствует (отменён) — раньше мог создать пользователя с «дефолтным» именем.
  - На экране ввода ФИО — «❌ Отменить» (`cancelfio`); `/start` тоже сбрасывает состояние.
  - (TTL для зависших заявок пока НЕ делали.)
- **C — надёжное продление (вариант «коды тарифов»):** каждому тарифу добавлен стабильный `code` (`std_1m_dev`, `max_1y_gb`, …). В `subscriptions` — колонка `tariff_code`, заполняется при покупке. Продление находит тариф по коду (`tariff_index_for_sub`), с откатом на поиск по имени для старых записей. Переименование/перестановка тарифов в `config.TARIFFS` больше не ломает продление.

### 29.05.2026 — Управление подписками в кабинете (A + B + E)

- **A — скрытие удалённых:** в `/me` подписки, которых больше нет в Marzban (404), не показываются (раньше висели как «не удалось получить статус» с нерабочей кнопкой продления).
- **B — удаление подписки пользователем:** кнопка «🗑 Удалить N» → подтверждение → удаление пользователя из Marzban (`delete_user()`, `DELETE /api/user/{username}`) + строки из `bot.db` (`db.delete_subscription()`). Хендлеры `del_{sub_id}` / `delcfm_{sub_id}`.
- **E — чистка БД от «сирот»:** скрипт `cleanup_orphans.py` (лежит в `/opt/vpn-bot/`). Удаляет строки `subscriptions`, чьих юзеров нет в Marzban (404). Запуск вручную: `venv/bin/python cleanup_orphans.py` (dry-run) → `--apply`. Перед `--apply` сделать `cp bot.db bot.db.bak.*`.
  - ⚠️ На 29.05.2026 **не выполнен** автоматически (требует доступа к Marzban admin API — запускается вручную владельцем).
- В кабинете подписки нумеруются (1, 2, …), кнопки продления/удаления привязаны к номеру.

### 29.05.2026 — Продление подписок + второй админ

**Продление подписок (`bot.py`, `db.py`):**
- В личном кабинете у каждой подписки — кнопка «🔄 Продлить».
- Продление возможно **только тем же тарифом**, что был куплен (поиск по `tariff_name`). Хендлеры: `renew_{sub_id}` → `paidrenew_{user_id}_{sub_id}` → `confirmrenew_{user_id}`.
- `renew_user()`: `GET` юзера → `PUT` `expire = max(now, expire) + дни`, тот же тариф (трафик/устройства/протоколы), `status=active` → `POST /api/user/{username}/reset` (обнуление трафика). Ссылка подписки не меняется, дубликат в `subscriptions` не создаётся.
- Лимит **2 года** (`MAX_TOTAL_DAYS = 730`): если `остаток + дни_тарифа > 730` → блок (проверка при показе и при подтверждении).
- БД: в `pending_payments` добавлена колонка `is_renewal` (миграция через `PRAGMA`).

**Второй администратор:**
- `config.ADMIN_ID` (одно число) → `config.ADMIN_IDS = [ADMIN_A, ADMIN_B]`.
- Заявки (покупка и продление) рассылаются всем админам через `notify_admins()` (ошибка отправки одному не блокирует остальных). Подтвердить может любой из `ADMIN_IDS`.
- ⚠️ Новый админ должен сам нажать `/start` в боте, иначе Telegram не даёт боту написать первым.

Бэкапы перед деплоем: `BackUP\ru-bot-20260529\` (до продления), `BackUP\ru-bot-20260529-admins\` (до второго админа).

### 29.05.2026 — Чистка мёртвого кода на NL

Перед чисткой снят бэкап NL → `D:\Creativ\AxiOm (1)\BackUP\nl-20260529\` (тарболы `/opt/vpn-bot`, `/opt/v3iplimit` без venv, unit-файлы, `Caddyfile`, `/root/config.json`).

Удалено с NL:
- **`devices-api.service`** (порт :8765) — stop + disable + удалён unit. Приложение его не использовало (Caddy `/devices/*` → :7070).
- **`vpn-bot.service`** — удалён unit (бот уже был перенесён на RU, был disabled+inactive).
- Файлы из `/opt/vpn-bot/`: `devices_api.py`, `devices_api.py.bak.*`, `bot.py`, `config.py`, `db.py`, `bot.db`, `__pycache__/`.

Не тронуто (живое): `support-bot.service` (+`support_bot.py`/`support_config.py`/`support.db`), `v3iplimit.service` (`/opt/v3iplimit/`), Marzban.

Верификация после чистки: `support-bot` и `v3iplimit` — active; `devices-api`/`vpn-bot` — not-found; порт :8765 свободен; `GET localhost:7070/health` → 200.

### 29.05.2026 — Перенос бота покупок NL → RU

- `vpn-bot.service` (бот покупок, `/opt/vpn-bot/`) перенесён с NL на **RU** (`IP_RU`, SSH **2222**). На NL остановлен и `disabled`.
- На RU бот запускается из venv (`/opt/vpn-bot/venv/bin/python`, `PYTHONUNBUFFERED=1`). Установлены `python3.12-venv`, `python3-pip`; создан `requirements.txt` (`aiogram==3.27.0`, `aiohttp==3.13.5`).
- БД `bot.db` теперь на RU. (NL ↔ RU прямого SSH-доступа нет — файлы при переносе шли через локальную машину.)
- **`set_v2iplimit()` переписана**: была синхронная запись в `/root/config.json` (работала, пока бот жил рядом с конфигом на NL); стала `async` HTTP `POST` на `http://IP_NL:7070/api/set_limit`. В `config.py` добавлены `V2IPLIMIT_API_URL` и `V2IPLIMIT_API_KEY`, удалены `V2IPLIMIT_CONFIG`, `import json`, `import os`. См. §6.7.
- Support-бот и v3iplimit остались на NL без изменений.

### Май 2026 — V3IpLimit: создание

- Создан `V3IpLimit` — форк V2IpLimit на Python с добавленным FastAPI REST API
- Caddy переключён: `/devices/*` → `localhost:7070` (было `8765`) — **приложение теперь ходит на v3iplimit**
- Один сервис `v3iplimit.service` закрывает три роли: IP-лимитер, REST API для приложения, Telegram-мониторинг
- ⚠️ `devices-api.service` (:8765) и оригинальный `V2IpLimit`-бинарник выведены из оборота (приложение их не использует). Окончательно удалены с NL 29.05.2026 — см. запись о чистке ниже.

### Май 2026 — V3IpLimit: архитектурный рефакторинг (по итогам ревью Opus)

**Модель данных:**
- `UserType.ip: list[str]` → `UserType.ips: dict[str, float]` (ip → `time.monotonic()`)
- Отказ от `Counter(ip) > 2`: порог на объём трафика заменён на TTL-окна по времени
- `ACTIVE_USERS` больше не очищается целиком — устаревшие записи удаляются по TTL
- `LAST_CHECK_RESULT` (снимок-фолбэк) — упразднён, стал не нужен

**Два раздельных окна:**
- `DISPLAY_TTL = 60с` для REST API (что видит приложение)
- `ENFORCE_TTL = 240с` для проверки лимитов (что влечёт отключение)

**Исправленные баги:**
- `limit = 0` теперь означает безлимит и в display (`∞`), и в enforcement (проверка пропускается); ранее `limit=0` отключало пользователя при любом подключении
- IPv4 regex заякорен на `from IP:port` — исключён захват IP назначения вместо источника
- Race condition: атомарный снимок `ACTIVE_USERS` до первого `await` в enforcement-цикле

**⚠️ НЕ задеплоенный фикс (расхождение код ↔ прод):**
- `_looks_like_token()`: в локальном репозитории добавлен `_` в regex и убрана проверка `"_" not in value`, НО на сервере (`/opt/v3iplimit/api/rest_api.py` на 29.05.2026) этот фикс **отсутствует**. Токены Marzban с `_` по-прежнему не распознаются → приложение видит лимит 100 вместо реального. Симптом проявляется не на всех токенах (только с `_`). См. предупреждение в §6.2.

### Май 2026 — Инфраструктура и бот (28.05.2026)

**BBR на всех серверах:**
- Включён TCP BBR congestion control на NL, FR, PL, RU серверах
- RU сервер доступен по SSH только через порт 2222

**Веб-сайты:**
- Задеплоен лендинг AxiOm VPN: `https://axiom.DOMAIN` (FR сервер, `/var/www/landing/`)
- Задеплоен дизайн-сайт: `https://design.DOMAIN` (RU сервер, `/var/www/design/`)

**VPN-бот — изменение флоу покупки:**
- Убраны 8 кнопок тарифов с главного экрана `/start`
- Убрана кнопка "Сравнить тарифы" с главного экрана (заменена на "Купить")
- Возвращена кнопка "Сравнить тарифы" — показывает описание всех тарифов + кнопки "Купить" и "Назад"
- Новый флоу покупки: "Купить" → выбор категории (лимит по устройствам / лимит по трафику) → 4 тарифа категории
- Убрана прямая VLESS-ссылка NL сервера (для Tejar) из сообщения подтверждения оплаты
- Формулировка ссылки изменена: "для Hiddify" → "для AxiOm или любого совместимого клиента"

**REST API:**
- Поле `ips` убрано из ответа клиенту (приватность)

**Flutter-приложение:**
- `Timer` (одноразовый) → `Timer.periodic` + `ref.keepAlive()` — авторефреш больше не умирает при диспоузе провайдера
- `skipError: true` в `asyncInfo.when` — бейдж не пропадает при ошибке обновления, показывает последние известные данные
