"""e2e toolbox DB 桥：stdin 读 SQL（可多语句）经 server 的 DB_CONFIG 执行。
SELECT 打印 JSON 行数组；DDL/DML 打印 []。仅供 e2e 确定性种子使用。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'server'))
from config import DB_CONFIG  # noqa: E402

import psycopg2  # noqa: E402

conn = psycopg2.connect(**DB_CONFIG)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute(sys.stdin.read())
    rows = [list(r) for r in cur.fetchall()] if cur.description else []
print(json.dumps(rows, default=str, ensure_ascii=False))
