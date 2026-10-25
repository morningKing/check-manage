# -*- coding: utf-8 -*-
"""幂等迁移：platform_plugins 表 + 按注册表种子 + admin.plugins 权限目录行。

种子 ON CONFLICT (plugin_id) DO NOTHING——重启/重跑永不重置管理员选择
（plugin-architecture spec §4）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    from utils.plugin_registry import PLUGINS
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS platform_plugins ("
            " plugin_id TEXT PRIMARY KEY,"
            " enabled BOOLEAN NOT NULL DEFAULT FALSE,"
            " config JSONB NOT NULL DEFAULT '{}',"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " updated_by TEXT)")
        for m in PLUGINS.values():
            cur.execute(
                "INSERT INTO platform_plugins (plugin_id, enabled) VALUES (%s, %s)"
                " ON CONFLICT (plugin_id) DO NOTHING",
                (m.id, m.default_enabled))
        cur.execute(
            "INSERT INTO role_permissions (role_id, permission_key)"
            " VALUES ('admin', 'admin.plugins') ON CONFLICT DO NOTHING")
        conn.commit()
    print("platform plugins migration ready.")


if __name__ == "__main__":
    run()
