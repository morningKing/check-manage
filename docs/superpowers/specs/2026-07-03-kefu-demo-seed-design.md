# 演示客服 seed 脚本 设计

**日期**: 2026-07-03
**分支**: feat/kefu-self-service-faq
**范围**: 后端。新增幂等 seed，让 `python init_db.py` 后全新数据库自动带一个「演示客服」（`/kefu/demo`），也可单独运行。复用现有仓库函数与当前 demo 内容，不改前端、不改后端业务逻辑。

## 背景与问题

`init_db.py` / `migrate_kefu.py` 只创建 kefu 表 + `kefu-guest` 角色，`seed_data.py` 只 seed 管理员——**没有任何脚本构造客服实例**。当前 DB 里的 `演示客服`（slug=`demo`）是开发期手动通过管理 API 建的（`created_at` 2026-07-02）。因此在全新数据库上访问 `/kefu/demo` 会失败，文档里的 `/kefu/demo` 示例不成立。

## 目标

- `python init_db.py` 后全新库开箱即用一个 `演示客服`（`/kefu/demo`）。
- 也能 `python seed_kefu.py` 单独补种。
- **幂等**：已存在同 slug 则跳过，绝不覆盖用户数据、不重复插入。

## 非目标（YAGNI）

- 不改前端、不改 kefu 业务逻辑/路由/仓库函数（仅**调用**它们）。
- 不 seed 除 demo 外的实例。
- 不做删除/重置 demo 的命令。

## 设计

### 1. 新模块 `server/seed_kefu.py`

**内容常量**（完全复用当前 demo 内容）：

```python
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
```

**幂等核心**（可被测试用任意 slug 调用，不硬编码 demo）：

```python
def seed_kefu_instance(spec: dict, faqs: list) -> bool:
    """幂等 seed 一个客服实例 + 其 FAQ。已存在同 slug 则跳过并返回 False；
    新建成功返回 True。复用 kefu_repo.create_instance / create_faq。"""
    if kefu_repo.get_instance_by_slug(spec['slug']):
        return False
    inst = kefu_repo.create_instance(spec)
    for f in faqs:
        kefu_repo.create_faq(inst['id'], f)
    return True
```

**薄封装 + 单独入口**：

```python
def seed_kefu_demo() -> bool:
    created = seed_kefu_instance(DEMO_INSTANCE, DEMO_FAQ)
    print('已 seed 演示客服（/kefu/demo）' if created else '演示客服已存在，跳过')
    return created

if __name__ == '__main__':
    seed_kefu_demo()
```

（`create_instance` 内部会 `ensure_bot_user()` 建 kefu-bot 用户，角色 `kefu-guest`。）

### 2. `init_db.py` 集成

在 `init_db()` 主体末尾（表 / `kefu-guest` 角色 / 管理员都已建好、其自身 DB 提交之后）追加 best-effort 调用：

```python
    # seed 演示客服（幂等；失败不影响 init）
    try:
        from seed_kefu import seed_kefu_demo
        seed_kefu_demo()
    except Exception as e:
        print(f"[warn] seed 演示客服失败（非致命）：{e}")
```

放末尾保证依赖顺序：`kefu-guest` 角色先于 `ensure_bot_user` 存在。`seed_kefu` 用 `kefu_repo`（`get_db` 连接池），与 init_db 自身的原生连接独立，不干扰其事务。

### 3. 幂等语义

按 `slug` 判存（`get_instance_by_slug`）。已存在 → 跳过，返回 False，不覆盖、不重复。反复运行 `init_db.py` / `seed_kefu.py` 安全。

## 数据流

`seed_kefu_demo → seed_kefu_instance → kefu_repo.create_instance + create_faq`（现有函数，走连接池）。无新表、无新路由。`init_db.py` 仅在末尾多调一次。

## 错误处理

- init_db 集成点 try/except：seed 失败打印警告但不中断 init（demo 非关键数据）。
- 单独 `python seed_kefu.py`：异常直接抛出（便于人工排查）。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `server/seed_kefu.py` | demo 常量 + 幂等 seed 函数 + 单独入口 | **新增** |
| `server/init_db.py` | `init_db()` 末尾 best-effort 调用 `seed_kefu_demo()` | 改（+~5 行） |
| `server/tests/test_seed_kefu.py` | seed 幂等/内容单测 | **新增** |

## 测试

`server/tests/test_seed_kefu.py`（pytest，真实 DB；用**一次性 slug** 避免污染真实 `demo`）：

- **create 路径**：先确保 `slug='demo-seedtest'` 不存在；`seed_kefu_instance({**DEMO_INSTANCE, 'slug': 'demo-seedtest'}, DEMO_FAQ)` → 返回 `True`；`get_instance_by_slug('demo-seedtest')` 存在，其 `guided_questions` / `panel_blocks` / `welcome_message` 与 spec 一致；该实例下 FAQ 数=2。
- **幂等路径**：再次调用 → 返回 `False`；实例仍 1 个、FAQ 仍 2 条（无重复）。
- **teardown**：删除 `slug='demo-seedtest'` 实例（`kefu_faq_items` 有 `ON DELETE CASCADE`，热问随之删除），确保不留测试数据、不碰真实 demo。
- 运行：`cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_seed_kefu.py -v`。

## 验证

- 单测覆盖 create + 幂等 + 内容。
- 对当前真实库跑一次 `python seed_kefu.py` → 因 demo 已存在应打印「演示客服已存在，跳过」并返回 False（幂等实证）。
- 纯后端、无 UI 交互面 → 按 CLAUDE.md 不需 Playwright。

## 文档同步

- `docs/user-guide/ai/smart-customer-service.md` 第 2 节「前置条件」：注明 `init_db.py` 会自动 seed 一个 `演示客服`（`/kefu/demo`，已存在则跳过），也可 `python seed_kefu.py` 手动补种。
- `CLAUDE.md` 的 `init_db.py` 说明（“runs DDL and seeds default admin”）补一句：并 seed 一个演示客服实例。
