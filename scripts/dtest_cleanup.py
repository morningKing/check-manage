"""DTEST 残留定点清理（dev 库）。

默认 --dry-run 只报数；--apply 才删除。只动 DTEST/dtest 前缀（或其子表）数据，
operation_logs 全站历史不在清理范围（备案）。
用法：
  python scripts/dtest_cleanup.py                  # 报数（dry-run）
  python scripts/dtest_cleanup.py --apply          # 删除
  python scripts/dtest_cleanup.py --db "host=... dbname=..."   # 覆盖连接串
连接参数读 server/.env（config.DB_CONFIG），与测试套件同源。

列名核对记录（2026-10-07，information_schema.columns 实查，public schema）：
- record_comments: 有 content(text) —— 原样。
- trigger_logs / webhook_logs: 均有 rule_name —— 原样；webhook_logs 总量 ~944 中
  仅 37 行 rule_name 含 DTEST，其余为 AI 批任务回调(outbox) 892 行等全站历史，
  不属 DTEST 残留、不在清理范围。
- workflow_instances: 定义外键列名为 workflow_id -> workflow_definitions.id —— 原样。
- merge_records: 【修正】无 version_id 列；实际为 source_version_id /
  source_version_name（target_branch_* 指向主分支，非 DTEST）。改用
  source_version_id IN (DTEST 版本) OR source_version_name ILIKE '%dtest%'。
- merge_backups: 经 merge_id -> merge_records.id 关联【修正：子查询取
  merge_records.id 而非 merge_id】。
- project_version_snapshots: 版本列名为 version_id -> project_versions.id —— 原样。
- user_current_project_branch: 【修正】无 collection 列；实际列 user_id/username/
  project_menu_id/branch_id。残留 101 行全是 project_menu_id 指向 DTEST 项目菜单
  （menu-proj-DTEST-* / menu-ws-DTEST-*，含菜单已删的孤儿行），故按
  project_menu_id ILIKE '%dtest%' 匹配（branch_id 均为 'main' 等不含 DTEST）。
- dynamic_data: 【修正】data::text 含 dtest 的为 0 行；残留为 collection 含 DTEST
  （15 行，其中 14 行同时 branch_id 指向 DTEST 版本）。改为 collection ILIKE 或
  branch_id 经版本子查询。
- data_files: 【修正】无 name 列；实际为 original_name(text)。
- column_views/roles/users/page_configs/menus: 列名与草案一致（column_views.id 为
  integer，不用 id ILIKE）。

依赖顺序（子先父后）：dynamic_data / user_current_project_branch 的分支、菜单引用
须先于 project_versions、menus 删除；merge_backups -> merge_records ->
project_versions 链严格子先父后。

安全护栏：page_configs/menus/roles/users 四表理论上只应有个位数 DTEST 行，
--apply 时若计数 > 20 判为模式误伤，直接中止不删。
"""
import argparse
import os
import sys

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

# (table, where 模板, 说明)。{d} 为 dtest 模式 '%dtest%'；
# 生成 id 子表用父表 EXISTS/IN 子查询，顺序：子表先于父表。
QUERIES = [
    ('record_comments', "content ILIKE '%dtest%'", 'DTEST 评论'),
    ('trigger_logs', "rule_name ILIKE '%dtest%'", '触发日志'),
    ('webhook_logs', "rule_name ILIKE '%dtest%'", 'webhook 日志（仅 DTEST 规则名）'),
    ('workflow_instances',
     "workflow_id IN (SELECT id FROM workflow_definitions WHERE name ILIKE '%dtest%')",
     '工作流实例（经定义子查询）'),
    ('workflow_definitions', "name ILIKE '%dtest%'", '工作流定义'),
    ('trigger_rules',
     "source_collection ILIKE '%dtest%' OR name ILIKE '%dtest%'", '触发规则'),
    ('webhook_rules', "name ILIKE '%dtest%'", 'webhook 规则'),
    ('column_views', "name ILIKE '%dtest%'", '列视图'),
    ('import_runs', "file_name ILIKE '%dtest%'", '导入历史'),
    ('merge_backups',
     "merge_id IN (SELECT id FROM merge_records WHERE source_version_id IN "
     "(SELECT id FROM project_versions WHERE name ILIKE '%dtest%') "
     "OR source_version_name ILIKE '%dtest%')",
     '合并备份（经 merge_records->版本 子查询）'),
    ('merge_records',
     "source_version_id IN (SELECT id FROM project_versions WHERE name ILIKE '%dtest%') "
     "OR source_version_name ILIKE '%dtest%'",
     '合并记录（经版本子查询/名称）'),
    ('project_version_snapshots',
     "version_id IN (SELECT id FROM project_versions WHERE name ILIKE '%dtest%')",
     '版本快照（经版本子查询）'),
    ('dynamic_data',
     "collection ILIKE '%dtest%' OR branch_id IN "
     "(SELECT id FROM project_versions WHERE name ILIKE '%dtest%')",
     '动态数据（DTEST collection 及分支行）'),
    ('user_current_project_branch', "project_menu_id ILIKE '%dtest%'", '用户当前分支（DTEST 项目菜单）'),
    ('project_versions', "name ILIKE '%dtest%'", '项目版本/分支'),
    ('data_files', "original_name ILIKE '%dtest%'", '上传文件'),
    ('menus', "id ILIKE '%dtest%' OR name ILIKE '%dtest%' OR path ILIKE '%dtest%'", '菜单（子，先于页面配置）'),
    ('page_configs', "id ILIKE '%dtest%' OR name ILIKE '%dtest%'", '页面配置'),
    ('roles', "name ILIKE '%dtest%'", '角色'),
    ('users', "username ILIKE '%dtest%' OR display_name ILIKE '%dtest%'", '用户'),
]

# 这些表只应有个位数 DTEST 行，超限视为模式误伤，--apply 前中止。
GUARD_LIMITS = {'page_configs': 20, 'menus': 20, 'roles': 20, 'users': 20}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='执行删除（默认 dry-run 只报数）')
    ap.add_argument('--db', default='', help='libpq 连接串覆盖，如 host=... dbname=...')
    args = ap.parse_args()
    from config import DB_CONFIG
    conn_kwargs = {k: v for k, v in DB_CONFIG.items() if k != 'options'}
    if args.db:
        conn_kwargs = psycopg2.extensions.parse_dsn(args.db)
    conn = psycopg2.connect(**conn_kwargs)
    cur = conn.cursor()
    total = 0
    violations = []
    counts = []
    for table, where, note in QUERIES:
        cur.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}')
        n = cur.fetchone()[0]
        counts.append((table, n, note))
        limit = GUARD_LIMITS.get(table)
        if limit is not None and n > limit:
            violations.append(f'{table} 命中 {n} 行 > 护栏 {limit}（疑似模式过宽）')
    if violations:
        for v in violations:
            print(f'[GUARD] {v}')
        print('已中止：请人工核对模式后再跑。未删除任何数据。')
        conn.rollback()
        conn.close()
        sys.exit(2)
    for table, n, note in counts:
        print(f'{table:32s} {n:6d}  {note}')
        if n and args.apply:
            cur.execute(f'DELETE FROM {table} WHERE {next(w for t, w, _ in QUERIES if t == table)}')
            total += cur.rowcount
    if args.apply:
        conn.commit()
        print(f'--apply: 共删除 {total} 行')
    else:
        conn.rollback()
        print('--dry-run: 未删除（加 --apply 执行）')
    conn.close()


if __name__ == '__main__':
    main()
