"""幂等迁移：把"数据工具"+"系统配置"菜单子树塌缩为单一"设置中心"。

可重复执行：每次先删旧子树（含全部后代），再 upsert 设置中心菜单。
用法（在 server/ 目录下）：
    python -m migrations.2026_06_13_settings_hub_menu
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras
from db import get_db

OLD_ROOT_IDS = ["menu-3", "menu-3-b"]
SETTINGS_MENU = {
    "id": "menu-settings", "name": "设置中心", "icon": "Setting",
    "page_id": None, "parent_id": None, "order": 4,
    "path": "/admin", "roles": ["admin"],
}


def _collect_descendants(cur, root_ids):
    """广度优先收集 root_ids 及其全部后代 id。"""
    to_delete, frontier = set(root_ids), list(root_ids)
    while frontier:
        cur.execute("SELECT id FROM menus WHERE parent_id = ANY(%s)", (frontier,))
        children = [r[0] for r in cur.fetchall()]
        new = [c for c in children if c not in to_delete]
        to_delete.update(new)
        frontier = new
    return list(to_delete)


def run():
    with get_db() as conn:
        cur = conn.cursor()
        ids = _collect_descendants(cur, OLD_ROOT_IDS)
        if ids:
            cur.execute("DELETE FROM menus WHERE id = ANY(%s)", (ids,))
        # upsert 设置中心：先删后插，保证可重复执行
        cur.execute("DELETE FROM menus WHERE id = %s", (SETTINGS_MENU["id"],))
        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            (SETTINGS_MENU["id"], SETTINGS_MENU["name"], SETTINGS_MENU["icon"],
             SETTINGS_MENU["page_id"], SETTINGS_MENU["parent_id"], SETTINGS_MENU["order"],
             SETTINGS_MENU["path"], psycopg2.extras.Json(SETTINGS_MENU["roles"])),
        )
        conn.commit()
    return {"deleted": ids, "inserted": SETTINGS_MENU["id"]}


if __name__ == "__main__":
    print(run())
