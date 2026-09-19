# -*- coding: utf-8 -*-
"""幂等迁移：AI 定时任务支持「AI 产出文件回填数据行」。

新增 ai_scan_tasks.output_file_field：指定记录上一个 file/image 字段。
子会话成功完成后，扫描引擎把其工作区 outputs/ 下的产物导入 data_files，
并以 {uid, name} 追加到该字段——AI 产出文件像手工上传一样在数据行呈现。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    "ALTER TABLE ai_scan_tasks ADD COLUMN IF NOT EXISTS output_file_field "
    "VARCHAR(100)",
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
    print("ai_scan_tasks.output_file_field ready.")
