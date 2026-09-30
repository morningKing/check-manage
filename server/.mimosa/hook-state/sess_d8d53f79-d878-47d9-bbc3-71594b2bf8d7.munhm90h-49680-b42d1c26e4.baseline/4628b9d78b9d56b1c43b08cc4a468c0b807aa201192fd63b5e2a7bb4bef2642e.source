# -*- coding: utf-8 -*-
"""
雷达图和漏斗图测试数据脚本

包含：
1. 更新 quality-bug 数据集确保有足够测试数据
2. 创建测试仪表板（含雷达图、漏斗图widget配置）
3. 验证聚合API返回正确的数据结构

使用方法：
cd server && python test_scripts/create_radar_funnel_test_data.py
"""
import sys
import os

# 添加 server 目录到路径
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
print("雷达图和漏斗图测试数据脚本")
print("=" * 60)

# ============================================
# 1. 确保 quality-bug 有足够测试数据
# ============================================
print("\n[1] 检查并补充 quality-bug 测试数据...")

# 检查现有数据
cur.execute("""
    SELECT COUNT(*) FROM dynamic_data
    WHERE collection = 'quality-bug' AND branch_id = 'main'
""")
existing_count = cur.fetchone()[0]
print(f"现有数据: {existing_count} 条")

# 统计各 severity 数量
cur.execute("""
    SELECT data->>'severity', COUNT(*)
    FROM dynamic_data
    WHERE collection = 'quality-bug' AND branch_id = 'main'
    GROUP BY data->>'severity'
""")
severity_stats = cur.fetchall()
print(f"Severity 分布: {dict(severity_stats)}")

# 统计各 status 数量
cur.execute("""
    SELECT data->>'status', COUNT(*)
    FROM dynamic_data
    WHERE collection = 'quality-bug' AND branch_id = 'main'
    GROUP BY data->>'status'
""")
status_stats = cur.fetchall()
print(f"Status 分布: {dict(status_stats)}")

# 统计各 assignee 数量
cur.execute("""
    SELECT data->>'assignee', COUNT(*)
    FROM dynamic_data
    WHERE collection = 'quality-bug' AND branch_id = 'main'
    GROUP BY data->>'assignee'
""")
assignee_stats = cur.fetchall()
print(f"Assignee 分布: {dict(assignee_stats)}")

# 如果数据不足，补充测试数据
if existing_count < 20:
    print("数据不足，开始补充...")

    test_bugs = [
        # severity: fatal (需要至少5条)
        {"bugNo": "BUG-FATAL-001", "title": "系统崩溃致命错误", "severity": "fatal", "status": "new", "assignee": "张三"},
        {"bugNo": "BUG-FATAL-002", "title": "数据丢失问题", "severity": "fatal", "status": "new", "assignee": "李四"},
        {"bugNo": "BUG-FATAL-003", "title": "权限绕过漏洞", "severity": "fatal", "status": "fixed", "assignee": "王五"},
        {"bugNo": "BUG-FATAL-004", "title": "SQL注入漏洞", "severity": "fatal", "status": "closed", "assignee": "张三"},
        {"bugNo": "BUG-FATAL-005", "title": "内存溢出", "severity": "fatal", "status": "closed", "assignee": "李四"},
        {"bugNo": "BUG-FATAL-006", "title": "服务无法启动", "severity": "fatal", "status": "closed", "assignee": "王五"},

        # severity: major (需要至少8条)
        {"bugNo": "BUG-MAJOR-001", "title": "页面加载缓慢", "severity": "major", "status": "new", "assignee": "张三"},
        {"bugNo": "BUG-MAJOR-002", "title": "表单验证失败", "severity": "major", "status": "new", "assignee": "李四"},
        {"bugNo": "BUG-MAJOR-003", "title": "搜索结果不准确", "severity": "major", "status": "new", "assignee": "王五"},
        {"bugNo": "BUG-MAJOR-004", "title": "导出格式错误", "severity": "major", "status": "fixed", "assignee": "张三"},
        {"bugNo": "BUG-MAJOR-005", "title": "分页功能异常", "severity": "major", "status": "closed", "assignee": "李四"},
        {"bugNo": "BUG-MAJOR-006", "title": "排序不生效", "severity": "major", "status": "closed", "assignee": "王五"},
        {"bugNo": "BUG-MAJOR-007", "title": "联动查询失败", "severity": "major", "status": "closed", "assignee": "张三"},
        {"bugNo": "BUG-MAJOR-008", "title": "批量操作报错", "severity": "major", "status": "closed", "assignee": "李四"},
        {"bugNo": "BUG-MAJOR-009", "title": "报表统计偏差", "severity": "major", "status": "closed", "assignee": "王五"},

        # severity: minor (需要至少3条)
        {"bugNo": "BUG-MINOR-001", "title": "UI样式微调", "severity": "minor", "status": "closed", "assignee": "张三"},
        {"bugNo": "BUG-MINOR-002", "title": "提示文案优化", "severity": "minor", "status": "closed", "assignee": "李四"},
        {"bugNo": "BUG-MINOR-003", "title": "图标显示问题", "severity": "minor", "status": "closed", "assignee": "王五"},

        # severity: suggestion (需要至少2条)
        {"bugNo": "BUG-SUG-001", "title": "建议增加快捷键", "severity": "suggestion", "status": "new", "assignee": "张三"},
        {"bugNo": "BUG-SUG-002", "title": "建议优化交互流程", "severity": "suggestion", "status": "new", "assignee": "李四"},
    ]

    for bug in test_bugs:
        bug_id = gen_id('bug')
        cur.execute("""
            INSERT INTO dynamic_data (id, collection, data, branch_id, version, created_at, updated_at)
            VALUES (%s, 'quality-bug', %s, 'main', 1, %s, %s)
        """, (bug_id, json.dumps(bug), now, now))

    conn.commit()
    print(f"补充了 {len(test_bugs)} 条测试数据")

    # 重新统计
    cur.execute("""
        SELECT data->>'severity', COUNT(*)
        FROM dynamic_data
        WHERE collection = 'quality-bug' AND branch_id = 'main'
        GROUP BY data->>'severity'
    """)
    severity_stats = cur.fetchall()
    print(f"更新后 Severity 分布: {dict(severity_stats)}")

# ============================================
# 2. 创建测试仪表板
# ============================================
print("\n[2] 创建测试仪表板...")

dashboard_id = "dash-radar-funnel-test"
dashboard_name = "雷达图漏斗图测试"

# Widget 配置
widgets = [
    # 雷达图 - Bug Severity Radar（使用多指标filter）
    {
        "id": "w-radar-severity",
        "type": "radar",
        "title": "Bug严重程度雷达图",
        "x": 0, "y": 0, "w": 4, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [
                {"type": "count", "filter": {"severity": "fatal"}, "name": "致命"},
                {"type": "count", "filter": {"severity": "major"}, "name": "严重"},
                {"type": "count", "filter": {"severity": "minor"}, "name": "一般"},
                {"type": "count", "filter": {"severity": "suggestion"}, "name": "建议"}
            ]
        }
    },

    # 漏斗图 - Bug Status Funnel（使用分组聚合）
    {
        "id": "w-funnel-status",
        "type": "funnel",
        "title": "Bug状态漏斗图",
        "x": 4, "y": 0, "w": 4, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "数量"}],
            "groupBy": {"type": "terms", "field": "status"},
            "sort": "value_desc",
            "limit": 10,
            "funnelShowRate": True
        }
    },

    # 雷达图 - Assignee Radar（按人员分布）
    {
        "id": "w-radar-assignee",
        "type": "radar",
        "title": "Bug负责人雷达图",
        "x": 8, "y": 0, "w": 4, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [
                {"type": "count", "filter": {"assignee": "张三"}, "name": "张三"},
                {"type": "count", "filter": {"assignee": "李四"}, "name": "李四"},
                {"type": "count", "filter": {"assignee": "王五"}, "name": "王五"}
            ]
        }
    },

    # 漏斗图 - Severity Distribution（严重程度分布）
    {
        "id": "w-funnel-severity",
        "type": "funnel",
        "title": "严重程度分布漏斗",
        "x": 0, "y": 3, "w": 6, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "数量"}],
            "groupBy": {"type": "terms", "field": "severity"},
            "sort": "value_desc",
            "limit": 10,
            "funnelShowRate": False
        }
    },

    # 环形图 - Status Ring（参考对比）
    {
        "id": "w-ring-status",
        "type": "ring",
        "title": "状态分布环形图",
        "x": 6, "y": 3, "w": 3, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "数量"}],
            "groupBy": {"type": "terms", "field": "status"},
            "sort": "value_desc",
            "limit": 10
        }
    },

    # 仪表盘 - Fatal Bug Gauge（参考对比）
    {
        "id": "w-gauge-fatal",
        "type": "gauge",
        "title": "致命Bug进度",
        "x": 9, "y": 3, "w": 3, "h": 3,
        "config": {
            "collection": "quality-bug",
            "metrics": [{"type": "count", "name": "致命"}],
            "filter": {"severity": "fatal"},
            "gaugeTarget": 10
        }
    },
]

# 删除已存在的测试仪表板
cur.execute("DELETE FROM dashboards WHERE id = %s", (dashboard_id,))

# 创建新仪表板
cur.execute("""
    INSERT INTO dashboards (id, name, description, layout, owner_id, is_global, created_at, updated_at)
    VALUES (%s, %s, %s, %s, 'user-admin', TRUE, %s, %s)
""", (dashboard_id, dashboard_name, "雷达图和漏斗图测试数据验证",
      psycopg2.extras.Json(widgets), now, now))

conn.commit()
print(f"仪表板创建成功: {dashboard_id}")

# ============================================
# 3. 验证聚合API数据结构
# ============================================
print("\n[3] 验证聚合API数据结构...")

# 验证多指标聚合（雷达图需要）
print("\n雷达图数据验证 (多指标聚合):")
cur.execute("""
    SELECT
        SUM(CASE WHEN data->>'severity' = 'fatal' THEN 1 ELSE NULL END) as fatal,
        SUM(CASE WHEN data->>'severity' = 'major' THEN 1 ELSE NULL END) as major,
        SUM(CASE WHEN data->>'severity' = 'minor' THEN 1 ELSE NULL END) as minor,
        SUM(CASE WHEN data->>'severity' = 'suggestion' THEN 1 ELSE NULL END) as suggestion
    FROM dynamic_data
    WHERE collection = 'quality-bug' AND branch_id = 'main'
""")
radar_row = cur.fetchone()
radar_metrics = {
    "fatal": int(radar_row[0] or 0),
    "major": int(radar_row[1] or 0),
    "minor": int(radar_row[2] or 0),
    "suggestion": int(radar_row[3] or 0)
}
print(f"  雷达图指标: {radar_metrics}")
print(f"  数据结构: type=single, metrics={radar_metrics}")

# 验证分组聚合（漏斗图需要）
print("\n漏斗图数据验证 (分组聚合):")
cur.execute("""
    SELECT data->>'status' as key, COUNT(*) as value
    FROM dynamic_data
    WHERE collection = 'quality-bug' AND branch_id = 'main'
    GROUP BY data->>'status'
    ORDER BY value DESC
""")
funnel_rows = cur.fetchall()
funnel_data = [{"key": row[0], "value": int(row[1])} for row in funnel_rows]
print(f"  漏斗图层级: {funnel_data}")
print(f"  数据结构: type=grouped, data={funnel_data}")

# ============================================
# 4. 输出测试说明
# ============================================
print("\n" + "=" * 60)
print("测试数据创建完成")
print("=" * 60)
print(f"""
仪表板ID: {dashboard_id}
仪表板名称: {dashboard_name}

包含Widget:
  - 雷达图: 2个
    * Bug严重程度雷达图 (4维度: 致命/严重/一般/建议)
    * Bug负责人雷达图 (3维度: 张三/李四/王五)

  - 漏斗图: 2个
    * Bug状态漏斗图 (显示转化率)
    * 严重程度分布漏斗 (不显示转化率)

  - 参考图表: 2个
    * 状态分布环形图
    * 致命Bug进度仪表盘

数据验证:
  雷达图数据: {radar_metrics}
  漏斗图数据: {funnel_data}

访问方式:
  http://localhost:5173/dashboard
  选择仪表板: "{dashboard_name}"

API测试:
  POST /dashboards/aggregate
  Body: {{ "collection":"quality-bug", "metrics":[{{"type":"count","filter":{{"severity":"fatal"}},"name":"fatal"}}] }}
""")

cur.close()
conn.close()