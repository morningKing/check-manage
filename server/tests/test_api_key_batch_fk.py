"""
api_keys 被删除时，ai_chat_batches.api_key_id 外键行为的真实 DB 集成测试。

`api_keys.owner_user_id`（Task 1）之后，`ai_chat_batches.api_key_id` 迁移最初写成
`REFERENCES api_keys(id)`（默认 ON DELETE NO ACTION）。一旦某个密钥创建过批任务，
管理员在 /admin/api-keys 硬删这个密钥（`DELETE FROM api_keys ...`，见
routes/api_keys.py::delete_api_key）就会撞外键约束抛出未捕获的
psycopg2.errors.ForeignKeyViolation（app.py 没有全局 errorhandler 兜底），密钥永久
删不掉。改成 ON DELETE SET NULL 后，删除应该成功，批任务原样保留、api_key_id 置空
（语义上等同于 UI 创建的批任务，对外接口不可见）。

这个测试直接操作真实 Postgres（不 mock get_db），跟 test_create_concurrency.py 同一套路：
显式插入 → 触发 → 断言 → teardown 清理。
"""
import uuid

from db import get_db


def _cleanup(key_id, batch_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (batch_id,))
        cur.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
        conn.commit()


def test_delete_api_key_with_batches_succeeds_and_nulls_api_key_id():
    key_id = f'ak-fktest-{uuid.uuid4().hex[:8]}'
    batch_id = f'batch-fktest-{uuid.uuid4().hex[:8]}'

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO api_keys (id, name, key_hash) VALUES (%s, %s, %s)",
            (key_id, 'FK 测试密钥', 'fake-hash-for-fk-test'),
        )
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, api_key_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (batch_id, 'user-admin', 'FK 测试批任务', 'prompt', key_id),
        )
        conn.commit()

    try:
        # 删除仍然被批任务引用的密钥必须成功，不能抛外键异常。
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
            conn.commit()

        # 密钥确实没了。
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM api_keys WHERE id = %s", (key_id,))
            assert cur.fetchone() is None

        # 批任务本身还在，只是 api_key_id 被置空。
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT api_key_id FROM ai_chat_batches WHERE id = %s", (batch_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] is None
    finally:
        _cleanup(key_id, batch_id)


def test_delete_owner_user_nulls_api_key_owner():
    """密钥属主被删除 → api_keys.owner_user_id 自动置空（ON DELETE SET NULL）。

    这是一道 fail-safe：置空后的密钥被 routes/open_api_batches.require_bound_key
    当作「未绑定」挡在 AI 批任务接口之外（403），不会再有人以一个已注销用户的
    名义跑 AI、烧他的额度。默认的 NO ACTION 则是把 users 行钉死删不掉。
    """
    uid = f'user-fktest-{uuid.uuid4().hex[:8]}'
    key_id = f'ak-ownertest-{uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', %s, 'developer')",
            (uid, f'fkowner_{uid[-8:]}', 'FK 属主测试'),
        )
        cur.execute(
            "INSERT INTO api_keys (id, name, key_hash, owner_user_id) "
            "VALUES (%s, %s, %s, %s)",
            (key_id, 'FK 属主测试密钥', f'fake-hash-{key_id}', uid),
        )
        conn.commit()

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))   # 不能抛外键异常
            conn.commit()

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT owner_user_id FROM api_keys WHERE id = %s", (key_id,))
            row = cur.fetchone()
            assert row is not None, '密钥不该被连带删除'
            assert row[0] is None, '属主被删后 owner_user_id 必须置空'
    finally:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()
