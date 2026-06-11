"""
Одноразовая чистка bot.db от «сирот» — строк subscriptions, чьих
пользователей больше нет в Marzban (например, удалены вручную из панели).

Логика безопасная:
  • Marzban отвечает 404 → строка считается сиротой и удаляется.
  • Marzban отвечает 200 → пользователь существует (в т.ч. expired) → строка остаётся.
  • Любой другой статус → строку НЕ трогаем, печатаем для разбора.

Запуск на RU-сервере (там, где лежит боевая bot.db):
    cd /opt/vpn-bot
    /opt/vpn-bot/venv/bin/python cleanup_orphans.py            # показать, что будет удалено (dry-run)
    /opt/vpn-bot/venv/bin/python cleanup_orphans.py --apply    # реально удалить

Перед --apply сделай бэкап:
    cp bot.db bot.db.bak.$(date +%Y%m%d_%H%M%S)
"""

import asyncio
import sqlite3
import sys

import aiohttp

import config

DRY_RUN = "--apply" not in sys.argv


async def main():
    async with aiohttp.ClientSession() as s:
        r = await s.post(
            f"{config.MARZBAN_URL}/api/admin/token",
            data={"username": config.MARZBAN_USERNAME, "password": config.MARZBAN_PASSWORD},
        )
        token = (await r.json())["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        conn = sqlite3.connect("bot.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, marzban_username, tariff_name FROM subscriptions"
        ).fetchall()

        orphans, kept, unknown = [], [], []
        for row in rows:
            resp = await s.get(
                f"{config.MARZBAN_URL}/api/user/{row['marzban_username']}",
                headers=headers,
            )
            if resp.status == 404:
                orphans.append(dict(row))
            elif resp.status == 200:
                kept.append(row["marzban_username"])
            else:
                unknown.append((row["marzban_username"], resp.status))

        print(f"Всего: {len(rows)} | сироты(404): {len(orphans)} | живые(200): {len(kept)} | прочее: {len(unknown)}")
        for o in orphans:
            print(f"  {'WOULD DEL' if DRY_RUN else 'DEL'} id={o['id']:>3}  {o['marzban_username']:<24} {o['tariff_name']}")
        if unknown:
            print("  UNKNOWN (не трогаю):", unknown)

        if DRY_RUN:
            print("\nDRY-RUN: ничего не удалено. Запусти с --apply, чтобы применить.")
            return

        for o in orphans:
            conn.execute("DELETE FROM subscriptions WHERE id = ?", (o["id"],))
        conn.commit()
        print("Удалено:", len(orphans), "| осталось:",
              conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0])


if __name__ == "__main__":
    asyncio.run(main())
