# AxiOm — VPN sales bot + architecture

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.12-green)

**AxiOm VPN** — privacy infrastructure built on a Marzneshin panel, multi-node device
limiter (V3IpLimit), Telegram sales/support bots and a Flutter client. This repository holds
the Telegram sales bot source and the full technical architecture reference of the system.

> 🌍 *English:* the code comments and docs are in Russian; the architecture overview
> ([`AxiOm_Architecture.md`](AxiOm_Architecture.md)) is the single source of truth for the
> whole system (servers, panel, device limiter, tariffs, payments, referrals).

Исходники Telegram-бота покупок и полная техническая документация инфраструктуры.

> 📖 **Полное описание системы** (серверы, Marzban, V3IpLimit, тарифы, оплата, рефералы,
> напоминания, история изменений) — в [`AxiOm_Architecture.md`](AxiOm_Architecture.md).
> Это главный источник истины, держим его в актуальном состоянии.

---

## Что в репозитории

| Файл | Назначение |
|------|-----------|
| `bot.py` | Telegram-бот покупок (aiogram 3): покупка/продление/триал через Платёжку, личный кабинет, рефералы, напоминания об окончании |
| `db.py` | SQLite (`bot.db`): пользователи, подписки, платежи, рефералы, напоминания |
| `config.py` | Конфиг и тарифы. Все секреты читаются из `.env` (`_require`), в коде их нет |
| `web_api.py` | HTTP-API для веб-покупок с лендинга + webhook Платёжкы (aiohttp, порт 8080) |
| `cleanup_orphans.py` | Утилита чистки БД от «сирот» (подписок, удалённых из Marzban) |
| `requirements.txt` | Зависимости Python |
| `landing/` | Лендинг `axiom.DOMAIN` (веб-покупки + реферал `?ref`). Деплой: `scp` на RU `/var/www/landing/` |
| `v3iplimit/` | IP-лимитер / device-limit (форк V2IpLimit, MIT). Работает на **NL** `/opt/v3iplimit/`, конфиг — `/root/config.json` (вне репо). См. §6 архитектуры |
| `AxiOm_Architecture.md` | Архитектура всей системы |

**Не в репозитории** (исключены `.gitignore`): `.env` и `config.json` (секреты), `bot.db`
(живые данные), `venv/`, `__pycache__/`, локальные бэкапы `bak-*`.

---

## Где работает

Бот покупок крутится на **RU-сервере** (`IP_RU`, SSH-порт **2222**):

- Рабочая директория: `/opt/vpn-bot/`
- Запуск: `systemd`-юнит `vpn-bot.service` (venv `/opt/vpn-bot/venv/`)
- Бэкенд: Marzban на NL (`https://vpn.DOMAIN`), оплата — Платёжка

Подробнее (серверы, DNS, ноды, V3IpLimit) — в `AxiOm_Architecture.md`, §2–§6.

---

## Локальный запуск / разработка

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
# создать .env (см. ниже), затем:
venv/bin/python bot.py
```

### Нужный `.env` (значения — у владельца, в репозиторий не коммитятся)

```
BOT_TOKEN=...            # токен Telegram-бота
ADMIN_IDS=123,456        # ID админов через запятую
CARD_NUMBER=...          # реквизиты для продления (карта)
CARD_HOLDER=...
MARZBAN_PASSWORD=...     # пароль API-пользователя API_USER в Marzban
V2IPLIMIT_API_KEY=...    # ключ REST API V3IpLimit
PAYMENT_SHOP_ID=...     # боевой магазин Платёжкы
PAYMENT_SECRET=...      # боевой секретный ключ live_...
```

---

## Деплой на прод (RU)

```powershell
# выкатить изменённые файлы и перезапустить бота
scp -P 2222 bot.py db.py config.py web_api.py root@IP_RU:/opt/vpn-bot/
ssh -p 2222 root@IP_RU "systemctl restart vpn-bot && journalctl -u vpn-bot -n 5 --no-pager"
```

После рестарта в логах должно быть `🚀 AxiOm VPN запущен`.

---

## Рабочий процесс (важно)

1. **Правим только локально** в этом репозитории, затем `git commit` → `git push`.
2. **Деплоим** на сервер из репозитория (`scp` выше).
3. **Не редактируем файлы на сервере напрямую** — иначе правки расходятся с репозиторием
   и затираются при следующем деплое. Если что-то правилось на сервере «по-быстрому» —
   сначала затянуть это в репо (`scp` обратно + commit), потом продолжать.

Перед деплоем полезно проверить синтаксис: `python -m py_compile bot.py db.py config.py web_api.py`.

---

## Git-процесс: ветки и Pull Request

Классические правила, чтобы не ломать прод и не затирать чужие правки:

1. **`main` — всегда рабочая и деплоится.** В `main` напрямую **не коммитим**. На сервер
   выкатываем только то, что уже в `main`.
2. **Новая фича или фикс — в отдельной ветке** от актуального `main`:
   ```bash
   git checkout main
   git pull                       # подтянуть свежий main
   git checkout -b feature/имя    # фичи: feature/...  фиксы: fix/...
   ```
3. **Коммиты — маленькие и осмысленные.** Сообщение по-русски, в повелительном
   наклонении: «Добавить welcome-бонус приглашённому», «Починить таймаут Платёжкы».
4. **Готово → пушим ветку и открываем Pull Request:**
   ```bash
   git push -u origin feature/имя
   gh pr create --fill            # или открыть PR на github.com
   ```
5. **Ревью → merge в `main`** (через PR, не напрямую). После merge ветку удаляем:
   ```bash
   git branch -d feature/имя && git push origin --delete feature/имя
   ```
6. **Деплоим на прод только из `main`** (после merge), командами из раздела «Деплой».
7. **Конфликты** решаем локально: подмёржить свежий `main` в свою ветку
   (`git merge main` или `git rebase main`), проверить `py_compile`, потом обновить PR.

> Кратко: один человек = одна ветка = один PR. Прод всегда отражает `main`.

---

## Безопасность

- Секреты живут **только** в `.env` на сервере (и у владельца). В репозитории их нет —
  не добавляйте их даже временно.
- `bot.db` — персональные данные пользователей, в репозиторий не попадает.

---

## License

This project is released under the [MIT License](LICENSE).

The bundled `v3iplimit/` directory is a fork of
[V2IpLimit](https://github.com/houshmand-2005/V2IpLimit) and retains its own MIT license
(`v3iplimit/LICENSE`, © 2023 Houshmand).
