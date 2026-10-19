# 演示客服 seed 脚本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增幂等 seed，让 `python init_db.py` 后全新库自动带一个「演示客服」（`/kefu/demo`），也可 `python seed_kefu.py` 单独补种。

**Architecture:** 新模块 `server/seed_kefu.py` 复用 `kefu_repo.create_instance`/`create_faq`，按 slug 幂等；`init_db.py` 末尾 best-effort 调用。仅后端，复用现有仓库函数，不改业务逻辑。

**Tech Stack:** Python + psycopg2（经 `kefu_repo`）+ pytest（mock-based，patch `kefu_repo`）。

## Global Constraints

- **复用** `kefu_repo` 的 `get_instance_by_slug(slug)`、`create_instance(payload)`、`create_faq(instance_id, payload)`——不重写 SQL。`seed_kefu.py` 以 `from utils import kefu_repo` 导入（与 `routes/kefu_admin.py` 一致）。
- **幂等**：`get_instance_by_slug(spec['slug'])` 命中即跳过，返回 `False`，**不覆盖、不重复**；新建返回 `True`。
- **DEMO 内容**（精确，复用当前 demo）：slug=`demo`，name=`演示客服`，welcome=`你好 👋 我是在线客服，有什么可以帮你？`，3 个引导问，4 个区块（links/faq/richtext/contact），2 条 FAQ。
- **init_db 集成**：在 `init_db()` 主体末尾 try/except best-effort 调用 `seed_kefu_demo()`，失败打印警告不中断。
- **测试 mock-based**：patch `seed_kefu.kefu_repo.*`，不连真实 DB、不污染真实 demo（遵循本仓库 kefu 测试惯例——`conftest.py` mock `db.get_db`、route 测试 `@patch` repo）。运行：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_seed_kefu.py -v`。
- 提交只 `git add` 该任务确切文件，禁止 `git add -A`。

---

### Task 1: `server/seed_kefu.py` + 单测（TDD）

**Files:**
- Create: `server/seed_kefu.py`
- Create: `server/tests/test_seed_kefu.py`

**Interfaces:**
- Consumes: `kefu_repo.get_instance_by_slug(slug) -> dict|None`、`create_instance(payload: dict) -> dict`（返回含 `id`）、`create_faq(instance_id: str, payload: dict) -> dict`。
- Produces: `seed_kefu_instance(spec: dict, faqs: list) -> bool`、`seed_kefu_demo() -> bool`、常量 `DEMO_INSTANCE`、`DEMO_FAQ`（`server/seed_kefu.py`）。

- [ ] **Step 1: 写失败测试**

`server/tests/test_seed_kefu.py`:

```python
from unittest.mock import patch
import seed_kefu
from seed_kefu import seed_kefu_instance, seed_kefu_demo, DEMO_INSTANCE, DEMO_FAQ


def test_seed_creates_when_absent():
    spec = {'slug': 'demo-seedtest', 'name': 'T'}
    faqs = [{'question': 'q1', 'answer': 'a1'}, {'question': 'q2', 'answer': 'a2'}]
    with patch.object(seed_kefu.kefu_repo, 'get_instance_by_slug', return_value=None), \
         patch.object(seed_kefu.kefu_repo, 'create_instance',
                      return_value={'id': 'kf_x', 'slug': 'demo-seedtest'}) as mc, \
         patch.object(seed_kefu.kefu_repo, 'create_faq') as mf:
        created = seed_kefu_instance(spec, faqs)
    assert created is True
    mc.assert_called_once_with(spec)
    assert mf.call_count == 2
    assert mf.call_args_list[0].args[0] == 'kf_x'   # faq attached to created instance id


def test_seed_skips_when_present():
    with patch.object(seed_kefu.kefu_repo, 'get_instance_by_slug',
                      return_value={'id': 'kf_e', 'slug': 'demo'}), \
         patch.object(seed_kefu.kefu_repo, 'create_instance') as mc, \
         patch.object(seed_kefu.kefu_repo, 'create_faq') as mf:
        created = seed_kefu_instance({'slug': 'demo'}, DEMO_FAQ)
    assert created is False
    mc.assert_not_called()
    mf.assert_not_called()


def test_demo_constants_shape():
    assert DEMO_INSTANCE['slug'] == 'demo'
    assert DEMO_INSTANCE['name'] == '演示客服'
    assert len(DEMO_INSTANCE['guided_questions']) == 3
    assert [b['type'] for b in DEMO_INSTANCE['panel_blocks']] == ['links', 'faq', 'richtext', 'contact']
    assert len(DEMO_FAQ) == 2


def test_seed_demo_uses_demo_constants():
    with patch('seed_kefu.seed_kefu_instance', return_value=True) as m:
        result = seed_kefu_demo()
    assert result is True
    m.assert_called_once_with(DEMO_INSTANCE, DEMO_FAQ)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_seed_kefu.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seed_kefu'`（或 import 错误）。

- [ ] **Step 3: 写 `server/seed_kefu.py`**

```python
"""幂等 seed 一个演示客服实例（/kefu/demo）。

可由 init_db.py 末尾自动调用，也可单独运行：
    cd server && python seed_kefu.py
已存在同 slug 则跳过，绝不覆盖已有数据。
"""
from utils import kefu_repo

DEMO_INSTANCE = {
    'slug': 'demo',
    'name': '演示客服',
    'welcome_message': '你好 👋 我是在线客服，有什么可以帮你？',
    'guided_questions': ['如何私有化部署？', '有哪些定价套餐？', '怎么申请试用？'],
    'panel_blocks': [
        {'id': 'blk_l', 'type': 'links', 'title': '快捷入口', 'enabled': True,
         'config': {'items': [
             {'url': 'https://example.com/docs', 'icon': '📘', 'label': '帮助文档'},
             {'url': '/tickets', 'icon': '🎫', 'label': '提交工单'}]}},
        {'id': 'blk_f', 'type': 'faq', 'title': '热点问题', 'enabled': True,
         'config': {'limit': 5}},
        {'id': 'blk_r', 'type': 'richtext', 'title': '公告', 'enabled': True,
         'config': {'markdown': '## 服务时间\n工作日 **9:00–18:00**'}},
        {'id': 'blk_c', 'type': 'contact', 'title': '联系我们', 'enabled': True,
         'config': {'phone': '400-123-4567', 'email': 'help@example.com'}},
    ],
}

DEMO_FAQ = [
    {'question': '如何重置密码？', 'category': '账户',
     'answer': '进入设置页，点击“重置密码”，按邮件指引操作。', 'enabled': True},
    {'question': '支持哪些支付方式？', 'category': '支付',
     'answer': '支付宝 / 微信 / 对公转账。', 'enabled': True},
]


def seed_kefu_instance(spec: dict, faqs: list) -> bool:
    """幂等 seed 一个客服实例 + 其 FAQ。已存在同 slug 则跳过返回 False；新建返回 True。"""
    if kefu_repo.get_instance_by_slug(spec['slug']):
        return False
    inst = kefu_repo.create_instance(spec)
    for f in faqs:
        kefu_repo.create_faq(inst['id'], f)
    return True


def seed_kefu_demo() -> bool:
    created = seed_kefu_instance(DEMO_INSTANCE, DEMO_FAQ)
    print('已 seed 演示客服（/kefu/demo）' if created else '演示客服已存在，跳过')
    return created


if __name__ == '__main__':
    seed_kefu_demo()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_seed_kefu.py -v`
Expected: PASS（4 用例）。

- [ ] **Step 5: Commit**

```bash
git add server/seed_kefu.py server/tests/test_seed_kefu.py
git commit -m "feat(kefu): 幂等演示客服 seed（server/seed_kefu.py）+ 单测"
```

---

### Task 2: `init_db.py` 集成 + 文档 + 实机幂等验证

**Files:**
- Modify: `server/init_db.py`（`init_db()` 末尾 best-effort 调用）
- Modify: `docs/user-guide/ai/smart-customer-service.md`（第 2 节前置条件）
- Modify: `CLAUDE.md`（init_db 说明补一句）

**Interfaces:**
- Consumes: Task 1 的 `seed_kefu_demo()`。

- [ ] **Step 1: 定位 `init_db()` 主体末尾并插入调用**

在 `server/init_db.py` 的 `init_db()` 函数体末尾（其数据库工作与提交都完成、`return`/函数结束之前；即所有表、`kefu-guest` 角色、管理员 seed 之后）插入：

```python
    # seed 演示客服（幂等；失败不影响 init）—— 依赖 kefu 表与 kefu-guest 角色已建
    try:
        from seed_kefu import seed_kefu_demo
        seed_kefu_demo()
    except Exception as e:
        print(f"[warn] seed 演示客服失败（非致命）：{e}")
```

（`init_db.py` 顶部已 `sys.path.insert(0, os.path.dirname(__file__))`，故 `from seed_kefu import ...` 可解析。）确认插入点在 `init_db()` 内、且在其自身 DB 连接的提交/关闭之后（避免与 `kefu_repo` 连接池交叉在同一事务里）。

- [ ] **Step 2: 实机幂等验证（对当前开发库）**

Run: `cd server && python seed_kefu.py`
Expected: 打印 `演示客服已存在，跳过`（当前库已有 demo，证明幂等不覆盖）。

Run: `cd server && python -c "from db import get_db; conn=get_db().__enter__(); cur=conn.cursor(); cur.execute(\"SELECT count(*) FROM kefu_instances WHERE slug='demo'\"); print('demo rows:', cur.fetchone()[0])"`
Expected: `demo rows: 1`（未重复）。

- [ ] **Step 3: 更新用户指南前置条件**

在 `docs/user-guide/ai/smart-customer-service.md` 第 2 节「## 2. 前置条件」中，补一条：

```markdown
- **演示实例**：`python init_db.py` 会自动 seed 一个「演示客服」（访问 `/kefu/demo`），已存在则跳过、不覆盖。也可随时 `cd server && python seed_kefu.py` 手动补种。
```

（若第 2 节是列表则作为新列表项加入；若为段落则新起一行要点。）

- [ ] **Step 4: 更新 CLAUDE.md**

在 `CLAUDE.md` 中 init_db 的说明处，把：

```
# Initialize database (runs DDL and seeds default admin)
cd server && python init_db.py
```

改为：

```
# Initialize database (runs DDL, seeds default admin, and seeds a demo kefu instance at /kefu/demo)
cd server && python init_db.py
```

- [ ] **Step 5: 运行 seed 单测 + 快速回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_seed_kefu.py -v`
Expected: PASS（4 用例，未受 init_db 改动影响）。

- [ ] **Step 6: Commit**

```bash
git add server/init_db.py docs/user-guide/ai/smart-customer-service.md CLAUDE.md
git commit -m "feat(kefu): init_db 末尾自动 seed 演示客服 + 文档"
```

---

## Self-Review

**Spec coverage:**
- `seed_kefu.py` 常量 + `seed_kefu_instance`（幂等核心）+ `seed_kefu_demo` + `__main__` → Task 1 Step 3 ✓
- 幂等：get_instance_by_slug 判存、跳过不覆盖 → Task 1 Step 3 + `test_seed_skips_when_present` ✓
- DEMO 内容精确（welcome/3 引导/4 区块/2 FAQ）→ Task 1 常量 + `test_demo_constants_shape` ✓
- init_db 末尾 best-effort 调用 → Task 2 Step 1 ✓
- 依赖顺序（kefu-guest 角色先建）→ Task 2 Step 1（插入点在角色 seed 之后）✓
- 测试 create + 幂等 + 内容，不污染真实 demo → Task 1（mock-based）✓
- 实机幂等验证 → Task 2 Step 2 ✓
- 文档（用户指南 + CLAUDE.md）→ Task 2 Step 3/4 ✓

**Placeholder scan:** 无 TBD/TODO；每个 code step 含完整代码；测试含具体断言。✓

**Type consistency:**
- `seed_kefu_instance(spec, faqs) -> bool` / `seed_kefu_demo() -> bool` 在 Task 1 定义，Task 2 调 `seed_kefu_demo()` 一致 ✓
- 复用 `kefu_repo.get_instance_by_slug`/`create_instance`/`create_faq` 签名与仓库一致（`create_instance` 返回含 `id`；`create_faq(instance_id, payload)`）✓
- `DEMO_INSTANCE`/`DEMO_FAQ` 常量名在常量定义、`seed_kefu_demo`、测试间一致 ✓

**注（偏离 spec 的测试机制）**：spec 的测试小节描述「真实 DB + 一次性 slug + teardown」；本计划改为**mock-based**（patch `kefu_repo`），因为这是本仓库 kefu 测试的既有惯例（`conftest.py` mock `db.get_db`、route 测试 `@patch` repo），且能达到 spec 的意图（覆盖 create/幂等/内容且**完全不碰真实 demo**）——是严格改进，执行前会向用户知会。
