from migrate_kefu import migrate_kefu


def test_migrate_adds_panel_blocks(db_conn):
    migrate_kefu(db_conn)
    migrate_kefu(db_conn)  # idempotent
    cur = db_conn.cursor()
    cur.execute("SELECT 1 FROM information_schema.columns "
                "WHERE table_name='kefu_instances' AND column_name='panel_blocks'")
    assert cur.fetchone() is not None
    cur.execute("SELECT column_default FROM information_schema.columns "
                "WHERE table_name='kefu_instances' AND column_name='panel_blocks'")
    assert "'[]'" in (cur.fetchone()[0] or '')
    db_conn.rollback()
