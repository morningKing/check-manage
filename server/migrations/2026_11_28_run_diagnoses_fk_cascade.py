# -*- coding: utf-8 -*-
"""幂等迁移：ai_run_diagnoses.attempt_id 外键补 ON DELETE CASCADE。

2026_10_27_run_diagnoses 建表时 `REFERENCES ai_execution_attempts(id)` 漏了
ON DELETE 动作（默认 NO ACTION），而同库其余引用 attempts 的表（执行审计
明细 / skillopt_p2 / skill_fit）一律 ON DELETE CASCADE。后果：删除批任务
→ 级联删 ai_chat_sessions → 级联删 ai_execution_attempts 时被
ai_run_diagnoses 挡住，报「在 ai_execution_attempts 上的更新或删除违反了
在 ai_run_diagnoses 上的外键约束」，批任务删不掉。

按 pg_constraint 实际探查约束（兼容默认名 *_fkey 与任意历史命名）：已存在
CASCADE 约束则原样保留（幂等快路径）；否则删掉全部旧 FK、以固定名重建带
CASCADE 的新约束。约束名落点用 %I / 字面量 DDL，不做 Python 侧字符串拼 SQL。
表不存在时直接跳过——全新库按注册顺序先跑 2026_10_27 建表，随后本迁移生效。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.ai_run_diagnoses')")
        if cur.fetchone()[0] is None:
            print("run diagnoses fk cascade skipped: table absent.")
            return
        cur.execute("""
            DO $mig$
            DECLARE
                r RECORD;
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'ai_run_diagnoses'::regclass
                      AND confrelid = 'ai_execution_attempts'::regclass
                      AND contype = 'f'
                      AND confdeltype = 'c') THEN
                    -- 已有 ON DELETE CASCADE 约束：幂等快路径，不动
                    RAISE NOTICE 'ai_run_diagnoses attempt_id already ON DELETE CASCADE';
                ELSE
                    FOR r IN
                        SELECT conname FROM pg_constraint
                        WHERE conrelid = 'ai_run_diagnoses'::regclass
                          AND confrelid = 'ai_execution_attempts'::regclass
                          AND contype = 'f'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE ai_run_diagnoses DROP CONSTRAINT %I',
                            r.conname);
                    END LOOP;
                    ALTER TABLE ai_run_diagnoses
                        ADD CONSTRAINT ai_run_diagnoses_attempt_fk
                        FOREIGN KEY (attempt_id)
                        REFERENCES ai_execution_attempts(id)
                        ON DELETE CASCADE;
                END IF;
            END
            $mig$;""")
        conn.commit()
    print("run diagnoses fk cascade migration ready.")


if __name__ == "__main__":
    run()
