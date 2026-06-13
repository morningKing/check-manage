import importlib

mig = importlib.import_module("migrations.2026_06_13_settings_hub_menu")


def test_migration_idempotent(db_conn):
    """运行两次，结果一致：无旧两棵树、存在唯一设置中心。"""
    mig.run()
    first = mig.run()  # 第二次不应报错
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM menus WHERE id IN ('menu-3','menu-3-b')")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM menus WHERE id = 'menu-settings'")
        assert cur.fetchone()[0] == 1
    assert first["inserted"] == "menu-settings"
