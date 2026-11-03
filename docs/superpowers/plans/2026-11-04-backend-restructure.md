# 后端文件结构重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `server/utils/`（113 文件/36,792 行）按业务域垂直分包并拆分 9 个千行巨文件，行为零变化。

**Architecture:** 每批 = `git mv` 移动 → 更新 codemod 映射表 → 跑 codemod 改写全仓 import → 机械三件套（compileall/grep/pyflakes）→ 全量单测 → 独立 commit。拆分文件统一「包 + 聚合 `__init__`」+ 符号面 parity 校验。

**Tech Stack:** Python 3.11+（Flask 后端）、pytest（casemanage_test 独立库）、AST codemod、Playwright E2E。

**Spec:** `docs/superpowers/specs/2026-11-03-backend-restructure-design.md`（113 文件完整映射表 §4、巨文件拆分 §5、codemod 规则 §6）

## Global Constraints

- 分支 `feat/backend-restructure`；**main 全程冻结**，全部门禁过 + 用户确认后才合回
- 硬切：不保留任何 `utils.*` 旧路径 shim；最终 `utils/` 目录消失
- 行为零变化：不改 SQL/算法/协议/日志文案；方法逐字搬移，diff 审查只允许整块位移
- 每批门禁全绿才能 commit 进下一批；任一批失败先回退该批再排查
- 单测跑前必须停 dev 栈（`cd server && python scripts/stack_stop.py`），避免 data_full 活栈用例互抢
- 文件移动一律 `git mv`；新建文件用 Write 工具；codemod 落盘走其内部实现
- Mimosa 钩子若拦截 codemod 执行：向用户说明并获准后重跑（钩子反馈优先）

---

### Task 1（批次0）: codemod 工具 + 包骨架 + common/ 迁移

**Files:**
- Create: `tools/refactor/rewrite_utils_imports.py`（完整代码见 Step 1）
- Create: `common/__init__.py`、`ai/__init__.py`、`ai/batch/__init__.py`、`ai/opencode/__init__.py`、`ai/skills/__init__.py`、`ai/sessions/__init__.py`、`ai/orchestration/__init__.py`、`ai/gate/__init__.py`、`ai/scan/__init__.py`、`data/__init__.py`、`projects/__init__.py`、`editor/__init__.py`、`collab/__init__.py`（均在 `server/` 下，空文件）
- Move: `server/utils/{operation_log,permissions,rbac_guard,notifier,api_errors,errors,logging_setup,log_tail,log_retention,rate_limit,upload_limits,filename,zip_unicode,search_text,py_runner}.py` → `server/common/`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: codemod CLI（`python tools/refactor/rewrite_utils_imports.py [--check]`，退出码 0=无待改/1=已改写或待改/2=需人工处理）；后续所有任务通过往 `MAPPING` dict 追加条目复用它

- [ ] **Step 1: 写 codemod 脚本**

写入 `tools/refactor/rewrite_utils_imports.py`（内容如下，`MAPPING` 直接带上批次0 的 15 条）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""utils.* import rewriter: AST locate + text-level precise replace.

Spec: docs/superpowers/specs/2026-11-03-backend-restructure-design.md SS6
- Touches ONLY import statements and `utils.<mod>` attribute-chain prefixes;
  every other byte is untouched (diff limited to import regions).
- Binding name always stays the OLD module name (split packages bind via
  `from <parent> import <leaf> as <old>`), so call sites never learn the
  new layout.
- Idempotent: rewritten files no longer reference mapped modules.
- A chain binding colliding with file-local names -> file reported for
  manual handling (exit 2); safe files still rewritten.

Usage (repo root):
  python tools/refactor/rewrite_utils_imports.py           # rewrite in place
  python tools/refactor/rewrite_utils_imports.py --check   # report only
Exit codes: 0 nothing to do; 1 rewrite done (or pending under --check);
            2 manual-fix files present
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / 'server'

# old module name (first segment after `utils.`) -> new dotted path.
# Split targets are package paths; their __init__ re-exports every original
# symbol (underscore-prefixed included). Entries are appended per batch.
MAPPING = {
    # ---- batch 0: common/ ----
    'operation_log': 'common.operation_log',
    'permissions': 'common.permissions',
    'rbac_guard': 'common.rbac_guard',
    'notifier': 'common.notifier',
    'api_errors': 'common.api_errors',
    'errors': 'common.errors',
    'logging_setup': 'common.logging_setup',
    'log_tail': 'common.log_tail',
    'log_retention': 'common.log_retention',
    'rate_limit': 'common.rate_limit',
    'upload_limits': 'common.upload_limits',
    'filename': 'common.filename',
    'zip_unicode': 'common.zip_unicode',
    'search_text': 'common.search_text',
    'py_runner': 'common.py_runner',
}

SCAN_FILES = ('app.py', 'proxy.py', 'init_db.py', 'auth.py')
SCAN_DIRS = (
    'routes', 'utils', 'tests', 'scripts', 'test_scripts', 'migrations',
    'common', 'ai', 'data', 'projects', 'editor', 'collab',
    '../mcp-server/tools', '../mcp-server/tests', '../scripts',
)


def iter_targets():
    for name in SCAN_FILES:
        p = SERVER / name
        if p.is_file():
            yield p
    for d in SCAN_DIRS:
        base = (SERVER / d).resolve()
        if base.is_dir():
            for q in sorted(base.rglob('*.py')):
                if '__pycache__' not in q.parts:
                    yield q


def _parent_leaf(dotted):
    parent, _, leaf = dotted.rpartition('.')
    return parent, leaf


def _binding_line(mod):
    parent, leaf = _parent_leaf(MAPPING[mod])
    return 'from %s import %s as %s' % (parent, leaf, mod)


def _stmt_span(src, node):
    lines = src.splitlines(keepends=True)
    start = sum(len(x) for x in lines[:node.lineno - 1])
    end = start + sum(len(x) for x in lines[node.lineno - 1:node.end_lineno])
    return start, end


def _trailing_comment(chunk):
    first = chunk.split('\n', 1)[0]
    for marker in ('  #', ' #'):
        idx = first.find(marker)
        if idx != -1:
            return first[idx:].rstrip()
    return ''


def _alias_text(a):
    return a.name + (' as ' + a.asname if a.asname else '')


def analyze(path):
    """-> (edits, problems). edits = [(start, end, text)], non-overlapping."""
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        src = f.read()
    rel = path.relative_to(REPO).as_posix()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [], ['%s: SyntaxError %s' % (rel, e)]

    edits = []
    problems = []
    emitted = set()        # old names already bound by a rewritten import
    unmoved_chain = False  # file still uses utils.<unmoved> via attribute chains

    import_nodes = []      # (node, kind, payload); sorted by source order later
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == 'utils':
                import_nodes.append((node, 'from_bare', None))
            elif node.module.startswith('utils.'):
                import_nodes.append((node, 'from_mod', node.module[6:]))
        elif isinstance(node, ast.Import):
            import_nodes.append((node, 'importline', None))
    import_nodes.sort(key=lambda t: (t[0].lineno, t[0].col_offset))

    def _root_is_utils(n):
        while isinstance(n, ast.Attribute):
            n = n.value
        return isinstance(n, ast.Name) and n.id == 'utils'

    # ---- attribute chains: utils.<mod>.<rest> -> <mod>.<rest> ----
    chain_mods = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and _root_is_utils(node)):
            continue
        if _root_is_utils(node.value):
            continue  # inner link of a chain
        inner = node
        while isinstance(inner.value, ast.Attribute):
            inner = inner.value
        mod = inner.attr  # utils.<mod>.<rest...>
        chain_mods.add(mod)
        if mod not in MAPPING:
            unmoved_chain = True
            continue
        edits.append((inner.col_offset, inner.end_col_offset, mod))

    # ---- import statements ----
    import_edits = []
    for node, kind, payload in import_nodes:
        span = _stmt_span(src, node)
        chunk = src[span[0]:span[1]]
        cmt = _trailing_comment(chunk)
        suffix = ('  ' + cmt.lstrip()) if cmt else ''

        if kind == 'from_mod':
            if payload not in MAPPING:
                continue
            text = chunk.replace('utils.' + payload, MAPPING[payload], 1)
        elif kind == 'from_bare':
            moved = [a for a in node.names if a.name in MAPPING]
            if not moved:
                continue
            parts = []
            unmoved = [a for a in node.names if a.name not in MAPPING]
            if unmoved:
                parts.append('from utils import ' +
                             ', '.join(_alias_text(a) for a in unmoved) + suffix)
                suffix = ''
            for a in moved:
                bind = a.asname or a.name
                parent, leaf = _parent_leaf(MAPPING[a.name])
                parts.append('from %s import %s as %s' % (parent, leaf, bind))
                if bind == a.name:
                    emitted.add(a.name)
            text = '\n'.join(parts)
        else:  # importline: import utils[, x] / import utils.X[ as y]
            out = []
            for a in node.names:
                if a.name == 'utils':
                    if unmoved_chain:
                        out.append('utils')  # keep bare: unmoved chains need it
                    continue
                if a.name.startswith('utils.'):
                    mod = a.name[6:]
                    if mod in MAPPING:
                        parent, leaf = _parent_leaf(MAPPING[mod])
                        bind = a.asname or mod
                        out.append('from %s import %s as %s' % (parent, leaf, bind))
                        if bind == mod:
                            emitted.add(mod)
                    else:
                        out.append(_alias_text(a))
                else:
                    out.append(_alias_text(a))
            text = '' if not out else 'import ' + ', '.join(out) + suffix
        if text != chunk:
            import_edits.append((span[0], span[1], text))

    # ---- chain bindings not provided by any rewritten import ----
    pending = [_binding_line(m) for m in sorted(set(MAPPING) & chain_mods)
               if m not in emitted]
    if pending:
        blk = '\n'.join(pending) + '\n'
        host = import_edits[0] if import_edits else None
        if host is not None and host[0] == min(
                (s for s, _, _ in import_edits), default=None):
            import_edits[0] = (host[0], host[1], blk + host[2])
        else:
            first_import = import_nodes[0][0]
            pos = _stmt_span(src, first_import)[0]
            import_edits.append((pos, pos, blk))
    edits.extend(import_edits)
    return edits, problems


def apply_edits(path, edits):
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        src = f.read()
    edits = sorted(edits, key=lambda x: (x[0], x[1]))
    for (s1, e1, _), (s2, e2, _) in zip(edits, edits[1:]):
        if e1 > s2:
            return None  # overlapping edits: refuse
    out = src
    for s, e, text in reversed(edits):
        out = out[:s] + text + out[e:]
    try:
        ast.parse(out)
    except SyntaxError:
        return None
    return out


def main():
    check = '--check' in sys.argv
    changed = 0
    problems = []
    for path in iter_targets():
        edits, probs = analyze(path)
        problems.extend(probs)
        if not edits:
            continue
        if check:
            changed += 1
            print('WOULD REWRITE %s (%d edits)'
                  % (path.relative_to(REPO).as_posix(), len(edits)))
            continue
        new = apply_edits(path, edits)
        if new is None:
            problems.append('%s: rewrite failed validation, left unchanged'
                            % path.relative_to(REPO).as_posix())
            continue
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(new)
        changed += 1
        print('REWRITTEN %s' % path.relative_to(REPO).as_posix())
    for p in problems:
        print('MANUAL: %s' % p, file=sys.stderr)
    print('%d files %s, %d manual' % (
        changed, 'to rewrite' if check else 'rewritten', len(problems)))
    if problems:
        return 2
    return 1 if changed else 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 2: 装校验依赖**

Run: `pip install pyflakes`
Expected: 成功安装（dev 环境，一次性）

- [ ] **Step 3: 建包骨架**

在 `server/` 下用 Write 工具创建 13 个空 `__init__.py`：
`common/`、`ai/`、`ai/batch/`、`ai/opencode/`、`ai/skills/`、`ai/sessions/`、`ai/orchestration/`、`ai/gate/`、`ai/scan/`、`data/`、`projects/`、`editor/`、`collab/`

- [ ] **Step 4: git mv 15 个文件**

```bash
cd server
for f in operation_log permissions rbac_guard notifier api_errors errors logging_setup log_tail log_retention rate_limit upload_limits filename zip_unicode search_text py_runner; do
  git mv utils/$f.py common/$f.py
done
```

- [ ] **Step 5: 先 --check 预览，再实跑 codemod**

```bash
cd /e/wsl/check/check-manage
python tools/refactor/rewrite_utils_imports.py --check   # 预览改动面，核对无意外文件
python tools/refactor/rewrite_utils_imports.py           # 实跑
```
Expected: 实跑后输出 `N files rewritten, 0 manual`；MANUAL 行若出现，按提示人工处理该文件后重跑至 0 manual

- [ ] **Step 6: 机械三件套**

```bash
cd server && python -m compileall -q app.py proxy.py init_db.py auth.py routes utils common ai data projects editor collab tests scripts test_scripts migrations ../mcp-server/tools ../mcp-server/tests ../scripts
git diff --name-only | grep '\.py$' | xargs python -m pyflakes 2>&1 | grep -E 'undefined name|redefinition'   # 必须为空
grep -rn 'utils\.' --include='*.py' routes utils common ai data projects editor collab tests scripts test_scripts migrations app.py proxy.py init_db.py auth.py ../mcp-server/tools ../mcp-server/tests ../scripts | grep -v __pycache__   # 必须为空
```
Expected: compileall 零输出；pyflakes/grep 均空

- [ ] **Step 7: 全量单测**

```bash
cd server && python scripts/stack_stop.py
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest -q
```
Expected: 全绿（约 2500 例，串行 ~160s；pytest.ini 自带 `-m "not stress"`）

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(common): utils 横向基础设施迁入 common/ + AST codemod 工具入库"
```

---

### Task 2（批次1）: data/ + editor/ 迁移（含 mcp-server 同步与 reload 点）

**Files:**
- Move: `server/utils/{mongo_query,script_runner,data_export,export_runner,export_references,etl_engine,etl_scheduler,field_indexes,field_index_scheduler,dependency_scheduler,scan_writeback_validator,mcp_servers,mcp_tool_rules,sequences,page_config_relations}.py` → `server/data/`
- Move: `server/utils/{suggest_editor_repo,suggest_editor_rules,suggest_editor_rule_ai,suggest_editor_attachments}.py` → `server/editor/`
- Modify: `tools/refactor/rewrite_utils_imports.py`（MAPPING 追加 19 条）
- Modify: `server/routes/page_configs.py`（codemod 自动改写 reload 点，人工复核）

**Interfaces:**
- Consumes: Task 1 的 codemod 与包骨架
- Produces: `data.page_config_relations`（`page_configs.py` 的 `importlib.reload` 目标）；`data.export_runner`/`data.script_runner`/`data.export_references`（mcp-server 工具引用）

- [ ] **Step 1: MAPPING 追加**

```python
    # ---- batch 1: data/ + editor/ ----
    'mongo_query': 'data.mongo_query',
    'script_runner': 'data.script_runner',
    'data_export': 'data.data_export',
    'export_runner': 'data.export_runner',
    'export_references': 'data.export_references',
    'etl_engine': 'data.etl_engine',
    'etl_scheduler': 'data.etl_scheduler',
    'field_indexes': 'data.field_indexes',
    'field_index_scheduler': 'data.field_index_scheduler',
    'dependency_scheduler': 'data.dependency_scheduler',
    'scan_writeback_validator': 'data.scan_writeback_validator',
    'mcp_servers': 'data.mcp_servers',
    'mcp_tool_rules': 'data.mcp_tool_rules',
    'sequences': 'data.sequences',
    'page_config_relations': 'data.page_config_relations',
    'suggest_editor_repo': 'editor.suggest_editor_repo',
    'suggest_editor_rules': 'editor.suggest_editor_rules',
    'suggest_editor_rule_ai': 'editor.suggest_editor_rule_ai',
    'suggest_editor_attachments': 'editor.suggest_editor_attachments',
```

- [ ] **Step 2: git mv**

```bash
cd server
for f in mongo_query script_runner data_export export_runner export_references etl_engine etl_scheduler field_indexes field_index_scheduler dependency_scheduler scan_writeback_validator mcp_servers mcp_tool_rules sequences page_config_relations; do
  git mv utils/$f.py data/$f.py
done
for f in suggest_editor_repo suggest_editor_rules suggest_editor_rule_ai suggest_editor_attachments; do
  git mv utils/$f.py editor/$f.py
done
```

- [ ] **Step 3: codemod --check 后实跑**（同 Task 1 Step 5）
- [ ] **Step 4: 复核 reload 点**

```bash
grep -n 'importlib.reload' server/routes/page_configs.py
```
Expected: 引用为 `importlib.reload(page_config_relations)` 且文件顶部有 codemod 生成的 `from data import page_config_relations` 绑定。若无（链改写遗漏），手工改写该处后重跑 codemod --check 至零待改。

- [ ] **Step 5: 机械三件套 + 全量单测**（同 Task 1 Step 6-7）
- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "refactor(data,editor): 数据管道与智能写作模块分包，mcp-server/迁移脚本 import 同步硬切"
```

---

### Task 3（批次2）: ai/opencode/ + ai/skills/ + 插件生成物 byte 级验证

**Files:**
- Move: `server/utils/{opencode_client,opencode_ownership,opencode_launch,opencode_parts,subagent_reuse_plugin,tool_timeout_plugin,plugin_registry}.py` → `server/ai/opencode/`
- Create: `server/ai/opencode/opencode_global/{__init__,global_paths,global_guard,global_write}.py`（由 `utils/opencode_global.py` 1129 行拆分）
- Move: `server/utils/{global_skills,skill_fit,skill_fit_ai,skillopt,skill_candidate_eval,skill_upload}.py` → `server/ai/skills/`
- Modify: `tools/refactor/rewrite_utils_imports.py`（MAPPING 追加）

**Interfaces:**
- Consumes: Task 1 codemod
- Produces: `ai.opencode.opencode_global.OPENCODE_GLOBAL_DIR`（`app.py` 启动链引用，聚合 `__init__` 导出）；`ai.skills.skillopt.ensure_runtime_plugin`

- [ ] **Step 1: 捕获 opencode_global 符号基线**

```bash
cd server && python -c "import utils.opencode_global as m, types; print('\n'.join(sorted(n for n, v in vars(m).items() if not n.startswith('__') and not isinstance(v, types.ModuleType))))" > ../tools/refactor/baselines/opencode_global.txt
```

- [ ] **Step 2: 记录 OC 全局目录部署物哈希**

```bash
cd server && OGD=$(python -c "from utils.opencode_global import OPENCODE_GLOBAL_DIR; print(OPENCODE_GLOBAL_DIR)") && echo "OGD=$OGD" && find "$OGD" -name '*.js' | sort | xargs sha256sum > ../tools/refactor/baselines/oc_deployed_js_before.txt
```

- [ ] **Step 3: git mv + opencode_global 拆分**

7 个平铺文件照常 `git mv` 进 `ai/opencode/`；6 个 skills 文件进 `ai/skills/`。然后拆 `opencode_global.py`：
- `git mv utils/opencode_global.py ai/opencode/opencode_global/global_paths.py`
- 从 `global_paths.py` **逐字剪切**（不重写）guard 组函数（`validate_name`/`resolve_under`/`_reject_symlink_components`/`_check_content_size`/`_check_dir_size` 及其专用常量）到新文件 `global_guard.py`；写入组（`_atomic_write` 及其余写文件函数）到 `global_write.py`——三个文件共享原文件的 import 头（逐字复制）
- 三个叶子各加 `__all__ = [...]`，逐项列出本文件定义的顶层名字（与基线文件对照，无遗漏无重叠）
- 建 `opencode_global/__init__.py`：

```python
"""原 utils.opencode_global 的聚合导出：符号面与拆分前完全一致。"""
from .global_paths import *  # noqa: F401,F403
from .global_guard import *  # noqa: F401,F403
from .global_write import *  # noqa: F401,F403
```

- 叶子间交叉引用按 pyflakes F821 提示补 `from .global_paths import X` 等包内导入

- [ ] **Step 4: MAPPING 追加并跑 codemod**

```python
    # ---- batch 2: ai/opencode/ + ai/skills/ ----
    'opencode_client': 'ai.opencode.opencode_client',
    'opencode_ownership': 'ai.opencode.opencode_ownership',
    'opencode_launch': 'ai.opencode.opencode_launch',
    'opencode_parts': 'ai.opencode.opencode_parts',
    'subagent_reuse_plugin': 'ai.opencode.subagent_reuse_plugin',
    'tool_timeout_plugin': 'ai.opencode.tool_timeout_plugin',
    'plugin_registry': 'ai.opencode.plugin_registry',
    'opencode_global': 'ai.opencode.opencode_global',
    'global_skills': 'ai.skills.global_skills',
    'skill_fit': 'ai.skills.skill_fit',
    'skill_fit_ai': 'ai.skills.skill_fit_ai',
    'skillopt': 'ai.skills.skillopt',
    'skill_candidate_eval': 'ai.skills.skill_candidate_eval',
    'skill_upload': 'ai.skills.skill_upload',
```
跑 `--check` → 实跑（同 Task 1 Step 5）

- [ ] **Step 5: 符号面 parity 校验**

```bash
cd server && python ../tools/refactor/symbol_parity.py ../tools/refactor/baselines/opencode_global.txt ai.opencode.opencode_global
```
（symbol_parity.py 代码见 Task 5 Step 1，本批先内联同样逻辑执行）
Expected: `MISSING`/`EXTRA` 均为空，退出码 0

- [ ] **Step 6: 插件生成物 byte 级验证**

```bash
cd server && rm -f "$OGD"/*.js && python -c "import app" && find "$OGD" -name '*.js' | sort | xargs sha256sum > ../tools/refactor/baselines/oc_deployed_js_after.txt
diff ../tools/refactor/baselines/oc_deployed_js_before.txt ../tools/refactor/baselines/oc_deployed_js_after.txt
```
Expected: diff 为空（`import app` 触发 ensure_* 重新部署，内容 byte 级一致）。若 diff 非空：查明差异原因，属模板路径问题的立即修复，不允许带差异进下一批。

- [ ] **Step 7: 机械三件套 + 全量单测 + Commit**（同 Task 1 Step 6-8，消息 `refactor(ai): OpenCode 集成与技能域分包，插件生成物 byte 级验证`）

---

### Task 4（批次3）: ai/sessions/ + ai/gate/ + ai/scan/

**Files:**
- Move: `server/utils/{chat_persist,ai_session_repo,session_admin_repo,session_history,session_file_import,session_token,ai_message_meta,ai_query,prompt_template,memory,takeover_repo,mention_files,artifact_store,status_badge_timeout_scheduler}.py` → `server/ai/sessions/`
- Move: `server/utils/{gate_policy_repo,action_check_extractor}.py` → `server/ai/gate/`
- Move: `server/utils/{ai_scan_engine,ai_scan_repo,ai_scan_scheduler,ai_schema_designer}.py` → `server/ai/scan/`
- Modify: `tools/refactor/rewrite_utils_imports.py`（MAPPING 追加 20 条，全部 `<name> → ai.sessions.<name>` / `ai.gate.<name>` / `ai.scan.<name>` 形式）

**Interfaces:** Consumes codemod；Produces `ai.sessions.*`（`routes/ai_chat.py` 等大量引用）、`ai.gate.*`、`ai.scan.*`

- [ ] **Step 1: MAPPING 追加（20 条，格式同前）**
- [ ] **Step 2: git mv 20 文件到三个包**
- [ ] **Step 3: codemod --check → 实跑**
- [ ] **Step 4: 机械三件套 + 全量单测**
- [ ] **Step 5: Commit**（`refactor(ai): 会话/门禁/扫描域分包`）

---

### Task 5（批次4a）: ai/batch/ 平铺 15 文件 + parity 工具 + 拆分基线

**Files:**
- Move: `server/utils/{batch_events,execution_audit,execution_budget,execution_effect,execution_contract,execution_lease,execution_commands,execution_checkpoint,verifier,oc_watchdog,todo_trace,trace_auditor,run_diagnosis,delivery_outbox,subtask_repo}.py` → `server/ai/batch/`
- Create: `tools/refactor/symbol_parity.py`
- Create: `tools/refactor/baselines/{batch_engine,batch_repo,agent_ledger,perf_analysis}.txt`
- Modify: `tools/refactor/rewrite_utils_imports.py`（MAPPING 追加 15 条 `<name> → ai.batch.<name>`）

**Interfaces:**
- Consumes codemod
- Produces: `symbol_parity.py` CLI（`python tools/refactor/symbol_parity.py <baseline.txt> <new.dotted.path>`，退出码 0=符号面一致）；4 份符号基线（Task 6/7 消费）

- [ ] **Step 1: 写 symbol_parity.py**

```python
#!/usr/bin/env python3
"""Split-parity check: new package surface must equal the captured baseline.

Usage: python tools/refactor/symbol_parity.py <baseline.txt> <new.dotted.path>
Baseline lines = top-level names (non-module, non-dunder) of the original
module, captured with capture_symbols before the split. Exit 0 = identical.
"""
import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server'))


def surface(mod):
    return {n for n, v in vars(mod).items()
            if not n.startswith('__') and not isinstance(v, types.ModuleType)}


def main():
    baseline = set(Path(sys.argv[1]).read_text(encoding='utf-8').split())
    new = surface(importlib.import_module(sys.argv[2]))
    for n in sorted(baseline - new):
        print('MISSING', n)
    for n in sorted(new - baseline):
        print('EXTRA', n)
    return 0 if not (baseline - new) and not (new - baseline) else 1


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 2: 捕获 4 份拆分基线（此时原文件仍在 utils/）**

```bash
cd server && for m in batch_engine batch_repo agent_ledger perf_analysis; do
  python -c "import utils.$m as m, types; print('\n'.join(sorted(n for n, v in vars(m).items() if not n.startswith('__') and not isinstance(v, types.ModuleType))))" > ../tools/refactor/baselines/$m.txt
done
```
Expected: 4 个非空基线文件（batch_engine 应有数百行）

- [ ] **Step 3: MAPPING 追加 15 条 + git mv 15 文件进 ai/batch/**
- [ ] **Step 4: codemod --check → 实跑**
- [ ] **Step 5: 机械三件套 + 全量单测**
- [ ] **Step 6: Commit**（`refactor(ai/batch): 批任务平铺模块归位，拆分基线与 parity 工具入库`）

---

### Task 6（批次4b）: batch_engine.py (4232) → ai/batch/engine/ 拆分

**Files:**
- Create: `server/ai/batch/engine/{__init__,facade,lifecycle,exceptions,discovery,worker,runner,gates,subtasks,transitions}.py`
- Delete: `server/utils/batch_engine.py`（git mv 到 worker.py 后消空）
- Modify: `tools/refactor/rewrite_utils_imports.py`（MAPPING: `'batch_engine': 'ai.batch.engine'`）

**Interfaces:**
- Consumes: Task 5 的基线 `tools/refactor/baselines/batch_engine.txt` 与 parity CLI
- Produces: `ai.batch.engine.get_worker` / `.BatchWorker` / 5 个异常类 / `discover_subtasks` / `subtask_status_from_messages` / `gate_participates` / `execution_lease_ttl` 等——符号面与原 `utils.batch_engine` 完全一致（parity 校验保证）

- [ ] **Step 1: `git mv utils/batch_engine.py ai/batch/engine/runner.py`**（最大块先落位，历史保留）
- [ ] **Step 2: 按 spec §5.1 分组逐字搬移**——从 `runner.py` 剪切以下内容到新叶子（剪切=整块位移，不改一字；每叶子复制原模块 import 头）：

| 叶子 | 搬入内容（原行号） |
|---|---|
| facade.py | `_OpenCodeFacade`(L93-269)、`get_worker()`、`execution_lease_ttl()`(L276-283) |
| lifecycle.py | `_workspace_root`/`_prepare_workspace`/`_recompute_batch_status`/`_cleanup_batch_oc_sessions`/`_notify_callback`(L288-576) |
| exceptions.py | `_SessionTimeout`/`_format_diagnosis`/`_TurnFailed`/`_SessionCancelled`/`_LeaseLost`/`_SessionPaused`(L580-675) |
| discovery.py | `discover_subtasks`/`subtask_status_from_messages`/`_raw_has_open_delegation`/`gate_participates`/`_require_fencing`(L676-783) |
| runner.py（保留） | `BatchWorker` 中单会话执行与完成保证方法：`_run_one`、工作区准备回收（`_provision_workspace`/`_record_workspace_files`/`_recover_session`/`_persist_user_prompt`/`_fetch_batch_context`/`_set_workspace_path`/`_set_opencode_id`/`_persist_provision_notice`/`_record_memory`）、完成保证（`_await_finished`/`_finish_with_guarantee`/`_progress_signature`/`_stall_diagnosis`/`_snapshot_assistant_ids`）——重组为 `class WorkerRunnerMixin:` |
| gates.py | mixin `WorkerGatesMixin`：`_fetch_action_checks`/`_register_action_expectations`/`_check_action_gate`/`_batch_gate_retry_flag`/`_maybe_gate_retry`/`_maybe_check_delegation_gate`/`_fetch_subagent_reuse`/`_build_reuse_directive`/`_audit_reuse_violations`/`_deleg_gate_batch`/`_deleg_gate_model`/`_check_agent` |
| subtasks.py | mixin `WorkerSubtasksMixin`：`_collect_subtasks`/`_write_subtask`/`_persist_conversation`/`_content_from_parts`/`_preview_from`/`_record_workspace_files` 归属冲突时以实际定义处为准（每符号只落一个叶子） |
| transitions.py | mixin `WorkerTransitionsMixin`：`_resolve_generation`/`_on_transition_miss`/`_mark_done`/`_mark_failed`/`_close_attempt_for_requeue`/`_consume_parent_attempt`/`_notify_child_done`/`_notify_scan` |
| worker.py | mixin `WorkerLifecycleMixin`：`__init__`/`start`/`stop`/`notify`/租约与监管线程（`_acquire_retry_loop`/`_start_with_lease`/`_lease_loop`/`_listen_*`/`_wait_for_wake`）/派发（`_dispatcher_loop`/`_effective_concurrency`/`_dispatch_tick`/`_safe_run_one`/`_claim_pending_sessions`/`_cancel_pending_requests`/`_restart_audit`/`_resolve_max_concurrent`）+ 组合类：`class BatchWorker(WorkerRunnerMixin, WorkerGatesMixin, WorkerSubtasksMixin, WorkerTransitionsMixin):`（`WorkerLifecycleMixin` 为本体） |

注意：`_run_one` 与 mixin 方法体内引用的模块级助手（如 `_recompute_batch_status`、`_prepare_workspace`、异常类、`discover_subtasks`）由 pyflakes F821 驱动补包内导入（`from .lifecycle import ...` 等）。

- [ ] **Step 3: 每叶子加 `__all__`（列本文件定义的顶层名字）+ 聚合 `__init__.py`**

```python
"""原 utils.batch_engine 的聚合导出：符号面与拆分前完全一致。"""
from .facade import *  # noqa: F401,F403
from .exceptions import *  # noqa: F401,F403
from .discovery import *  # noqa: F401,F403
from .lifecycle import *  # noqa: F401,F403
from .worker import *  # noqa: F401,F403
from .runner import *  # noqa: F401,F403
from .gates import *  # noqa: F401,F403
from .subtasks import *  # noqa: F401,F403
from .transitions import *  # noqa: F401,F403
```

- [ ] **Step 4: MAPPING 追加 + codemod --check → 实跑**（全仓 `from utils.batch_engine import X` → `from ai.batch.engine import X`）
- [ ] **Step 5: 符号面 parity**

```bash
cd server && python ../tools/refactor/symbol_parity.py ../tools/refactor/baselines/batch_engine.txt ai.batch.engine
```
Expected: 退出码 0。非 0 时修复（MISSING=漏搬/漏导出；EXTRA=__init__ 误导出叶子模块名——确认叶子模块都已被 ModuleType 过滤，通常是 `__all__` 拼错）

- [ ] **Step 6: mixin 方法完整性断言**

```bash
cd server && python -c "
from ai.batch.engine import BatchWorker
from ai.batch.engine.worker import WorkerLifecycleMixin
from ai.batch.engine.runner import WorkerRunnerMixin
from ai.batch.engine.gates import WorkerGatesMixin
from ai.batch.engine.subtasks import WorkerSubtasksMixin
from ai.batch.engine.transitions import WorkerTransitionsMixin
names = [n for c in (WorkerLifecycleMixin, WorkerRunnerMixin, WorkerGatesMixin, WorkerSubtasksMixin, WorkerTransitionsMixin) for n in vars(c)]
dup = {n for n in names if names.count(n) > 1}
assert not dup, 'mixin 方法重叠: %s' % dup
print('mixin 方法无重叠, BatchWorker 方法数:', len([n for n in vars(BatchWorker) if not n.startswith('__')]))
"
```
Expected: 输出 mixin 方法无重叠

- [ ] **Step 7: 机械三件套 + 全量单测**（batch 域测试 `tests/test_ai_batch*` 重点观察）
- [ ] **Step 8: Commit**（`refactor(ai/batch): batch_engine 4232 行拆为 engine/ 九模块，符号面 parity 校验通过`）

---

### Task 7（批次4c）: batch_repo + agent_ledger + perf_analysis 三拆

**Files:**
- Create: `server/ai/batch/repo/{__init__,status,children,config,lifecycle,queries}.py`
- Create: `server/ai/batch/ledger/{__init__,extract,record,query}.py`
- Create: `server/ai/batch/perf/{__init__,intervals,runs,report}.py`
- Delete: `server/utils/{batch_repo,agent_ledger,perf_analysis}.py`
- Modify: `tools/refactor/rewrite_utils_imports.py`（MAPPING: 3 条→包路径）

**Interfaces:**
- Consumes: Task 5 基线 ×3 + parity CLI
- Produces: `ai.batch.repo.*` / `ai.batch.ledger.*` / `ai.batch.perf.*`，符号面与原模块一致

- [ ] **Step 1: 三个包各按 spec §5.2/§5.3/§5.4 分组拆分**（程序同 Task 6：git mv 最大块 → 逐字剪切分组 → `__all__` → 聚合 `__init__`）：
  - repo：`lifecycle.py`（create/cancel/pause/resume/delete/append/reset_failed/_recompute_batch_status_for）最大先 mv；`status.py`（recompute_batch_status_tx）、`children.py`（transition_child/read_child_control_state/cancel_child/get_child_session/reexecute_child/continue_child/resume_child/try_repark_transition）、`config.py`（validate_*/assign_child_models/update_batch_config/get_max_files_per_batch）、`queries.py`（list/get/results/usage/messages/_text_from_content）
  - ledger：`record.py` 最大先 mv（record_messages/record_state/finalize_interactive_turn/_fill_subtask_meta/_registered_subtask_ids）；`extract.py`（args_to_text/_start_dt/extract_from_parts/extract_from_part_map/extract_tool_parts）；`query.py`（list_tool_calls/reconcile_task_durations）
  - perf：`runs.py` 最大先 mv（resolve_session_runs/_run_attempt_row/_open_conn 等）；`intervals.py`（_union/_covered_ms/_subtract/_intersect/coverage_split/_to_ms/_now_ms/_percentile）；`report.py`（其余报告聚合函数，按 def 清单归位）
- [ ] **Step 2: MAPPING 3 条（'batch_repo': 'ai.batch.repo', 'agent_ledger': 'ai.batch.ledger', 'perf_analysis': 'ai.batch.perf'）+ codemod**
- [ ] **Step 3: parity ×3**（`symbol_parity.py` 对 `ai.batch.repo`/`ai.batch.ledger`/`ai.batch.perf`，基线在 Task 5 Step 2 已捕获）Expected: 全部退出码 0
- [ ] **Step 4: 机械三件套 + 全量单测**
- [ ] **Step 5: Commit**（`refactor(ai/batch): batch_repo/agent_ledger/perf_analysis 拆包，parity 全过`）

---

### Task 8（批次5a）: ai/orchestration/ + orchestration_engine 拆分

**Files:**
- Move: `server/utils/{orchestration_defs,approval_repo}.py` → `server/ai/orchestration/`
- Create: `server/ai/orchestration/engine/{__init__,runs,steps,approvals}.py`（由 `utils/orchestration_engine.py` 1104 行拆分）
- Create: `tools/refactor/baselines/orchestration_engine.txt`
- Modify: codemod MAPPING（3 条：`orchestration_defs`/`approval_repo` → `ai.orchestration.<name>`；`orchestration_engine` → `ai.orchestration.engine`）

**Interfaces:** Consumes codemod + parity CLI；Produces `ai.orchestration.engine.*`（`routes/ai_orchestrations.py` 引用）

- [ ] **Step 1: 捕获基线**（命令同 Task 5 Step 2，模块名 `orchestration_engine`）
- [ ] **Step 2: git mv 两平铺文件 + engine 拆分**：`runs.py` 最大先 mv（create/get/list_run + `_advance_run`/`_advance_run_locked`/`_run_edges`/`_edge_matches`/`_render_prompt`）；`steps.py`（`_launch_agent_step` 及步骤启动辅助）；`approvals.py`（`_open_approval`/`_open_policy_approval`/`_abort_timeout_child`）；聚合 `__init__` + `__all__`
- [ ] **Step 3: MAPPING + codemod --check → 实跑**
- [ ] **Step 4: parity**（`ai.orchestration.engine`）+ 机械三件套 + 全量单测
- [ ] **Step 5: Commit**（`refactor(ai/orchestration): 编排域分包，engine 拆三模块 parity 通过`）

---

### Task 9（批次5b）: projects/ 平铺 5 + versioning/backup/dependencies 三拆

**Files:**
- Move: `server/utils/{version,branch_lock,workspace,workspace_changes,workspace_outputs}.py` → `server/projects/`
- Create: `server/projects/versioning/{__init__,repo,diff,merge,restore}.py`（project_version.py 2267）
- Create: `server/projects/backup/{__init__,io,service,scheduler,factory_reset}.py`（backup.py 1492）
- Create: `server/projects/dependencies/{__init__,repo,scan,checks}.py`（cross_project_dependency.py 1122）
- Create: 3 份基线 txt
- Modify: codemod MAPPING（8 条）

**Interfaces:** Consumes codemod + parity；Produces `projects.versioning/backup/dependencies.*`（`routes/project_versions.py`、`routes/backups.py`、`routes/cross_project_dependencies.py` 引用）

- [ ] **Step 1: 捕获 3 基线**（project_version/backup/cross_project_dependency）
- [ ] **Step 2: 三包拆分**（程序同 Task 6-7；分组按 spec §5.5/§5.6/§5.7 表）：
  - versioning：`merge.py` 最大先 mv（merge_project_version/merge_project_version_detailed/_rollback_merge）；`repo.py`（分支/版本 CRUD 九函数）；`diff.py`（load_*/compute_*/get_collection_fields）；`restore.py`（restore_from_project_version/get_project_version_delete_impact/lock_*/unlock_*）
  - backup：`service.py` 最大先 mv（create_backup/restore_backup/compute_restore_warnings/delete_backup_file）；`io.py`（zip/序列化/导表 11 函数）；`scheduler.py`（settings/is_backup_due/cleanup/start_backup_scheduler）；`factory_reset.py`（factory_reset）
  - dependencies：`repo.py` 最大先 mv（CRUD+check_branch_exists）；`scan.py`（scan/save/get_dependency_relations）；`checks.py`（check_data_reachability/check_circular_dependency）
- [ ] **Step 3: MAPPING 8 条 + codemod**
- [ ] **Step 4: parity ×3 + 机械三件套 + 全量单测**
- [ ] **Step 5: Commit**（`refactor(projects): 项目版本/备份/跨项目依赖分包，三拆 parity 全过`）

---

### Task 10（批次5c）: collab/ 14 文件

**Files:**
- Move: `server/utils/{workflow,workflow_engine,workflow_repo,row_action_engine,row_action_validate,row_action_condition,trigger_engine,webhook,webhook_engine,kefu_repo,kefu_event_bus,kefu_sse_ticket,kefu_guardrail,menu_export}.py` → `server/collab/`
- Modify: codemod MAPPING（14 条 `<name> → collab.<name>`）

**Interfaces:** Consumes codemod；Produces `collab.*`（`routes/workflows.py`、`routes/webhooks.py`、`routes/kefu_*` 引用）

- [ ] **Step 1: MAPPING 14 条 + git mv 14 文件**
- [ ] **Step 2: codemod --check → 实跑**
- [ ] **Step 3: 机械三件套 + 全量单测**
- [ ] **Step 4: Commit**（`refactor(collab): 工作流/行动/客服/webhook 域分包`）

---

### Task 11（批次6）: app.py 迁移块收敛 + 收尾 + 删 utils/

**Files:**
- Modify: `server/app.py:169-178,324-约620`（32 个 `spec_from_file_location` 块 → 两个注册表 + 单循环）
- Modify: `mcp-server/tools/_server_imports.py`（docstring 更新）
- Modify: `server/db_schema/core.py`（4 处注释里的 utils 路径更新）
- Delete: `server/utils/`（整个目录）
- Modify: codemod MAPPING（此时应为全量；若有遗漏条目补上）

**Interfaces:**
- Consumes: 前序全部批次
- Produces: `BOOT_MIGRATIONS` 注册表（新迁移登记点，替代原「双注册」痛点）

- [ ] **Step 1: app.py 迁移块收敛**——L169-178 的 execution-audit 块替换为：

```python
# 迁移启动注册表（显式有序，行为等价于原逐段 try/except 块；新迁移必须在此
# 登记且顺序不可调整——后续迁移依赖前面迁移建的表。init_db 的自动扫描不覆盖
# 生产重启路径，见 docs/superpowers/specs/2026-11-03-backend-restructure-design.md §7）。
# SkillOpt/插件启动块（原 L182-322）位于 PRE 与 MAIN 之间，执行顺序原样保留。
_BOOT_MIGRATIONS_PRE = [
    ('2026_09_17_execution_audit_tables.py',
     'execution audit migration on boot failed'),
]


def _run_boot_migrations(entries):
    import importlib.util as _ilu
    _base = os.path.dirname(os.path.abspath(__file__))
    for _fname, _errmsg in entries:
        _mp = os.path.join(_base, 'migrations', _fname)
        try:
            _spec = _ilu.spec_from_file_location('_boot_' + _fname[:-3], _mp)
            _m = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            _m.run()
        except Exception as _e:
            logging.warning('%s: %s', _errmsg, _e)


_run_boot_migrations(_BOOT_MIGRATIONS_PRE)
```

L324 起（原 session-groups 块到 session-permission-mode 块，共 31 个）整段替换为：

```python
_BOOT_MIGRATIONS_MAIN = [
    ('2026_09_18_session_groups.py', 'session groups migration on boot failed'),
    ('2026_09_18_session_group_icon.py', 'session group icon migration on boot failed'),
    ('2026_09_19_session_pin.py', 'session pin migration on boot failed'),
    ('2026_09_18_scan_output_field.py', 'scan output field migration on boot failed'),
    ('2026_09_20_agent_action_gate.py', 'agent action gate migration on boot failed'),
    ('2026_09_18_skillopt_p2.py', 'skillopt p2 migration on boot failed'),
    ('2026_09_21_batch_child_soft_delete.py', 'batch child soft-delete migration on boot failed'),
    ('2026_09_23_harness_p0_execution_safety.py', 'harness p0 migration on boot failed'),
    ('2026_09_23_harness_p1_durable_execution.py', 'harness p1 migration on boot failed'),
    ('2026_09_23_harness_p2_orchestration.py', 'harness p2 migration on boot failed'),
    ('2026_09_24_subagent_session_reuse.py', 'subagent reuse migration on boot failed'),
    ('2026_09_25_artifact_owner_scope.py', 'artifact owner scope migration on boot failed'),
    ('2026_09_28_message_search_text.py', 'message search_text migration on boot failed'),
    ('2026_09_29_tool_calls_backfill.py', 'tool calls backfill migration on boot failed'),
    ('2026_09_29_embedding_provider.py', 'embedding provider migration on boot failed'),
    ('2026_09_26_batch_notify_trigger.py', 'batch notify trigger migration on boot failed'),
    ('2026_09_26_action_gate_mode.py', 'action gate mode migration on boot failed'),
    ('2026_10_11_gate_checkpoint_policy.py', 'gate checkpoint/policy migration on boot failed'),
    ('2026_10_01_skill_fit_tables.py', 'skill fit tables migration on boot failed'),
    ('2026_10_05_def_version_content_archive.py', 'def version content archive migration on boot failed'),
    ('2026_10_27_run_diagnoses.py', 'run diagnoses migration on boot failed'),
    ('2026_10_27_skill_candidates.py', 'skill candidates migration on boot failed'),
    ('2026_10_10_tool_call_duration.py', 'tool call duration migration on boot failed'),
    ('2026_10_18_suggest_editor_tables.py', 'suggest editor migration on boot failed'),
    ('2026_10_21_suggest_editor_attachments.py', 'suggest editor attachments migration on boot failed'),
    ('2026_10_25_batch_takeover.py', 'batch takeover migration on boot failed'),
    ('2026_10_25b_platform_plugins.py', 'platform plugins migration on boot failed'),
    ('2026_10_25c_mcp_tool_governance.py', 'mcp tool governance migration on boot failed'),
    ('2026_10_27_batch_multi_model.py', 'batch multi-model migration on boot failed'),
    ('2026_10_30_message_feedback.py', 'message feedback migration on boot failed'),
    ('2026_10_31_session_permission_mode.py', 'session permission_mode migration on boot failed'),
]

_run_boot_migrations(_BOOT_MIGRATIONS_MAIN)
```

要点：错误消息字符串为原文逐字提取（`grep -oE "logging\.warning\('[^']+" server/app.py` 核对）；执行顺序 = 原块在文件中的出现顺序（非文件名日期序）；`_ilu` 局部导入避免污染；块间夹着的 SkillOpt/插件/scheduler 块（原 L182-322）**原样保留不动**，仅在其前后插入两次 `_run_boot_migrations` 调用。

- [ ] **Step 2: 收尾杂项**——`mcp-server/tools/_server_imports.py` docstring 中 `server/utils` 表述改为新包说明；`db_schema/core.py` 注释里 4 处 `utils/xxx.py` 路径更新为新路径；codemod 全量映射终跑 `--check` 确认零待改
- [ ] **Step 3: 删 utils/**

```bash
cd server && grep -rn '^import utils$\|^import utils\.\|from utils' --include='*.py' . ../mcp-server/tools ../mcp-server/tests ../scripts | grep -v __pycache__   # 必须为空
git rm -r utils && rm -rf utils  # 清理 pycache 残留
```

- [ ] **Step 4: 启动冒烟 + 机械三件套 + 全量单测**

```bash
cd server && python -c "import app" && echo BOOT_OK
```
Expected: `BOOT_OK`，无未处理异常（迁移失败 warning 属正常幂等回执）；三件套与全量单测同前

- [ ] **Step 5: Commit**（`refactor(app): 32 段迁移启动块收敛为 BOOT_MIGRATIONS 注册表，utils/ 目录退役`）

---

### Task 12: 最终门禁（E2E）+ 合并准备

**Files:** 无代码改动（验证 + 合并准备）

**Interfaces:** Consumes 全部批次；Produces 合并就绪结论

- [ ] **Step 1: 起全栈**——后端 + proxy 整套（BACKEND_URL 显式指 3002）+ 前端 + se:mock，确认 `:5173` 代理栈就绪
- [ ] **Step 2: 数据管理 E2E 全量**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest -q -m data_full
```
Expected: L2 101 + L3 26 全绿

- [ ] **Step 3: Playwright 核心套件**（repo 根执行，spec 按 `playwright.config.ts` testDir='./e2e'）

```bash
npx playwright test suggest-editor-usability.spec.ts suggest-editor.spec.ts ai-chat-smoke.spec.ts external-takeover.spec.ts
```
Expected: 全绿

- [ ] **Step 4: 真机人工冒烟**——登录 → 普通会话一轮 → 批任务一轮（mock 模型）→ 智能写作一篇 → 数据页表格/导出，页面无白屏无 405/500
- [ ] **Step 5: 遗留清点**——`grep -rn '\butils\b' --include='*.py' server/ mcp-server/tools mcp-server/tests scripts | grep -v __pycache__ | grep -v '^\s*#'`，允许残留仅限：纯文本注释里对历史的描述。整理分支 commit 列表与门禁证据，汇报用户
- [ ] **Step 6: 等待用户确认后合回 main**（合回前 `git rebase main` 复跑 Task 12 全部门禁）

---

## Self-Review 记录

1. **Spec 覆盖**：§4 映射 113 文件 → Task 1-10 全覆盖（15+19+14+20+15+拆4+3+8+14 = 108 移动 + opencode_global/orchestration_engine 并入拆分 + utils/__init__ 随目录删除）；§5 八拆 → Task 3/6/7/8/9；§6 codemod 四规则 → Task 1 脚本；§7 七个耦合点 → Task 2（reload/mcp-server）、Task 3（byte 验证）、Task 11（BOOT_MIGRATIONS/db_schema）；§8 门禁 → 每批 Step + Task 12；§10 回退 → 全局约束。
2. **占位符扫描**：无 TBD/TODO；所有代码块为完整可运行内容；perf/report.py 的归位依据是「按 def 清单归位」+ §5.4 轴定义（实施时 def 清单即代码本身，非占位）。
3. **一致性**：codemod 绑定名规则（旧名恒定）与拆分包聚合 `__init__` 贯穿全部任务；MAPPING 条目格式统一 `<旧名>: '<新dotted>'`；parity 基线文件名与消费任务一一对应（batch_engine/batch_repo/agent_ledger/perf_analysis → Task 6/7；opencode_global → Task 3；orchestration_engine → Task 8；project_version/backup/cross_project_dependency → Task 9）。
