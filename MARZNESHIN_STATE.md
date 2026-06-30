# AxiOm — состояние системы после переезда на Marzneshin

> Актуально на **08.06.2026**, обновлено **26.06.2026** (DPI-фикс: транспорт `[tcp]` Reality → XHTTP+TLS, см. §2), **30.06.2026** (добавлен Naive-прокси — нативная интеграция в Marzneshin + поддержка в клиенте, см. §9 и [`NAIVE_PROXY.md`](NAIVE_PROXY.md)). Единый источник истины по тому, **что есть сейчас**
> после миграции с Marzban на Marzneshin. Секреты тут не хранятся (пароли/ключи/токены —
> в `.env` на серверах и в авто-памяти).
>
> Исторические документы плана/обкатки (`MIGRATION_MARZNESHIN.md`, `Marzneshin_Phase1_Report.md`,
> `SESSION_LOG_Marzneshin_Migration.md`) удалены — миграция завершена, их содержимое
> законсервировано здесь в актуальном виде.

---

## 1. Итог одной строкой

VPN-инфраструктура переведена с **Marzban** на **Marzneshin**. Боевой бот продаж,
лимитер устройств и клиентские подписки работают на новой панели. Старый Marzban оставлен
**остановленным как откат** (панель на NL ещё жива, ноды Marzban погашены).

---

## 2. Панель и ноды

**Панель Marzneshin — на FR** (`IP_FR`), домен `fr.DOMAIN`.
Локальная нода FR: `INSECURE=True`, backend `grpcio`. Удалённые ноды — `grpclib` (TLS), порт `53042`.
⚠️ **Фаервол (10.06.2026):** на NL/PL/RU `53042/tcp` закрыт для всех, кроме IP панели FR
(`iptables -I INPUT ! -s IP_FR/32 -p tcp --dport 53042 -j DROP`). Персист: NL/PL —
`netfilter-persistent` (`/etc/iptables/rules.v4`); RU — юнит `axiom-firewall.service` (oneshot,
iptables-persistent на RU не стоит). Новой ноде правило надо ставить вручную при добавлении.

| Нода | IP | backend | Протоколы | Статус |
|---|---|---|---|---|
| France (local) | IP_FR | grpcio | VLESS WS + XHTTP+TLS + HY2 | healthy |
| Poland | IP_PL | grpclib | VLESS WS + XHTTP+TLS + HY2 | healthy |
| Netherlands | IP_NL | grpclib | VLESS WS + XHTTP+TLS + HY2 | healthy |
| Russia | IP_RU | grpclib | **VLESS WS + XHTTP+TLS** | healthy |

**Транспорт `[tcp]` (26.06.2026): Reality → XHTTP+TLS.** DPI 3-го поколения в РФ начал резать
VLESS+Reality (поведенческий анализ) — слот `[tcp]` перестал работать. На всех нодах добавлены
xhttp-инбаунды (порт `2087`, `network:xhttp`, `security:tls`, путь `/xhvless`, серт Let's Encrypt
`fr.DOMAIN`); хосты `[tcp]` переключены на них, старые reality-хосты отключены. DPI видит легитимный
HTTPS к реальному домену. **Пересборка клиента НЕ потребовалась** — текущее ядро тянет xhttp+tls;
прошлый диагноз «version-lock» был ложным (блокером была reality, не версия xray).
⚠️ Готчи: `fp=chrome` в vless-ссылке ломает xhttp-downstream → fingerprint пустой; reality
несовместима с xhttp; клиент обязан включить тумблер **«Use xray-core when possible»** (для
существующих сборок — рассылка/инструкция). Правило подписки `^HiddifyNextX → base64-links`.
**Удалённые ноды:** xhttp-инбаунд задаётся **через панель** (`GET`→правка→`PUT /api/nodes/{id}/xray/config`),
а не правкой файла — иначе инбаунд не регистрируется для пуша юзеров (на локальной FR правка файла работает).

**DPI-фикс v2 (28.06.2026): `[tcp]` xhttp переведён с порта 2087 на 443 (за Caddy).** Провайдеры
режут TLS на нестандартном порту 2087 (у части юзеров `[tcp]` не работал, у других — да; на мобильном
через раз). Решение: на каждой ноде Caddy на 443 принимает `fr.DOMAIN/xhvless` и
reverse-proxy'ит в локальный xhttp-инбаунд (`https://127.0.0.1:2087`, `flush_interval -1`); хосты
`[tcp]` в панели (id 15/16/17/18) переключены `port 2087→443`. Теперь `[tcp]` = обычный HTTPS на 443,
фильтрация по порту не мешает. **Порт 2087 оставлен открытым как фолбэк** (старые конфиги живы; новые
едут на 443 при обновлении подписки). На FR Caddy уже держал `fr.DOMAIN` (ACME, самопродление);
на PL/NL/RU добавлен сайт-блок `fr.DOMAIN` с файловым сертом `/var/lib/marznode/fr.{crt,key}`,
а `sync_marznode_cert.sh` теперь и `systemctl reload caddy` на удалённых нодах (иначе после продления
серта Caddy отдавал бы старый). Проверено сквозным xray-тестом на всех 4 нодах + реальным клиентом.
⚠️ Клиентское: первый коннект xhttp может «прогреваться» до нескольких минут; некоторые iOS-клиенты
(Happ) могут капризничать — самый совместимый фолбэк-транспорт `[ws]` (тоже 443).

**Инбаунды:** VLESS_WS / VLESS_Reality / VLESS_XHTTP / HY2 на нодах (reality-инбаунды живы, но
их хосты отключены — слот `[tcp]` обслуживает xhttp). RU: WS + XHTTP, без Reality/HY2.

**Сервисы Marzneshin:**
- **Standard** (id=1) — VLESS `[tcp]` (XHTTP+TLS) на FR/PL/NL (без RU).
- **Maximum** (id=2) — VLESS WS + `[tcp]` (XHTTP+TLS) + HY2 на FR/PL/NL + **WS + `[tcp]` на RU**.

**Почему RU без HY2:** умная маршрутизация .ru заворачивает ответы с публичного IP в
WG-туннель на NL → UDP-протоколы (HY2) ломаются асимметрией; WS (Cloudflare) и xhttp+tls работают.

**Продление серта `[tcp]`:** все ноды используют один серт `fr.DOMAIN`. systemd-timer
`marznode-cert-sync.timer` (daily) на FR раздаёт обновлённый Let's Encrypt-серт с Caddy на все ноды
по SSH (только при изменении) + рестартит marznode. Без него серт ротировался бы ~раз в 60 дней.

**HY2:** UDP `9444`, TLS (Let's Encrypt `fr.DOMAIN`) + обфускация Salamander.
⚠️ Готч: рестарт удалённой marznode теряет sing-box (HY2) юзеров — восстанавливает только
рестарт самой панели `docker restart marzneshin-marzneshin-1` на FR (~30с).

**Админы панели (все sudo):** `admin` (исходный), `V3IPLimit` (лимитер), `API_USER` (бот/скрипты).

---

## 3. Бот продаж — на Marzneshin (прод)

Боевой `vpn-bot.service` на **RU** (`IP_RU`, SSH `:2222`) переведён на Marzneshin API
(cutover 08.06.2026). Каталог `/opt/vpn-bot`.

**Ключевые отличия Marzneshin (учтены в коде):**
1. **Username → нижний регистр** при создании. Генерация имён в боте — сразу `.lower()`.
2. **Нет поля `status`** (как в Marzban). `_normalize_user()` синтезирует его из
   `is_active`/`expired`/`enabled`/`data_limit_reached`.
3. **expire** через `expire_strategy=fixed_date` + `expire_date` (ISO, naive UTC), а не unix.
4. **Инбаунды → `service_ids`**: `_tariff_to_service_ids()` — есть `VLESS WS` в тарифе → `[2]`
   (Maximum), иначе `[1]` (Standard).
5. Поиск юзеров — параметр `username` (не `search`). Подписка — абсолютный `subscription_url` из ответа.

**Имена функций сохранены** (`get_marzban_user`, `get_marzban_users`, `create_user`,
`renew_user`, `delete_user`, …) — внутри ходят на Marzneshin. `bot.db` и **столбец
`marzban_username` оставлены как есть** (бизнес-логика/история не переписывалась).

**Тарифы** (в `config.py`, бизнес-обёртки над 2 сервисами) — без изменений:
4 «Стандарт» (→ Standard), 4 «Максимальный» + триал (→ Maximum). Цены/сроки/лимиты устройств/трафик
сохранены 1:1; лимит устройств ставится через device-API V3IpLimit.

**Веб-покупки (лендинг + Платёжка), фиксы 10.06.2026:**
- Реферальный бонус теперь начисляется и на **платных** веб-покупках (`web_api.py`: `referrer_id`
  передаётся в `save_web_payment` на платном пути; раньше — только при 100%-промо).
- Гонка «webhook + опрос статуса» разведена CAS: `db.claim_web_issue()` атомарно `pending → issuing`,
  второй вызов не дублирует создание юзера (раньше ловил 409), а ждёт результат; при ошибке
  платёж возвращается в `pending` для повтора.

**Маппинг API Marzban → Marzneshin:**

| Операция | Marzban | Marzneshin |
|---|---|---|
| Auth | `POST /api/admin/token` | `POST /api/admins/token` (form) |
| Get user | `GET /api/user/{u}` | `GET /api/users/{u}` |
| List | `GET /api/users` | `GET /api/users?page=&size=` |
| Create | `POST /api/user` | `POST /api/users` |
| Update | `PUT /api/user/{u}` | `PUT /api/users/{u}` (username в теле!) |
| Delete | `DELETE /api/user/{u}` | `DELETE /api/users/{u}` |
| Reset traffic | `POST /api/user/{u}/reset` | `POST /api/users/{u}/reset` |
| Enable | — (поле status) | `POST /api/users/{u}/enable` |
| Срок | `expire` (unix) | `expire_strategy` + `expire_date` (ISO) |
| Инбаунды | `inbounds:{vless:[…]}` | `service_ids:[1,2]` |

---

## 4. V3IpLimit (лимит устройств) — на Marzneshin, мульти-нодовый

Боевой лимитер/счётчик устройств — **на FR** (`fr.DOMAIN/devices`).
Считает онлайн-IP по access-логам Xray **и** sing-box (HY2), окно скользящее.

**Мульти-нодовый подсчёт без cross-SSH:** на каждой ноде `log-forwarder.service` тейлит
`/var/lib/marznode/access.log` + sing-box лог и **пушит** новые строки на FR
`POST /devices/api/ingest` → один и тот же парсер агрегирует IP по ВСЕМ нодам (глобальный лимит).

**API (используется ботом и приложением):**
`GET /devices/api/devices/{username|token}?key=` → `{connected, limit}`
(поле `ips` убрано из ответа 10.06.2026 — ключ зашит в клиент, список IP всех юзеров был утечкой;
бот/приложение используют только `connected`/`limit`). Проверка ключа — `hmac.compare_digest`.
`POST /devices/api/set_limit` (персональный лимит); `GET /devices/health`; `POST /devices/api/ingest`.
Резолв `{token}`: `_build_response` приводит username к `.lower()` — старые Marzban-токены
кодируют исходный регистр (`Arco`), а счёт идёт по lowercase (`arco`); без нормализации
давало `connected:0`.

**Мост `/devices` на NL → FR:** в Caddy на NL `handle /devices/* → https://fr.DOMAIN`
(`header_up Host`). Выпущенные сборки приложения раньше ходили на NL — после остановки старого
лимитера это сломалось; мост чинит их без пересборки. ⚠️ **Мост не убирать**, пока не все клиенты
обновлены на FR-сборку.

Старый лимитер эпохи Marzban (`v3iplimit.service` на NL + телеграм-бот `@IPLIMIT_BOT`) —
**остановлен и отключён** (тейлил логи Marzban-нод, больше не нужен).

**Ключ device-API** (`DEVICE_API_KEY`) в коде НЕ хранится — `api/rest_api.py` читает его из env.
На FR задаётся в `/opt/v3iplimit/.env` (chmod 600, `EnvironmentFile=` в systemd-юните). Тот же
ключ — в `.env` бота (`V2IPLIMIT_API_KEY`) и в клиенте `device_count_provider.dart`.
⚠️ Значение присутствует в git-истории до 08.06.2026 → при необходимости полной безопасности
ключ стоит **ротировать** во всех трёх местах.

---

## 5. Непрерывность старых ссылок (шим-редирект)

Старая ссылка Marzban (`vpn.DOMAIN/sub/{token}`) ≠ Marzneshin
(`fr.DOMAIN/sub/{user}/{key}`). Мост — `sub-redirect.service` на **NL** (`:8011`):
декодит username из токена → `.lower()` → есть в Marzneshin? **302** на новый sub : иначе
fallthrough-прокси на Marzban `:8000` (fail-safe). Caddy NL: `handle /sub/* → 127.0.0.1:8011`
перед catch-all. Клиент (302-follow) сам подтягивает новый конфиг → **переустановка не нужна**.

⚠️ **Caddy + `sub-redirect.service` на NL обязаны оставаться живыми**, даже когда погасим панель
Marzban — иначе старые ссылки перестанут редиректиться.

---

## 6. Миграция пользователей — выполнена

Скрипт `_node_template/migrate_user.py` (лежит на NL как `/opt/migrate_user.py`): читает юзера
Marzban (API sudo `migrator`, фолбэк SQLite) → маппит inbounds→service (Reality→1 / WS+Reality→2) →
создаёт в Marzneshin `username.lower()` → переносит expire (`None`→`never`, не +30 дней!) /
data_limit / персональный IP-лимит (из старого `/root/config.json` `SPECIAL_LIMIT`). Идемпотентен (409).
`used_traffic` НЕ переносится. `bot.db` НЕ трогает.

**Все живые (не истёкшие) юзеры перенесены** и проверены (service_ids, срок, лимиты). При cutover
бота `bot.db` единоразово приведён к нижнему регистру:
`UPDATE subscriptions SET marzban_username=lower(...)` (+ в pending/web_payments/web_trials).

---

## 7. Что осталось из старого Marzban

| Что | Где | Статус |
|---|---|---|
| Панель Marzban | NL `marzban-marzban-1` | **Up** (намеренно — откат + fallthrough шима) |
| marzban-node | PL, RU | Exited (остановлены 08.06) |
| marzban-node | FR `marzban-node-marzban-node-1` | **Up** (остаток старой схемы, не используется — кандидат на `docker stop`, ест память на самом нагруженном сервере) |
| `v3iplimit.service` + `@IPLIMIT_BOT` | NL | inactive + disabled |
| `vpn-bot-test.service` | RU | inactive (тестовый инстанс, не нужен) |
| `/var/lib/marzban/db.sqlite3` | NL | цел (нужен пока панель жива) |

**Активно из старого — только панель Marzban на NL.** Всё остальное на новом стеке или остановлено.

### План полного вывода Marzban (когда будешь готов)
1. Грейс-период ~1–2 недели с живой панелью + шимом.
2. Остановить панель Marzban на NL (`docker compose stop`), **Caddy + шим оставить**.
3. Бэкап `db.sqlite3`, затем удалить: остановленные marzban-node (PL/RU/FR), `vpn-bot-test` (RU),
   старый `/opt/v3iplimit` (NL).
4. (Опц.) убрать `@IPLIMIT_BOT` в BotFather.

---

## 8. Клиент AxiOm v2

Добавлен каскадный селектор Страна → **Протокол** (VLESS/Hysteria2) → Транспорт.
Парсер remark: `^(.*?)\s*\([^)]*\)\s*\[([a-z0-9]+)\]\s*$`; токен → `ws`=VLESS/WS, `tcp`=VLESS/XHTTP+TLS
(до 26.06.2026 — Reality; транспорт сменился на сервере без пересборки клиента, токен `[tcp]` сохранён),
`hy2`=Hysteria2. **Сервер виден в селекторе только если remark = `<АнглСтрана> (текст) [ws|tcp|hy2]`.**
⚠️ Для туннелирования xhttp клиент должен включить тумблер **«Use xray-core when possible»** (Настройки→Общие).

**Счётчик устройств (08.06):** `device_count_provider.dart` `_baseUrl` → `fr.DOMAIN/devices`;
ключ передаётся при сборке `--dart-define=DEVICE_API_KEY=<key>` (в коде не хранится — иначе счётчик
скрыт). Пересобраны **v2 Android (split-per-abi APK) + Windows** с ключом. Репо клиента —
`github.com/arsenii-cmd/AxiOm-v2` (ветка `master`).

Осталось: **раздать новую сборку** пользователям (до этого старые установки работают через
Caddy-мост `/devices` на NL).

---

## 9. Naive-прокси (HTTP/2 CONNECT через gost) — нативная интеграция (30.06.2026)

Запасной транспорт, независимый от связки VLESS+XHTTP из §2. Naive — HTTP CONNECT поверх TLS;
протокол портирован в **sing-box** из Caddy `klzgrad/forwardproxy`. **В xray-core он не
реализован** — это ограничение протокола, не конфигурации (разные движки, разные наборы
протоколов; подробнее и пошаговая инструкция по раскатке/патчам — [`NAIVE_PROXY.md`](NAIVE_PROXY.md)).

**Зачем второй транспорт:** xhttp-связка `[tcp]` (см. §2) — компромисс под DPI, но архитектурно
несовместима со sing-box-клиентами без тумблера «Use xray-core when possible», а часть клиентов
(сторонние Android-приложения вроде Happ) sing-box-формат подписки не запрашивают вовсе. Naive
не зависит от этого: чистый HTTP/2-прокси-трафик, неотличимый от обычного HTTPS до домена.

**Архитектура (идентична на FR/PL/NL):**
- **gost** (`go-gost/gost` v3.2.6) — handler `http`, TLS-листенер на `:8888` (серт Let's Encrypt
  ноды). Заменяет собой связку Caddy `forwardproxy` — у Caddy v2.11.4 известный баг
  TCP-hijack для HTTP/1.1 CONNECT (после ответа `200` трафик не туннелировался дальше).
- **Аутентификация** — auther-плагин gost делает HTTP-callback на локальный `naive_auth.py`:
  раз в 30с тянет активных юзеров (`is_active && enabled && !expired`, опционально фильтр по
  `NAIVE_SERVICE_IDS`) из API панели, строит set паролей `xxh128(subscription_key)` — тот же
  алгоритм, которым сама Marzneshin деривирует UUID/password (`AUTH_GENERATION_ALGORITHM=XXH128`).
  Отвечает gost'у `{ok,id}` на POST `{username,password,client}`.
- systemd: `gost-proxy.service` + `naive-auth.service` на каждой ноде. Креды панели — в
  `/etc/naive-auth.env` (chmod 600), не в коде.

**Интеграция в панель — без миграций схемы.** У Marzneshin нет нативного UI-протокола под Naive,
использован существующий механизм «ручных хостов» (host без привязанного inbound — только
`host_protocol`). Три точечных патча, применяемых **volume-mount поверх образа** (переживают
`docker pull`/пересоздание контейнера — без mount слетают):
- `app/models/proxy.py` — `ProxyTypes.Http = "http"` в enum.
- `app/utils/share.py` — `create_config()`: для `host_protocol == "http"` пароль для ручного
  хоста деривируется как `xxh128(key)` (per-user секрет вместо `host.uuid`/`host.password`).
- `v2share/singbox.py` — `create_outbound()`: ветка `protocol == "http"` строит sing-box
  http-outbound (`username`/`password`).

Применяются идемпотентным скриптом `apply_patches.py`. Создан отдельный **сервис Naive** в
панели (через API, одинаково на PL/NL/FR) + три ручных хоста (по одному на ноду). **Формат
remark обязателен:** `<Страна> ({USERNAME}) [Naive]` — `{USERNAME}` рендерится панелью per-user,
не хардкодить конкретное имя.

**Видимость в подписке зависит от ФОРМАТА ответа, не от тумблера в клиенте.** У Naive нет
URI-схемы (`naive://` не существует как ссылочный формат) — выразим только JSON-аутбаундом
sing-box. Marzneshin выбирает формат подписки (`links`/`xray`/`sing-box`/…) через
`subscription_settings.rules` (regex по User-Agent запроса, общий для ВСЕХ хостов сразу — это
**не** per-host фильтр). Следствие:
- UA, смэппленный на `sing-box` → клиент видит и VLESS, и Naive в JSON.
- UA, смэппленный на `xray`/`links` (vless-ссылки) → Naive **автоматически отсутствует**:
  библиотека генерации ссылок (`v2share/links.py`, `supported_protocols`) не содержит `"http"`,
  при `swallow_errors=True` (дефолт Marzneshin) хост тихо пропускается, не ошибка.
- Тумблер клиента «Use xray-core when possible» тут ни при чём — он решает, каким движком
  (xray vs sing-box) клиент исполняет уже полученный конфиг, а не какой конфиг он получил.

**Раскатка:** идентично на PL/FR/NL параметризованным скриптом (домен ноды → свой LE-серт).
RU пока **не раскатан** — перед раскаткой проверить, что на RU есть LE-серт под `ru.DOMAIN`
(Caddy на RU уже держит сертификаты для других целей, можно переиспользовать выпуск).

**Клиент AxiOm v2 (Flutter):** добавлена обработка протокола в селекторе сервера —
`lib/features/proxy/model/server_option.dart` (токен `[naive]`/`[http]` → протокол `naive`,
ранг сортировки после Hysteria2, лейбл «Naive», транспорт «HTTP/2 TLS») и
`lib/features/home/widget/server_selector_card.dart` (строка «Транспорт» скрыта для Naive,
аналогично Hysteria2 — у обоих единственный транспорт). Видим только при sing-box-формате
подписки (правило по UA выше).

⚠️ **Не реализовано (идея, обсуждалась 30.06.2026):** для сторонних xray-клиентов (Happ и
аналоги), которые не запрашивают sing-box-формат — поставить нормальный TLS-fingerprint
(`chrome` и т.п.) на существующих `[tcp]`-хостах специально под них, не трогая то, что видит
AxiOm-клиент. Технически достижимо **без патчей панели**: добавить в `subscription_settings.rules`
правило по UA Happ → результат `xray`/`links` (тогда Naive у них автоматически не появится, а
`[tcp]` получит нужный fp). Осталось: 1) узнать реальный User-Agent Happ при запросе подписки;
2) добавить правило в панели; 3) выставить `fingerprint` на хостах `[tcp]`.
