# Naive-прокси поверх Marzneshin — как это сделано

> Дата внедрения: **30.06.2026**. Раскатано на **PL, FR, NL** (одинаково). RU — не раскатан.
> Краткая сводка — в [`MARZNESHIN_STATE.md`](MARZNESHIN_STATE.md) §9. Этот документ — пошаговый
> технический гайд для повторения/обслуживания/раскатки на новую ноду.
>
> Секреты (пароли, токены, API-ключи, реальные домены/IP) тут не хранятся — используются
> плейсхолдеры (`DOMAIN`, `IP_FR` и т.п., как в [`PLACEHOLDERS.md`](PLACEHOLDERS.md), который
> в репозиторий не попадает).

---

## 1. Зачем

[tcp]-слот (VLESS + XHTTP + TLS, см. `MARZNESHIN_STATE.md` §2) — рабочий, но архитектурно
хрупкий компромисс под DPI: требует от sing-box-клиента специального тумблера и не виден
сторонним xray-клиентам без донастройки. **Naive** — независимый запасной транспорт:
HTTP/2 CONNECT поверх честного TLS-соединения к реальному домену, неотличимый снаружи от
обычного HTTPS-трафика к веб-сайту.

Протокол изначально реализован в Caddy (`klzgrad/forwardproxy`), затем портирован как нативный
outbound-тип в **sing-box**. **В xray-core такого протокола нет** — это не баг конфигурации, а
разница протокол-сетов движков. Отсюда два следствия, которые определили весь дизайн ниже:

1. Naive можно отдать клиенту только в **sing-box JSON**-формате подписки (нет `naive://` ссылки).
2. На сервере Naive не может быть обычным Xray-инбаундом панели — обслуживается отдельным
   процессом (`gost`), панель лишь хранит метаданные хоста и генерирует конфиг для клиента.

---

## 2. Почему gost, а не Caddy `forwardproxy`

Первая попытка — встроить Naive через `klzgrad/forwardproxy` (модуль Caddy, есть готовая сборка
с этим плагином). После CONNECT Caddy отвечал `200`, но **трафик дальше не туннелировался** —
известный баг TCP-hijack для HTTP/1.1 CONNECT в Caddy v2.11.4. Переключились на отдельный процесс
**gost v3.2.6** (`go-gost/gost`), у которого `http`-handler с CONNECT работает штатно и есть
встроенный механизм auther-плагинов (HTTP callback) для динамической per-user-аутентификации.

---

## 3. Серверная часть (на каждой ноде: FR, PL, NL)

### 3.1 `naive_auth.py` — аутентификация против панели

Лежит в `/usr/local/bin/naive_auth.py`. Маленький HTTP-сервис, который:

1. Раз в 30 секунд опрашивает API панели Marzneshin (`GET /api/users` с пагинацией) под
   служебным админ-аккаунтом.
2. Фильтрует пользователей: `is_active && enabled && !expired` (и опционально по
   `NAIVE_SERVICE_IDS`, если Naive должен быть виден не всем сервисам).
3. Для каждого юзера строит ожидаемый пароль = `xxh128(subscription_key).hexdigest()` — это **тот
   же алгоритм**, которым сама Marzneshin деривирует UUID/password для протоколов без явного
   `host.uuid`/`host.password` (`AUTH_GENERATION_ALGORITHM=XXH128` в настройках панели), так что
   отдельно ничего генерировать/хранить не нужно — пароль детерминированно следует из ключа
   подписки пользователя.
4. Держит в памяти set `{username: password}` и отвечает `gost`'у на callback-запрос
   `POST {username, password, client}` → `{"ok": true/false, "id": username}`.

Креды панели и `NAIVE_SERVICE_IDS` — в `/etc/naive-auth.env` (`chmod 600`), юнит читает его через
`EnvironmentFile=`. В коде `naive_auth.py` секретов нет.

### 3.2 `gost` — TLS-листенер + auther-плагин

`/etc/gost/naive.yaml` (домен-специфичный сертификат на каждой ноде):

```yaml
services:
  - name: naive-proxy
    addr: ":8888"
    handler:
      type: http
      auther: naive-auther
    listener:
      type: tls
      tls:
        certFile: /etc/letsencrypt/live/<домен-ноды>/fullchain.pem
        keyFile: /etc/letsencrypt/live/<домен-ноды>/privkey.pem

authers:
  - name: naive-auther
    plugin:
      type: http
      addr: "http://127.0.0.1:8765"   # naive_auth.py
```

> Готча при первом деплое: пути `${CERT}`/`${KEY}` в heredoc-шаблоне съедались локальным bash при
> подстановке через ssh-туннель — лечится только literal-путями в YAML без интерполяции переменных
> со стороны управляющей машины (генерировать готовый YAML и заливать файлом, не через `ssh '...'`
> с переменными).

### 3.3 systemd-юниты

```ini
# /etc/systemd/system/naive-auth.service
[Unit]
Description=Naive auth backend for gost
After=network.target

[Service]
EnvironmentFile=/etc/naive-auth.env
ExecStart=/usr/bin/python3 /usr/local/bin/naive_auth.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/gost-proxy.service
[Unit]
Description=gost Naive proxy
After=network.target naive-auth.service
Requires=naive-auth.service

[Service]
ExecStart=/usr/local/bin/gost -C /etc/gost/naive.yaml
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```

### 3.4 Зависимости

`xxhash` (для `xxh128`) ставится через `apt-get install python3-xxhash` (фоллбек —
`pip3 install xxhash`, если в окружении нет системного пакета; голый `pip`/`pip install` без
venv на свежих Debian/Ubuntu не работает, нужен либо apt, либо `python3 -m venv`).

---

## 4. Патчи панели (Marzneshin) — нативная интеграция

Цель: чтобы Naive выглядел в панели «как остальные подключения» — создаваемый сервис, юзеры
привязываются обычным способом, подписка генерируется панелью автоматически, без отдельного
скрипта-конвертера сбоку.

### 4.1 Почему патчи, а не форк/пересборка образа

Три точечных изменения в Python-коде панели и в библиотеке генерации подписок (`v2share`).
Вместо форка образа — **volume mount** конкретных файлов поверх их штатных путей внутри
контейнера. Так апдейты `docker pull` новой версии Marzneshin не требуют пересборки своего
образа — слетают только сами эти 3 файла, если апстрим их радикально переписал (проверяется
вручную при апдейте).

### 4.2 Файлы патчей

Лежат на хосте (FR — где живёт панель) в `/var/lib/marzneshin/patches/`:

**`proxy.py`** (патчит `app/models/proxy.py` — enum `ProxyTypes`):
```python
class ProxyTypes(str, Enum):
    ...
    ShadowTLS = "shadowtls"
    Http = "http"          # добавлено
```

**`share.py`** (патчит `app/utils/share.py` — функция `create_config()`):
```python
# ветка для "ручного" хоста (host без привязанного inbound)
if host.host_protocol == "http":
    auth_uuid, auth_password = None, gen_password(key)   # xxh128(key)
else:
    auth_uuid, auth_password = (UUID(host.uuid) if host.uuid else None), host.password
```

**`singbox.py`** (патчит `v2share/singbox.py` — функция `create_outbound()`):
```python
elif config.protocol == "http":
    outbound["username"] = "axiom"
    outbound["password"] = config.password
```

### 4.3 Применение — идемпотентный скрипт

`/var/lib/marzneshin/patches/apply_patches.py` копирует эти три файла поверх реальных путей
внутри контейнера (если уже совпадают — no-op). Запускается вручную после `docker pull` новой
версии панели, перед `docker compose up -d`.

### 4.4 docker-compose — персистентность через volume mount

`/etc/opt/marzneshin/docker-compose.yml`:
```yaml
services:
  marzneshin:
    volumes:
      - /var/lib/marzneshin:/var/lib/marzneshin
      - /var/lib/marzneshin/patches/proxy.py:/app/app/models/proxy.py
      - /var/lib/marzneshin/patches/share.py:/app/app/utils/share.py
      - /var/lib/marzneshin/patches/singbox.py:/usr/local/lib/python3.12/site-packages/v2share/singbox.py
```

Без этих volume-маунтов патчи слетают при любом пересоздании контейнера (`docker compose up -d
--force-recreate`, обновление образа и т.п.) — правка файла внутри уже запущенного контейнера
переживает только рестарт процесса, не пересоздание.

> Перед первым применением был сделан **полный бэкап** `docker-compose.yml` (суффикс
> `.preNaive`) и панели — на случай, если патч уронит контейнер на старте (риск для прода).

### 4.5 Создание сервиса и хостов через API

Сервис **Naive** создан через API панели одинаково на всех трёх нодах (PL/FR/NL). Хосты
привязываются к юзерам через `service_ids` (не `inbound_ids` — это два разных механизма
привязки в Marzneshin: `inbound_ids` работает только для реальных Xray/sing-box-инбаундов
ноды, `service_ids` — более общий уровень, которым оперируют и «ручные» хосты).

**Формат remark хоста обязателен:** `<Страна> ({USERNAME}) [Naive]` (например
`France ({USERNAME}) [Naive]`) — `{USERNAME}` это литеральный плейсхолдер, который сама панель
подставляет per-user при генерации подписки; хардкодить конкретное имя пользователя в remark —
ошибка (увидят все юзеры одно и то же чужое имя в названии сервера).

---

## 5. Видимость в клиенте — зависит от формата подписки

Это самый частый источник путаницы при отладке, поэтому отдельно и подробно.

**Naive не имеет ссылочной (URI) схемы.** В отличие от `vless://`, `vmess://`, `trojan://` —
никакого `naive://` как стандарта не существует. Единственный способ передать его клиенту —
JSON-аутбаунд в sing-box-конфиге.

Marzneshin решает, в каком формате ответить на запрос подписки, через таблицу правил
`subscription_settings.rules` — список `{pattern (regex по User-Agent), result (формат)}`,
проверяемых по порядку (`app/routes/subscription.py::user_subscription`). Доступные `result`:
`xray`, `sing-box`, `clash`, `clash-meta`, `links`, `base64-links`, `template`, `block`.

Эти правила **выбирают формат для всего ответа сразу**, а не отдельные хосты. Но конечный эффект
эквивалентен per-host фильтрации, потому что генератор ссылок (`v2share/links.py`,
`LinksConfig.supported_protocols`) **не содержит `"http"`** в списке поддерживаемых протоколов:
при `swallow_errors=True` (дефолт Marzneshin) такой хост при рендере в `links`/`xray`-формат
**молча пропускается**, без ошибки.

Итог:
| UA-паттерн → формат | Что видит клиент |
|---|---|
| `sing-box` | VLESS **и** Naive (оба умеют JSON) |
| `xray` / `links` / `base64-links` | только VLESS-ссылки, Naive отсутствует |

**Тумблер «Use xray-core when possible» в клиенте AxiOm к этому не относится** — он переключает,
каким движком (xray vs sing-box) уже полученный sing-box-конфиг исполняется на устройстве, а не
какой конфиг клиент получил от панели. Если кажется, что Naive «то есть, то нет» в зависимости
от тумблера — на самом деле разные подписки добавлены в клиент (одна с суффиксом `/sing-box` в
URL, другая без) или используется другая сборка/UA.

---

## 6. Клиент AxiOm v2 — поддержка протокола в UI-селекторе

Каталог `D:\Creativ\AxiOm_APP_v2\hiddify-app`. Сервер уже отдавал Naive в составе sing-box JSON
с правильными remark-тегами — но кастомный Flutter-селектор серверов парсит remark через regex
и маппинг bracket-токена в протокол; токен `naive`/`http` не был в нём учтён, сервер просто
отбрасывался при парсинге (возвращался `null`).

**`lib/features/proxy/model/server_option.dart`:**
- Константа `protocolNaive = 'naive'`.
- В `_tokenToProtocolTransport`: `'naive' || 'http' => (protocolNaive, 'naive')`.
- `_protocolRank`: VLESS(0) → Hysteria2(1) → Naive(2) → остальное(3) — порядок в дропдауне.
- `protocolLabel`: `'naive' => 'Naive'`.
- `transportLabel`: `'naive' => 'HTTP/2 TLS'`.

**`lib/features/home/widget/server_selector_card.dart`:**
- В `buildTransportRow` строка «Транспорт» скрывается для Naive, как и для Hysteria2 — у обоих
  ровно один транспорт, отдельный дропдаун избыточен.

Низкоуровневый enum `lib/singbox/model/singbox_proxy_type.dart` (`ProxyType`) уже содержал
`http`/`naive` из апстрима Hiddify — правок не потребовалось, это просто маппинг отображаемых
названий для ядра, а не парсер UI-селектора.

> Тест `test/features/proxy/model/server_option_test.dart` уже был рассинхронизирован с актуальной
> сигнатурой `ServerOption` **до** этой работы (вызывает `find()` с 3 аргументами вместо 4) —
> не трогался намеренно, это отдельный pre-existing долг.

---

## 7. Раскатка на новую ноду — чеклист

1. Убедиться, что на ноде есть валидный LE-сертификат на её домен (Caddy на ноде уже должен
   его держать для других целей — gost переиспользует те же файлы `fullchain.pem`/`privkey.pem`).
2. Установить `gost` (бинарник) и `python3-xxhash`.
3. Развернуть `naive_auth.py`, `/etc/naive-auth.env` (креды панели, `NAIVE_SERVICE_IDS`),
   `/etc/gost/naive.yaml` (с путём к серту этой ноды), юниты `naive-auth.service` +
   `gost-proxy.service`, `systemctl enable --now` оба.
4. На панели (один раз, не per-нода): убедиться, что сервис **Naive** существует, привязать к
   нему новый хост ноды (`service_ids`), remark — `<Страна> ({USERNAME}) [Naive]`.
5. Проверить логи `naive-auth`/`gost-proxy` — корректно тянут юзеров, нет ошибок TLS.
6. Тестовый коннект клиентом с sing-box-форматом подписки.

**RU — следующая по очереди**, не раскатан на момент написания. Перед раскаткой свериться, что
LE-серт под доменом RU существует (см. п.1).

---

## 8. Что осталось (не реализовано)

Идея от 30.06.2026, обсуждалась, но не сделана: дать сторонним xray-клиентам (например, Happ),
которые не запрашивают sing-box-формат подписки, нормальный TLS-fingerprint на `[tcp]`-хостах
(`chrome` и т.п.) — без вреда для AxiOm-клиента, поскольку он эти `[tcp]`-хосты в текущей схеме
не использует тем же путём. План:

1. Узнать реальный `User-Agent`, который Happ шлёт при запросе подписки.
2. Добавить правило в `subscription_settings.rules` панели: этот UA → формат `xray`/`links`.
3. Поставить `fingerprint` на хостах `[tcp]` (не повлияет на Naive — он в `xray`/`links`-формате
   и так не светится, см. §5).

Патчей панели для этого не требуется — используется штатный механизм UA→формат.
