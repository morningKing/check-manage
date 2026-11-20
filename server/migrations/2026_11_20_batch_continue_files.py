# -*- coding: utf-8 -*-
"""幂等迁移：继续对话带附件——ai_chat_sessions 加 continue_files JSONB 列。

Open API POST /v1/ai-batches/<batchId>/sessions/<childId>/continue 的可选
files 参数（[{"name","path"}]，path 为 /uploads 暂存区相对路径）落在本列，
由 batch_engine 在 continue 轮把暂存文件复制进子会话既有工作区的 uploads/
后与 continue_prompt 一同消费清空。形态与 input_files JSONB 一致；NULL =
本轮无附件，旧行为逐字节不变。接管页与内部继续对话路由不传 files，不受影响。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS continue_files JSONB")
        conn.commit()
    print("batch continue-files migration ready.")


if __name__ == "__main__":
    run()
