# -*- coding: utf-8 -*-
"""幂等迁移：内置 MCP 工具治理两表（2026-10-25 spec §4）。

ai_mcp_tools —— 工具元数据快照（mcp-server 启动时 upsert + 清理消失行）；
ai_mcp_tool_rules —— 角色×工具全量矩阵（mcp-server 启动播种默认行，ON
CONFLICT DO NOTHING 不覆盖管理员选择；管理端 PUT 全量替换）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ai_mcp_tools ("
            " tool_name TEXT PRIMARY KEY,"
            " group_id TEXT NOT NULL,"
            " group_name TEXT NOT NULL,"
            " group_order INT NOT NULL DEFAULT 0,"
            " description TEXT NOT NULL DEFAULT '',"
            " access TEXT NOT NULL CHECK (access IN ('read','write')),"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ai_mcp_tool_rules ("
            " role TEXT NOT NULL,"
            " tool_name TEXT NOT NULL,"
            " allowed BOOLEAN NOT NULL,"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " PRIMARY KEY (role, tool_name))")
        conn.commit()
    print("mcp tool governance tables ready.")


if __name__ == "__main__":
    run()
