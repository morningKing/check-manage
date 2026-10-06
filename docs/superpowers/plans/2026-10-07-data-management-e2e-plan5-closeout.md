# 数据管理 E2E 测试体系 · 计划⑤：收口 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口数据管理 E2E 体系——残留一次性清理、flaky 加固、L2 时长分层（marker）、user-guide 核对、03 首轮全量执行报告，达成「可长期复跑的全量回归资产」终态。

**Architecture:** 六任务：清理脚本入库并执行 → flaky 四例评估加固 → slow marker 分层 → 一行级 minor 顺手波 → user-guide 行为变更核对（更新或结论记录）→ 双口径全量跑 + 03 报告。零产品代码改动；测试加固与文档为本计划主改动。

**Tech Stack:** pytest + requests、Playwright + TS、psycopg2 直连（清理）、Markdown 文档。

**Spec:** [docs/superpowers/specs/2026-10-06-data-management-e2e-design.md](../specs/2026-10-06-data-management-e2e-design.md) §8/§9；前置计划①–④（基线 L2 100 + L3 25 全量绿）。

## Global Constraints

- **红线**：清理脚本只删 `DTEST`/`dtest` 前缀（ILIKE '%dtest%'）行；operation_logs 全站历史**只备案不清**（无法按 DTEST 归属）；备份 ZIP 文件只删本套件建的；**绝不调用 restore/factory-reset/batchClear**。
- 清理脚本必须入库可复跑（`scripts/dtest_cleanup.py`），默认 `--dry-run` 只报数，`--apply` 才删；按依赖顺序删（子表先于父表）。
- flaky 加固只改测试自身（等待策略/断言方式/资源隔离），不改产品；每个加固先复现（≥5 轮统计）再改，改后 ≥5 轮稳定才算收敛。
- marker 分层：`slow` marker 登记进 `server/pytest.ini`；fast 层 = `pytest -m "not slow"`；marker 打在**实测单例 >30s** 的用例上（预期 E08/E09/E10 ETL 异步轮询、F10–F12 webhook 同步、F13/F15 行动作轮询——以实测为准，报告注明每个 marker 的依据）。
- 一行级 minor 修复波只动计划①–④ ledger 里已备案的 deferred 项，不新增范围。
- user-guide 核对：逐条行为变更给结论（「需更新并已改 / 无需更新+理由」），记录在 03 报告附录。
- 缺陷硬规则继续适用：本计划无产品修复；金丝雀用例（F12/H04）保持现状。
- 既有事实沿用：14+表残留清单（计划③④ ledger）；flaky 观察清单 TD-B15/B16/C12/C14；L2 fast/全量时长（全量 41:58，automation ~8min）。

---

### Task 1: 残留清理脚本入库 + 执行 + 复核

**Files:**
- Create: `scripts/dtest_cleanup.py`
- Modify: `docs/data-testing/04-缺陷记录与修复.md`（已知限制 ⑮ 清理状态更新为「已清理，脚本可复跑」）

**Interfaces:**
- Produces: `scripts/dtest_cleanup.py [--dry-run|--apply] [--db DSN 覆盖]`——按依赖序清 16 类 DTEST 残留，输出逐表计数表。

- [ ] **Step 1: 写清理脚本**

表与依赖顺序（子先父后）。三类生成 id 表（snapshots/merge_records/merge_backups/workflow_instances 的外键是 `prj-ver-*`/`merge-*`/`wfi-*`，**无 DTEST 前缀**）必须走父表子查询；其余按列 LIKE。列名以 information_schema 实际核对为准：

```python
"""DTEST 残留定点清理（dev 库）。

默认 --dry-run 只报数；--apply 才删除。只动 DTEST/dtest 前缀（或其子表）数据，
operation_logs 全站历史不在清理范围（备案）。
用法：
  python scripts/dtest_cleanup.py            # 报数
  python scripts/dtest_cleanup.py --apply    # 删除
连接参数读 server/.env（config.DB_CONFIG），与测试套件同源。
"""
import argparse
import os
import sys

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

# (table, where 模板, 说明)。{d} 为 dtest 模式 '%dtest%'；
# 生成 id 子表用父表 EXISTS 子查询，顺序：子表先于父表。
QUERIES = [
    ('record_comments', "content ILIKE '%dtest%'", 'DTEST 评论'),
    ('trigger_logs', "rule_name ILIKE '%dtest%'", '触发日志'),
    ('webhook_logs', "rule_name ILIKE '%dtest%'", 'webhook 日志'),
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
     "merge_id IN (SELECT merge_id FROM merge_records WHERE version_id IN "
     "(SELECT id FROM project_versions WHERE name ILIKE '%dtest%'))",
     '合并备份（经记录子查询）'),
    ('merge_records',
     "version_id IN (SELECT id FROM project_versions WHERE name ILIKE '%dtest%')",
     '合并记录（经版本子查询）'),
    ('project_version_snapshots',
     "version_id IN (SELECT id FROM project_versions WHERE name ILIKE '%dtest%')",
     '版本快照（经版本子查询）'),
    ('project_versions', "name ILIKE '%dtest%'", '项目版本/分支'),
    ('user_current_project_branch', "collection ILIKE '%dtest%'", '用户当前分支'),
    ('dynamic_data', "data::text ILIKE '%dtest%'", '动态数据（含分支行）'),
    ('data_files', "name ILIKE '%dtest%'", '上传文件'),
    ('page_configs', "id ILIKE '%dtest%' OR name ILIKE '%dtest%'", '页面配置'),
    ('menus', "id ILIKE '%dtest%' OR name ILIKE '%dtest%' OR path ILIKE '%dtest%'", '菜单'),
    ('roles', "name ILIKE '%dtest%'", '角色'),
    ('users', "username ILIKE '%dtest%' OR display_name ILIKE '%dtest%'", '用户'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    from config import DB_CONFIG
    conn = psycopg2.connect(**{k: v for k, v in DB_CONFIG.items() if k != 'options'})
    cur = conn.cursor()
    total = 0
    for table, where, note in QUERIES:
        cur.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}')
        n = cur.fetchone()[0]
        print(f'{table:35s} {n:6d}  {note}')
        if n and args.apply:
            cur.execute(f'DELETE FROM {table} WHERE {where}')
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
```

**列名核对要求（Step 0）**：`merge_records` 是否有 `version_id`、`merge_backups` 是否经 `merge_id` 关联、`workflow_instances` 的定义外键列名、`user_current_project_branch` 的 collection 列名——全部以 information_schema 实查后修正 QUERIES 并在脚本注释留核对记录；子查询链若与实际外键不符，按真实外键改写（如 merge_records 经 branch/version 关联的中间表）。

- [ ] **Step 2: 列名核对 → dry-run → apply → 复核**

先 `psycopg2` 逐表 `\d` 或查 information_schema.columns 修正 TARGETS 的列名（如 record_comments 是否有 content、project_version_snapshots 的版本列名、user_current_project_branch 的 collection 列名），脚本内注释保留核对记录。然后：

```bash
python scripts/dtest_cleanup.py                # dry-run 报数留证
python scripts/dtest_cleanup.py --apply        # 删除
python scripts/dtest_cleanup.py                # 复核应全 0（除 operation_logs）
```

Expected: apply 后全表 0（users/roles/menus/page_configs/dynamic_data/data_files/import_runs/merge_*/project_versions/snapshots/user_current_project_branch/workflow 两表/trigger 两表/webhook 两表/column_views/comments）。

- [ ] **Step 3: L2/L3 抽查不受影响**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_crud.py tests/test_data_full_support.py -q
# Expected: 23 passed（清理不得影响套件自身）
```

- [ ] **Step 4: Commit**

```bash
git add scripts/dtest_cleanup.py docs/data-testing/04-缺陷记录与修复.md
git commit -m "chore(data-full): DTEST 残留定点清理脚本入库并执行——16 类子父序清零，04 ⑮ 状态更新"
```

---

### Task 2: flaky 四例评估与加固（TD-B15/B16/C12/C14）

**Files:**
- Modify: `e2e/data-full/data-field-types.spec.ts`（B15/B16）
- Modify: `e2e/data-full/data-relations.spec.ts`（C12/C14）

**Interfaces:**
- Produces: 四例加固后 ≥5 轮连续稳定；加固手法与统计记录进 03 报告附录。

- [ ] **Step 1: 复现统计**

L3 全量连跑 5 轮，记录四例各自的失败轮次与失败输出（已有历史：B15 一次双提交 strict-mode、B16 一次双提交、C12/C14 各一次负载窗口超时）。

- [ ] **Step 2: 针对性加固（按失败签名，执行者按实际输出选择组合）**

- **B15/B16 双提交（strict-mode violation：同记录渲染两行 / 提交两次）**：根因是「确定」点击后 UI 尚未禁用按钮时断言/后续操作触发二次提交。加固：点确定后**立即等待对话框消失**（`.el-dialog:visible` toBeHidden）再断言行；行断言改 `toHaveCount(1)` 精确计数而非 `.first()` 容忍（容忍掩盖真实回归——04 #8 金丝雀语义只在 F16 保留）。
- **C12/C14 负载窗口超时**：全量跑 CPU 饱和导致下拉/轮询超时。加固：失败步骤的 expect 超时从默认提升到 20–30s（带注释说明负载依据）；若 C12 的 remote 搜索首字符丢字，改 `pressSequentially`（逐键）替代 `fill`。

- [ ] **Step 3: 收敛验证**

加固后 L3 全量连跑 5 轮全部 25 passed（记录每轮时长）。若仍有失败：换手法再 5 轮；两轮手法仍不收敛 → 该例降级为 `test.fixme` 并在 03 报告单列（不许静默跳过）。

- [ ] **Step 4: Commit**

```bash
git add e2e/data-full/data-field-types.spec.ts e2e/data-full/data-relations.spec.ts
git commit -m "fix(data-full): flaky 加固——B15/B16 双提交竞态收敛 + C12/C14 负载窗口超时提额（5 轮稳定）"
```

---

### Task 3: L2 时长分层（slow marker）

**Files:**
- Modify: `server/pytest.ini`（markers 追加 `slow`）
- Modify: `server/tests/test_data_full_io.py`（E08/E09/E10 等实测慢例打标）
- Modify: `server/tests/test_data_full_automation.py`（F10–F13/F15 等按实测）
- Modify: `docs/data-testing/01-测试方案.md`（§3 跑法补 fast/full 双口径）

**Interfaces:**
- Produces: fast 层（`-m "not slow"`）与全量两种口径的实测时长数据。

- [ ] **Step 1: 实测单例耗时并打标**

`pytest --durations=30 tests/test_data_full_io.py tests/test_data_full_automation.py -q` 取单例耗时，**>30s 的用例**加 `@pytest.mark.slow`（预期：E08/E09/E10 ETL 异步轮询 30–40s、F10 before 同步 ~10s、F11/F12 签名/失败 ~36s、F13/F15 行动作轮询 ~30s——以实测为准）。pytest.ini markers 段追加：

```ini
    slow: 慢用例（单例 >30s，ETL 异步轮询/webhook 同步/行动作轮询）；fast 层用 -m "not slow"
```

- [ ] **Step 2: 双口径实测**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_* -q -m "not slow"   # fast 层时长
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_* -q                 # 全量时长
```

Expected: fast 层全绿且明显提速（记录两组数字）；slow 子集单独跑也全绿。

- [ ] **Step 3: 01 文档跑法更新 + Commit**

01 §3 补：fast 层命令、全量命令、两口径实测时长。Commit：

```bash
git add server/pytest.ini server/tests/test_data_full_io.py server/tests/test_data_full_automation.py docs/data-testing/01-测试方案.md
git commit -m "feat(data-full): L2 时长分层——slow marker（实测>30s 单例），fast 层 -m not slow，双口径时长留证"
```

---

### Task 4: 一行级 minor 顺手波

**Files:**
- Modify: `server/tests/test_data_full_support.py`（H04 isdir 防护）
- Modify: `server/tests/test_data_full_field_types.py`（F02 前置总数 pin——注意这是族B文件）
- Modify: `server/tests/test_data_full_views_query.py`（G03 正控断言）
- Modify: `server/tests/test_data_full_automation.py`（F09 删 sleep、规则删除入 finally）

**Interfaces:**
- Produces: 计划①–④ ledger 顶部 deferred 项中「一行级」的清偿。

- [ ] **Step 1: 五处小修**

1. H04：`os.listdir(BACKUP_DIR)` 前加 `os.path.isdir(BACKUP_DIR)` 防护（终审建议）。
2. F02（test_data_full_field_types.py，即计划② B04）：导入锚断言后补 `total == 1` pin（钉「update 动作不建行」——注意该例实测为 update 语义，pin 的是本例自身记录数）。
3. G03：private 域断言前补正控 `assert names, '探测列表不应为空'`（消除单跑空过）。
4. F09：`time.sleep(2)` 删除（webhook 同步语义下返回即确定，事实 #19）；规则 DELETE 移入 finally。
5. H01：孤儿评论 DELETE 补状态断言 `<300`。

- [ ] **Step 2: 四文件跑绿 → Commit**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_support.py tests/test_data_full_field_types.py tests/test_data_full_views_query.py tests/test_data_full_automation.py -q
# Expected: 全绿（fast 层口径即可）
git add -A server/tests/
git commit -m "test(data-full): minor 顺手波——H04 isdir/F02 pin/G03 正控/F09 清理入 finally/H01 DELETE 断言"
```

---

### Task 5: user-guide 行为变更核对

**Files:**
- Modify: `docs/user-guide/`（按核对结论更新若干篇）
- Modify: `docs/data-testing/03-测试执行报告.md`（新建，附录 A 核对结论表；正文在 Task 6 完成）

**Interfaces:**
- Produces: 行为变更→user-guide 处置的完整核对表（已更新/无需更新+理由）。

- [ ] **Step 1: 建核对清单并逐条处置**

行为变更输入（来自 04 缺陷修复 + 计划②③④事实修正，逐条给结论）：

1. POST/batch-create 缺 id 服务端 uuid 兜底（修复 d17bb70）——「API 直调可不带 id」是否值得写（前端行为不变）。
2. ExcelView 支持标量字段单元格编辑回写（a71ad76）——**用户可见新能力**，user-guide 的 Excel 视图章节需更新（只读→可编辑说明+权限说明）。
3. batch-delete 被引用父返回 200+blocked（非 409）——API 行为文档若有删除章节需对齐。
4. 未知 collection GET 返回 200 空集（R2）——API 行为。
5. merge strategy 'ours' 静默 no-op——**用户可见**：分支合并 UI 若暴露 ours 选项需警示（读 user-guide 分支章节确认 UI 是否暴露）。
6. 导入数字单元格按字符串存库——**用户可见**数据现象，导入章节值得一句说明。
7. webhook 手动规则/签名/同步语义——管理员章节。
8. 菜单导出无导入（仅单向）——导出章节若声称回环需修正。

执行者逐条读 `docs/user-guide/` 相关章节（先 ls 该目录建索引），按上述处置：需要更新的小改直接改（保持各篇文风），无需更新的在核对表记录理由。

- [ ] **Step 2: 03 报告建骨架 + 附录 A**

新建 `docs/data-testing/03-测试执行报告.md`，先写：报告头（版本/日期/范围）+ 附录 A「user-guide 行为变更核对表」（本任务产物，全量正文留 Task 6）。Commit：

```bash
git add docs/user-guide/ docs/data-testing/03-测试执行报告.md
git commit -m "docs(user-guide): 行为变更核对——ExcelView 可编辑/导入字符串/ours no-op 等处置落章 + 03 报告骨架附录A"
```

---

### Task 6: 双口径全量跑 + 03 执行报告定稿

**Files:**
- Modify: `docs/data-testing/03-测试执行报告.md`（正文定稿）
- Modify: `docs/data-testing/01-测试方案.md`（如收口期事实有变）

**Interfaces:**
- Produces: 03 首轮全量执行报告（体系交付终态文档）。

- [ ] **Step 1: 收口期全量跑（清理+加固+分层后的干净基线）**

```bash
python scripts/dtest_cleanup.py --apply
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_* -q -m "not slow"   # fast 层
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_* -q                # 全量
npx playwright test e2e/data-full    # 两遍
python scripts/dtest_cleanup.py      # 复核残留
```

Expected: fast 层全绿（记录时长）、全量 L2 100 passed、L3 25 passed 两遍、复核残留 0。**注意长跑用后台+轮询**（L3 全量 4min、L2 全量 40min，勿前台傻等）。

- [ ] **Step 2: 03 报告正文**

结构对齐 `docs/ai-testing/03-测试执行报告.md`（先读其结构）：
1. 执行概要（日期、口径：L2 100 fast/full 双时长、L3 25 两轮、flaky 收敛统计）
2. 分族结果表（八族 × L2/L3 计数与关键覆盖点）
3. 时长分层（fast/full 对比 + slow marker 清单）
4. 缺陷与金丝雀状态（04 #1–#8：已修 3/登记 5，金丝雁翻转条件）
5. 残留与清理（脚本位置、清理前后计数、operation_logs 备案说明）
6. 已知限制索引（04 ⑨–⑭ + flaky 收敛记录）
7. 回归资产使用说明（fast/full 跑法、L3 跑法、清理脚本）
8. 附录 A user-guide 核对表（Task 5 产物）

- [ ] **Step 3: Commit + 自查**

```bash
git add docs/data-testing/
git commit -m "docs(data-testing): 03 首轮全量执行报告——双口径时长/八族矩阵/缺陷金丝雀状态/残留清零（体系收口）"
```

自查：`git status` 干净；L2 fast/full、L3 两轮、清理复核五组证据齐；user-guide 核对表覆盖全部 8 条行为变更。

---

## 体系终态（本计划完成后）

- 八族全覆盖：L2 100 + L3 25 全量回归资产，fast 层（`-m "not slow"`）供日常，全量供门禁。
- docs/data-testing 五件套齐（01 方案/02 用例/03 报告/04 缺陷）+ scripts/dtest_cleanup.py 运维脚本。
- 未修缺陷 5 项（04 #3–#8 中未修复者）带金丝雀待产品排期；残留 operation_logs 备案。
