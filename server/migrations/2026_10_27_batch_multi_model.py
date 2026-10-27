# -*- coding: utf-8 -*-
"""幂等迁移：批任务多模型分配——ai_chat_batches 加 models JSONB 列。

多模型批在创建/追加时把模型列表按 batch_seq 轮询分配进子会话已有的
ai_chat_batches→ai_chat_sessions.model 列（该列原为开放 API standalone
会话服务）；批行 model 列保留 = models[0]，既有读者（性能分析/开放 API
文档口径）不受影响。models 为空/单值 → 子会话 model 留 NULL，派发走
批级 model，与旧语义完全一致。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE ai_chat_batches ADD COLUMN IF NOT EXISTS models JSONB")
        conn.commit()
    print("batch multi-model migration ready.")


if __name__ == "__main__":
    run()
