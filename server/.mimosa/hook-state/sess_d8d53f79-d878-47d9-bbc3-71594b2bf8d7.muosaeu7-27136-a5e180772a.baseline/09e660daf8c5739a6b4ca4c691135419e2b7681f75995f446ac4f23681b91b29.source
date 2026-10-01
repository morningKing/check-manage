import importlib
mig = importlib.import_module("migrations.2026_06_14_workflow_tables")


def test_migration_creates_tables(db_conn):
    mig.run()
    mig.run()  # 幂等
    with db_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.workflow_definitions')")
        assert cur.fetchone()[0] is not None
        cur.execute("SELECT to_regclass('public.workflow_instances')")
        assert cur.fetchone()[0] is not None
