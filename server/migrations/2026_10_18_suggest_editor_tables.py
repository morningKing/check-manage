# -*- coding: utf-8 -*-
"""幂等迁移：智能写作（suggest-editor）三张表 + 内置模板 seed + 侧边栏菜单。

- se_documents：文档元数据（正文与建议本体在 se_document_states 的 Yjs 二进制里）
- se_document_states：每文档一份 Y.encodeStateAsUpdate（BYTEA）+ 已分析块 hash（JSONB）
- se_templates：写作模板（content_html 为 Tiptap HTML，客户端 setContent 应用）

菜单走 settings-hub 同款幂等 upsert：path 直接对上前端静态路由 /suggest-editor。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras
from db import get_db

MENU = {
    "id": "menu-suggest-editor", "name": "智能写作", "icon": "EditPen",
    "page_id": None, "parent_id": None, "order": 3,
    "path": "/suggest-editor", "roles": ["admin", "developer"], "menu_type": "system",
}

# 内置模板：content_html 只用 StarterKit+TaskList 覆盖的节点，客户端 setContent 应用
BUILTIN_TEMPLATES = [
    {
        "id": "se-tpl-blank", "name": "空白文档", "description": "从零开始写作，停笔后自动获得优化建议",
        "is_builtin": True, "sort_order": 10,
        "content_html": "",
    },
    {
        "id": "se-tpl-weekly", "name": "项目周报", "description": "本周完成 / 下周计划 / 风险与求助",
        "is_builtin": True, "sort_order": 20,
        "content_html": (
            "<h1>本周工作汇报</h1>"
            "<h2>本周完成</h2><ul><li>【事项一：简述做了什么、结果如何】</li><li>【事项二】</li></ul>"
            "<h2>下周计划</h2><ul><li>【计划一】</li><li>【计划二】</li></ul>"
            "<h2>风险与求助</h2><p>【当前阻塞、需要的资源或决策】</p>"
        ),
    },
    {
        "id": "se-tpl-meeting", "name": "会议纪要", "description": "主题 / 结论 / 行动项（任务清单）",
        "is_builtin": True, "sort_order": 30,
        "content_html": (
            "<h1>会议纪要</h1>"
            "<p><strong>主题：</strong>【议题】　<strong>时间：</strong>【日期】　<strong>参会人：</strong>【名单】</p>"
            "<h2>会议结论</h2><ol><li>【结论一】</li><li>【结论二】</li></ol>"
            "<h2>行动项</h2>"
            '<ul data-type="taskList">'
            '<li data-checked="false" data-type="taskItem"><label><input type="checkbox"><span></span></label>'
            "<div><p>【行动项 · 负责人 · 截止时间】</p></div></li>"
            '<li data-checked="false" data-type="taskItem"><label><input type="checkbox"><span></span></label>'
            "<div><p>【行动项 · 负责人 · 截止时间】</p></div></li>"
            "</ul>"
        ),
    },
    {
        "id": "se-tpl-proposal", "name": "方案建议书", "description": "背景 / 目标 / 方案 / 预期收益",
        "is_builtin": True, "sort_order": 40,
        "content_html": (
            "<h1>方案建议书</h1>"
            "<h2>一、背景</h2><p>【当前面临的问题与机会】</p>"
            "<h2>二、目标</h2><p>【要达成的可衡量结果】</p>"
            "<h2>三、方案</h2><p>【总体思路与关键举措】</p>"
            "<blockquote><p>【备选方案与取舍理由】</p></blockquote>"
            "<h2>四、预期收益</h2><p>【收益测算与验收标准】</p>"
        ),
    },
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS se_documents ("
            " id TEXT PRIMARY KEY,"
            " title TEXT NOT NULL,"
            " owner_user_id TEXT NOT NULL,"
            " template_id TEXT,"
            " status VARCHAR(16) NOT NULL DEFAULT 'active',"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " last_opened_at TIMESTAMPTZ)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_se_documents_owner "
                    "ON se_documents (owner_user_id, status, updated_at DESC)")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS se_document_states ("
            " document_id TEXT PRIMARY KEY,"
            " state BYTEA,"
            " analyzed_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS se_templates ("
            " id TEXT PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " description TEXT,"
            " content_html TEXT NOT NULL DEFAULT '',"
            " is_builtin BOOLEAN NOT NULL DEFAULT FALSE,"
            " owner_user_id TEXT,"
            " sort_order INT NOT NULL DEFAULT 100,"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")

        for tpl in BUILTIN_TEMPLATES:
            cur.execute(
                "INSERT INTO se_templates (id, name, description, content_html, is_builtin, sort_order) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (tpl["id"], tpl["name"], tpl["description"], tpl["content_html"],
                 tpl["is_builtin"], tpl["sort_order"]))

        cur.execute(
            'INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) '
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (MENU["id"], MENU["name"], MENU["icon"], MENU["page_id"], MENU["parent_id"],
             MENU["order"], MENU["path"], psycopg2.extras.Json(MENU["roles"]), MENU["menu_type"]))
        conn.commit()
    print("suggest editor tables/templates/menu migration ready.")


if __name__ == "__main__":
    run()
