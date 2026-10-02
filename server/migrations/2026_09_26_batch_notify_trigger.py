# -*- coding: utf-8 -*-
"""PG LISTEN/NOTIFY trigger——子会话入队（status→pending）时通知 worker。

P3-C1：批任务子会话入队后，dispatcher 此前要等下一轮 2s poll 才 claim。
本迁移在 ai_chat_sessions 上挂 AFTER INSERT/UPDATE 行级触发器：status
变更为 'pending' 的瞬间 pg_notify('batch_claim_ready')，batch_engine 的
dispatcher 用专用 LISTEN 连接 select 唤醒立即 claim（连接断线仍有
POLL_INTERVAL/_wake.wait 轮询兜底，行为不回退）。

幂等：CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS 再建，可重跑。
注意 INSERT 分支 OLD 未赋值，plpgsql 中 OLD.status 取 NULL，
`NULL IS DISTINCT FROM 'pending'` 为真——新建 pending 行照常通知（已实测）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE OR REPLACE FUNCTION notify_batch_claim() RETURNS trigger AS $$
            BEGIN
                IF NEW.status = 'pending' AND OLD.status IS DISTINCT FROM 'pending' THEN
                    PERFORM pg_notify('batch_claim_ready', '');
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        cur.execute("""
            DROP TRIGGER IF EXISTS notify_batch_claim_ready ON ai_chat_sessions;
            CREATE TRIGGER notify_batch_claim_ready
                AFTER UPDATE OR INSERT ON ai_chat_sessions
                FOR EACH ROW EXECUTE FUNCTION notify_batch_claim()
        """)
        conn.commit()
    print("batch notify trigger ready.")


if __name__ == "__main__":
    run()
