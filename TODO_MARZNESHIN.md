# TODO после переезда на Marzneshin

> Список «надо сделать, но не сейчас». Актуально на 08.06.2026.
> Текущее состояние системы — в `MARZNESHIN_STATE.md`.

## ✅ Уже сделано (для контекста)
- Панель+ноды Marzneshin, бот продаж и V3IpLimit на новой панели, миграция живых юзеров, шим старых ссылок.
- Документация + репозиторий обновлены; device-ключ вынесен в `.env`.
- **Caddy-мост `/devices` на NL → FR** (08.06): выпущенные сборки приложения снова получают счётчик устройств.
- **`vpn-bot-test.service` на RU удалён** (08.06).
- **Lowercase-фикс резолва токена** в `v3iplimit/api/rest_api.py` (08.06): `_build_response`
  нормализует username — старые Marzban-токены (`Arco`) больше не давали `connected:0`.
- **Клиент v2 пересобран** (08.06): `_baseUrl` → FR + `--dart-define=DEVICE_API_KEY`;
  Android (split-per-abi APK) + Windows. Правки закоммичены в `AxiOm-v2`.

---

## 🟡 Осталось

### 1. Раздать новую сборку v2 пользователям
- Код и сборка готовы (FR-URL + ключ). Осталось **выложить/разослать** новые APK + Windows-сборку.
- ⚠️ Любую будущую сборку делать **с ключом**: `flutter build ... --dart-define=DEVICE_API_KEY=<key>`
  (Makefile его НЕ передаёт → без ключа счётчик скрыт). Запушить ветку `AxiOm-v2/master` на GitHub.
- ⚠️ Пока не все обновились — **не убирать `/devices` из Caddy на NL** даже после вывода Marzban.

### 2. ✅ Фаервол: порт нод `53042` — только с FR (сделано 10.06)
- На NL/PL/RU: `iptables -I INPUT ! -s IP_FR/32 -p tcp --dport 53042 -j DROP`.
- Персист: NL/PL — `netfilter-persistent save`; RU — юнит `axiom-firewall.service` (oneshot).
- Проверено: снаружи 53042 закрыт на всех трёх, с FR доступен, ноды healthy.

### 3. HY2 на мобильных (UDP 9444 режут операторы)
- VLESS/TCP работает, HY2 на мобиле часто не пингуется (UDP на нестандартном порту).
- Пробовать альтернативный UDP-порт. ⚠️ Готч: смена порта/конфига HY2 на нодах роняет sing-box юзеров —
  ОБЯЗАТЕЛЕН рестарт панели `docker restart marzneshin-marzneshin-1` на FR в конце (см. `MARZNESHIN_STATE.md`).

### 4. Полный вывод Marzban (после грейс-периода ~1–2 недели)
- Остановить панель Marzban на NL (`docker compose stop`). **Caddy + `sub-redirect` + `/devices`-мост оставить живыми.**
- Бэкап `/var/lib/marzban/db.sqlite3`.
- Удалить: остановленные `marzban-node` (PL/RU), старый `/opt/v3iplimit` (NL).
- В `config.py` бота `MARZBAN_PASSWORD` всё ещё `_require` — при выводе Marzban либо оставить заглушку
  в `.env`, либо убрать обязательность (иначе бот не стартует).
- (Опц.) убрать `@IPLIMIT_BOT` и Telegram-логгер Marzban `@LOGGER_BOT` в BotFather.

### 5. Ротация device-ключа (`DEVICE_API_KEY`)
- Отложено: репозиторий приватный. Значение есть в git-истории до 08.06.2026.
- Если делать: новый ключ в 3 местах — `/opt/v3iplimit/.env` (FR), `.env` бота (`V2IPLIMIT_API_KEY`, RU),
  клиент `device_count_provider.dart` (+ пересборка).
