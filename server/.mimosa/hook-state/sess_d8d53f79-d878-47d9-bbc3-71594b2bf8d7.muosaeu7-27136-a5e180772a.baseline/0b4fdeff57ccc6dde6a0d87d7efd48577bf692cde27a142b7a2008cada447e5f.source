# -*- coding: utf-8 -*-
"""
质量度量仪表盘完整配置脚本

包含：
1. 数据页配置（项目管理、测试问题）
2. 菜单结构
3. 测试数据（需求、问题）
4. 关联关系
5. 仪表盘配置（11个图表，使用新的 metrics 格式）

使用方法：
cd server && python test_scripts/create_quality_dashboard.py
"""
import sys
import os

# 添加 server 目录到路径，以便导入 config
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import psycopg2
import psycopg2.extras
from config import DB_CONFIG
from datetime import datetime, timezone
import json
import uuid

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()

def gen_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')

print("=" * 60)
print("质量度量仪表盘配置脚本")
print("=" * 60)

# ============================================
# 1. 创建数据页配置
# ============================================
print("\n[1] 创建数据页配置...")

# 项目管理（需求跟踪）
req_page_id = "page-quality-project"
req_page_fields = [
    {
        "id": "f1",
        "fieldName": "reqNo",
        "label": "需求编号",
        "controlType": "text",
        "required": True,
        "order": 1,
        "isPrimaryKey": True
    },
    {
        "id": "f2",
        "fieldName": "reqName",
        "label": "需求名称",
        "controlType": "text",
        "required": True,
        "order": 2
    },
    {
        "id": "f3",
        "fieldName": "version",
        "label": "版本号",
        "controlType": "select",
        "required": True,
        "order": 3,
        "options": [
            {"label": "V1.0", "value": "V1.0"},
            {"label": "V1.1", "value": "V1.1"},
            {"label": "V2.0", "value": "V2.0"},
            {"label": "V2.1", "value": "V2.1"}
        ]
    },
    {
        "id": "f4",
        "fieldName": "status",
        "label": "状态",
        "controlType": "select",
        "required": True,
        "order": 4,
        "options": [
            {"label": "开发中", "value": "dev"},
            {"label": "已提测", "value": "testing"},
            {"label": "已上线", "value": "released"}
        ]
    },
    {
        "id": "f5",
        "fieldName": "priority",
        "label": "优先级",
        "controlType": "select",
        "required": False,
        "order": 5,
        "options": [
            {"label": "P0", "value": "P0"},
            {"label": "P1", "value": "P1"},
            {"label": "P2", "value": "P2"}
        ]
    },
    {
        "id": "f6",
        "fieldName": "relatedBugs",
        "label": "关联问题",
        "controlType": "relation",
        "required": False,
        "order": 6,
        "relationConfig": {
            "targetCollection": "quality-bug",
            "displayField": "bugNo",
            "targetField": "relatedReq"
        }
    }
]

cur.execute("SELECT id FROM page_configs WHERE id = %s", (req_page_id,))
if cur.fetchone():
    cur.execute("UPDATE page_configs SET fields = %s, updated_at = %s WHERE id = %s",
                (psycopg2.extras.Json(req_page_fields), now, req_page_id))
    print(f"  更新页面配置: {req_page_id}")
else:
    cur.execute("""
        INSERT INTO page_configs (id, name, description, api_endpoint, fields, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (req_page_id, "项目管理", "需求跟踪数据页", "/api/quality-project",
          psycopg2.extras.Json(req_page_fields), now, now))
    print(f"  创建页面配置: {req_page_id}")

# 测试问题（缺陷跟踪）
bug_page_id = "page-quality-bug"
bug_page_fields = [
    {
        "id": "b1",
        "fieldName": "bugNo",
        "label": "问题编号",
        "controlType": "text",
        "required": True,
        "order": 1,
        "isPrimaryKey": True
    },
    {
        "id": "b2",
        "fieldName": "title",
        "label": "问题标题",
        "controlType": "text",
        "required": True,
        "order": 2
    },
    {
        "id": "b3",
        "fieldName": "severity",
        "label": "严重程度",
        "controlType": "select",
        "required": True,
        "order": 3,
        "options": [
            {"label": "致命", "value": "fatal"},
            {"label": "严重", "value": "major"},
            {"label": "一般", "value": "minor"},
            {"label": "建议", "value": "suggestion"}
        ]
    },
    {
        "id": "b4",
        "fieldName": "status",
        "label": "状态",
        "controlType": "select",
        "required": True,
        "order": 4,
        "options": [
            {"label": "新建", "value": "new"},
            {"label": "已修复", "value": "fixed"},
            {"label": "已关闭", "value": "closed"},
            {"label": "重新打开", "value": "reopened"}
        ]
    },
    {
        "id": "b5",
        "fieldName": "relatedReq",
        "label": "关联需求",
        "controlType": "relation",
        "required": False,
        "order": 5,
        "relationConfig": {
            "targetCollection": "quality-project",
            "displayField": "reqNo",
            "targetField": "relatedBugs"
        }
    },
    {
        "id": "b6",
        "fieldName": "assignee",
        "label": "处理人",
        "controlType": "text",
        "required": False,
        "order": 6
    }
]

cur.execute("SELECT id FROM page_configs WHERE id = %s", (bug_page_id,))
if cur.fetchone():
    cur.execute("UPDATE page_configs SET fields = %s, updated_at = %s WHERE id = %s",
                (psycopg2.extras.Json(bug_page_fields), now, bug_page_id))
    print(f"  更新页面配置: {bug_page_id}")
else:
    cur.execute("""
        INSERT INTO page_configs (id, name, description, api_endpoint, fields, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (bug_page_id, "测试问题", "缺陷跟踪数据页", "/api/quality-bug",
          psycopg2.extras.Json(bug_page_fields), now, now))
    print(f"  创建页面配置: {bug_page_id}")

conn.commit()

# ============================================
# 2. 创建菜单结构
# ============================================
print("\n[2] 创建菜单结构...")

# 查找工作空间
cur.execute("SELECT id FROM menus WHERE menu_type = 'workspace' LIMIT 1")
ws = cur.fetchone()
workspace_id = ws[0] if ws else None

if not workspace_id:
    workspace_id = "ws-quality"
    cur.execute("""
        INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type, project_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (workspace_id, '质量管理工作空间', 'DataAnalysis', None, None, 100, '/quality-workspace',
          json.dumps(['admin', 'developer', 'guest']), 'workspace', workspace_id))
    print(f"  创建工作空间: {workspace_id}")

# 项目菜单（二级）
project_menu_id = "menu-quality-project"
cur.execute("SELECT id FROM menus WHERE id = %s", (project_menu_id,))
if not cur.fetchone():
    cur.execute("""
        INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type, project_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (project_menu_id, '质量度量项目', 'Monitor', None, workspace_id, 10, '/quality-project',
          json.dumps(['admin', 'developer', 'guest']), 'project', project_menu_id))
    print(f"  创建项目菜单: {project_menu_id}")

# 需求管理数据页（三级）
req_menu_id = "menu-quality-req-data"
cur.execute("SELECT id FROM menus WHERE id = %s", (req_menu_id,))
if not cur.fetchone():
    cur.execute("""
        INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type, project_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (req_menu_id, '需求管理', 'Document', req_page_id, project_menu_id, 10, '/quality-req',
          json.dumps(['admin', 'developer', 'guest']), 'data', project_menu_id))
    print(f"  创建数据菜单: {req_menu_id}")

# 测试问题数据页（三级）
bug_menu_id = "menu-quality-bug"
cur.execute("SELECT id FROM menus WHERE id = %s", (bug_menu_id,))
if not cur.fetchone():
    cur.execute("""
        INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type, project_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (bug_menu_id, '测试问题', 'Warning', bug_page_id, project_menu_id, 11, '/quality-bug',
          json.dumps(['admin', 'developer', 'guest']), 'data', project_menu_id))
    print(f"  创建数据菜单: {bug_menu_id}")

# 仪表盘菜单（三级）
dashboard_menu_id = "menu-quality-dashboard"
cur.execute("SELECT id FROM menus WHERE id = %s", (dashboard_menu_id,))
if not cur.fetchone():
    cur.execute("""
        INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type, project_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (dashboard_menu_id, '质量度量看板', 'DataAnalysis', None, project_menu_id, 12, '/quality-dashboard',
          json.dumps(['admin', 'developer', 'guest']), 'data', project_menu_id))
    print(f"  创建仪表盘菜单: {dashboard_menu_id}")

conn.commit()

# ============================================
# 3. 创建测试数据
# ============================================
print("\n[3] 创建测试数据...")

# 需求数据
requirements = [
    # V1.0 - 5个需求（已上线）
    {"id": "req-001", "reqNo": "REQ-001", "reqName": "用户登录功能", "version": "V1.0", "status": "released", "priority": "P0"},
    {"id": "req-002", "reqNo": "REQ-002", "reqName": "用户注册功能", "version": "V1.0", "status": "released", "priority": "P0"},
    {"id": "req-003", "reqNo": "REQ-003", "reqName": "权限管理", "version": "V1.0", "status": "released", "priority": "P1"},
    {"id": "req-004", "reqNo": "REQ-004", "reqName": "首页布局优化", "version": "V1.0", "status": "released", "priority": "P2"},
    {"id": "req-005", "reqNo": "REQ-005", "reqName": "消息通知", "version": "V1.0", "status": "released", "priority": "P1"},
    # V1.1 - 3个需求（已上线）
    {"id": "req-006", "reqNo": "REQ-006", "reqName": "数据导出", "version": "V1.1", "status": "released", "priority": "P0"},
    {"id": "req-007", "reqNo": "REQ-007", "reqName": "报表统计", "version": "V1.1", "status": "released", "priority": "P1"},
    {"id": "req-008", "reqNo": "REQ-008", "reqName": "多语言支持", "version": "V1.1", "status": "released", "priority": "P2"},
    # V2.0 - 4个需求（提测/开发中）
    {"id": "req-009", "reqNo": "REQ-009", "reqName": "移动端适配", "version": "V2.0", "status": "testing", "priority": "P0"},
    {"id": "req-010", "reqNo": "REQ-010", "reqName": "性能优化", "version": "V2.0", "status": "testing", "priority": "P0"},
    {"id": "req-011", "reqNo": "REQ-011", "reqName": "API网关", "version": "V2.0", "status": "dev", "priority": "P1"},
    {"id": "req-012", "reqNo": "REQ-012", "reqName": "日志系统", "version": "V2.0", "status": "dev", "priority": "P1"},
    # V2.1 - 2个需求（开发中）
    {"id": "req-013", "reqNo": "REQ-013", "reqName": "AI助手", "version": "V2.1", "status": "dev", "priority": "P0"},
    {"id": "req-014", "reqNo": "REQ-014", "reqName": "自动化测试", "version": "V2.1", "status": "dev", "priority": "P1"},
]

for req in requirements:
    cur.execute("SELECT id FROM dynamic_data WHERE id = %s", (req["id"],))
    if not cur.fetchone():
        data = {k: v for k, v in req.items() if k != "id"}
        cur.execute("""
            INSERT INTO dynamic_data (id, collection, data, branch_id)
            VALUES (%s, %s, %s, %s)
        """, (req["id"], "quality-project", psycopg2.extras.Json(data), "main"))

conn.commit()
print(f"  创建需求数据: {len(requirements)} 条")

# 问题数据
bugs = [
    # V1.0 问题（已关闭）
    {"id": "bug-001", "bugNo": "BUG-001", "title": "登录页面白屏", "severity": "fatal", "status": "closed", "assignee": "张三", "relatedReq": ["req-001"]},
    {"id": "bug-002", "bugNo": "BUG-002", "title": "密码不区分大小写", "severity": "major", "status": "closed", "assignee": "李四", "relatedReq": ["req-001"]},
    {"id": "bug-003", "bugNo": "BUG-003", "title": "注册页验证码不刷新", "severity": "major", "status": "closed", "assignee": "张三", "relatedReq": ["req-002"]},
    {"id": "bug-004", "bugNo": "BUG-004", "title": "角色权限不生效", "severity": "fatal", "status": "closed", "assignee": "王五", "relatedReq": ["req-003"]},
    {"id": "bug-005", "bugNo": "BUG-005", "title": "首页响应慢", "severity": "minor", "status": "closed", "assignee": "李四", "relatedReq": ["req-004"]},
    {"id": "bug-006", "bugNo": "BUG-006", "title": "消息推送延迟", "severity": "major", "status": "closed", "assignee": "张三", "relatedReq": ["req-005"]},
    {"id": "bug-007", "bugNo": "BUG-007", "title": "消息重复推送", "severity": "minor", "status": "closed", "assignee": "王五", "relatedReq": ["req-005"]},

    # V1.1 问题（已关闭）
    {"id": "bug-008", "bugNo": "BUG-008", "title": "导出Excel格式错误", "severity": "major", "status": "closed", "assignee": "张三", "relatedReq": ["req-006"]},
    {"id": "bug-009", "bugNo": "BUG-009", "title": "导出超1万行崩溃", "severity": "fatal", "status": "closed", "assignee": "李四", "relatedReq": ["req-006"]},
    {"id": "bug-010", "bugNo": "BUG-010", "title": "饼图数据不准确", "severity": "major", "status": "closed", "assignee": "王五", "relatedReq": ["req-007"]},
    {"id": "bug-011", "bugNo": "BUG-011", "title": "切换语言部分未翻译", "severity": "suggestion", "status": "closed", "assignee": "张三", "relatedReq": ["req-008"]},
    {"id": "bug-012", "bugNo": "BUG-012", "title": "报表导出乱码", "severity": "minor", "status": "closed", "assignee": "李四", "relatedReq": ["req-007"]},

    # V2.0 问题（部分未修复）
    {"id": "bug-013", "bugNo": "BUG-013", "title": "移动端适配后字体过小", "severity": "major", "status": "fixed", "assignee": "张三", "relatedReq": ["req-009"]},
    {"id": "bug-014", "bugNo": "BUG-014", "title": "部分页面布局错乱", "severity": "major", "status": "new", "assignee": "李四", "relatedReq": ["req-009"]},
    {"id": "bug-015", "bugNo": "BUG-015", "title": "接口响应超过2秒", "severity": "fatal", "status": "fixed", "assignee": "王五", "relatedReq": ["req-010"]},
    {"id": "bug-016", "bugNo": "BUG-016", "title": "内存泄漏导致OOM", "severity": "fatal", "status": "new", "assignee": "张三", "relatedReq": ["req-010"]},
    {"id": "bug-017", "bugNo": "BUG-017", "title": "网关配置不生效", "severity": "major", "status": "new", "assignee": "李四", "relatedReq": ["req-011"]},
    {"id": "bug-018", "bugNo": "BUG-018", "title": "日志格式不统一", "severity": "suggestion", "status": "new", "assignee": "王五", "relatedReq": ["req-012"]},

    # V2.1 问题（新建）
    {"id": "bug-019", "bugNo": "BUG-019", "title": "AI回复不准确", "severity": "major", "status": "new", "assignee": "张三", "relatedReq": ["req-013"]},
    {"id": "bug-020", "bugNo": "BUG-020", "title": "AI响应超时", "severity": "fatal", "status": "new", "assignee": "李四", "relatedReq": ["req-013"]},
]

for bug in bugs:
    cur.execute("SELECT id FROM dynamic_data WHERE id = %s", (bug["id"],))
    if not cur.fetchone():
        data = {k: v for k, v in bug.items() if k not in ("id", "relatedReq")}
        cur.execute("""
            INSERT INTO dynamic_data (id, collection, data, branch_id)
            VALUES (%s, %s, %s, %s)
        """, (bug["id"], "quality-bug", psycopg2.extras.Json(data), "main"))

conn.commit()
print(f"  创建问题数据: {len(bugs)} 条")

# ============================================
# 4. 创建关联关系
# ============================================
print("\n[4] 创建关联关系...")

for bug in bugs:
    for req_id in bug.get("relatedReq", []):
        cur.execute("""
            SELECT 1 FROM data_relations
            WHERE collection = %s AND record_id = %s AND field_name = %s AND related_id = %s AND branch_id = 'main'
        """, ("quality-bug", bug["id"], "relatedReq", req_id))
        if not cur.fetchone():
            # Bug -> Requirement
            cur.execute("""
                INSERT INTO data_relations (collection, record_id, field_name, related_id, related_collection, branch_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, ("quality-bug", bug["id"], "relatedReq", req_id, "quality-project", "main"))
            # Requirement -> Bug (反向)
            cur.execute("""
                INSERT INTO data_relations (collection, record_id, field_name, related_id, related_collection, branch_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, ("quality-project", req_id, "relatedBugs", bug["id"], "quality-bug", "main"))

conn.commit()
print(f"  创建关联关系: {len(bugs) * 2} 条")

# ============================================
# 5. 创建仪表盘配置
# ============================================
print("\n[5] 创建仪表盘配置...")

dashboard_id = "dashboard-quality-metrics"

# 删除旧仪表盘
cur.execute("DELETE FROM dashboards WHERE id = %s", (dashboard_id,))
conn.commit()

# 仪表盘布局 - 方案A：需求质量视角
dashboard_layout = [
    # 第一行：核心指标卡（4个）
    {
        "id": "w-req-total",
        "type": "metric",
        "title": "需求总数",
        "x": 0, "y": 0, "w": 3, "h": 2,
        "config": {
            "collection": "quality-project",
            "metrics": [{"type": "count", "name": "需求数"}],
        }
    },
    {
        "id": "w-bug-total",
        "type": "metric",
        "title": "问题总数",
        "x": 3, "y": 0, "w": 3, "h": 2,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "问题数"}],
        }
    },
    {
        "id": "w-bug-per-req",
        "type": "metric",
        "title": "平均每需求问题数",
        "x": 6, "y": 0, "w": 3, "h": 2,
        "config": {
            "collection": "quality-project",
            "metrics": [{"type": "relationCountAvg", "field": "relatedBugs", "name": "平均问题数"}],
        }
    },
    {
        "id": "w-fatal-bug",
        "type": "metric",
        "title": "致命问题数",
        "x": 9, "y": 0, "w": 3, "h": 2,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "致命数"}],
            "filter": {"severity": "fatal"},
        }
    },

    # 第二行：对比图表（3个）
    {
        "id": "w-version-compare",
        "type": "bar",
        "title": "各版本需求数与问题数对比",
        "x": 0, "y": 2, "w": 6, "h": 3,
        "config": {
            "collection": "quality-project",
            "metrics": [
                {"type": "count", "name": "需求数"},
                {"type": "relationCountSum", "field": "relatedBugs", "name": "问题数"},
            ],
            "groupBy": {"type": "terms", "field": "version"},
            "sort": "key_asc",
            "limit": 10,
        }
    },
    {
        "id": "w-severity-dist",
        "type": "pie",
        "title": "问题严重程度分布",
        "x": 6, "y": 2, "w": 4, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "问题数"}],
            "groupBy": {"type": "terms", "field": "severity"},
            "sort": "value_desc",
            "limit": 10,
        }
    },
    {
        "id": "w-status-dist",
        "type": "pie",
        "title": "问题状态分布",
        "x": 10, "y": 2, "w": 2, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "问题数"}],
            "groupBy": {"type": "terms", "field": "status"},
            "sort": "value_desc",
            "limit": 10,
        }
    },

    # 第三行：矩阵分析（2个）
    {
        "id": "w-status-severity-matrix",
        "type": "dataTable",
        "title": "问题状态-严重程度矩阵",
        "x": 0, "y": 5, "w": 8, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "问题数"}],
            "groupBy": {"type": "terms", "field": "status"},
            "breakdownBy": {"type": "terms", "field": "severity"},
            "limit": 20,
        }
    },
    {
        "id": "w-priority-req",
        "type": "bar",
        "title": "各优先级需求数",
        "x": 8, "y": 5, "w": 4, "h": 3,
        "config": {
            "collection": "quality-project",
            "metrics": [{"type": "count", "name": "需求数"}],
            "groupBy": {"type": "terms", "field": "priority"},
            "sort": "key_asc",
            "limit": 10,
        }
    },

    # 第四行：需求分析（2个）
    {
        "id": "w-req-status-dist",
        "type": "pie",
        "title": "需求状态分布",
        "x": 0, "y": 8, "w": 4, "h": 3,
        "config": {
            "collection": "quality-project",
            "metrics": [{"type": "count", "name": "需求数"}],
            "groupBy": {"type": "terms", "field": "status"},
            "sort": "value_desc",
            "limit": 10,
        }
    },
    {
        "id": "w-version-req-status",
        "type": "dataTable",
        "title": "版本-需求状态矩阵",
        "x": 4, "y": 8, "w": 8, "h": 3,
        "config": {
            "collection": "quality-project",
            "metrics": [{"type": "count", "name": "需求数"}],
            "groupBy": {"type": "terms", "field": "version"},
            "breakdownBy": {"type": "terms", "field": "status"},
            "sort": "key_asc",
            "limit": 20,
        }
    },
]

cur.execute("""
    INSERT INTO dashboards (id, name, description, layout, owner_id, is_global, created_at, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
""", (
    dashboard_id,
    "质量度量看板",
    "项目版本质量统计：需求数、问题数、问题占比、状态分布",
    psycopg2.extras.Json(dashboard_layout),
    "user-admin",
    True,
    now, now
))
conn.commit()
print(f"  创建仪表盘: {dashboard_id} ({len(dashboard_layout)} 个图表)")

# ============================================
# 完成
# ============================================
print("\n" + "=" * 60)
print("配置完成!")
print("=" * 60)
print(f"""
数据统计:
  - 需求页面: {req_page_id}
  - 问题页面: {bug_page_id}
  - 需求数据: {len(requirements)} 条
  - 问题数据: {len(bugs)} 条
  - 关联关系: {len(bugs) * 2} 条
  - 仪表盘图表: {len(dashboard_layout)} 个

访问路径:
  - 需求管理: /quality-req
  - 测试问题: /quality-bug
  - 仪表盘: /dashboard (选择"质量度量看板")
""")

conn.close()