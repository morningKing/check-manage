# -*- coding: utf-8 -*-
"""工具访问钳制：角色×工具矩阵（DB）+ 代码默认/分层回落（spec §5.2）。

矩阵表 ai_mcp_tool_rules（全量行）由管理端 PUT 全量替换；本模块 TTL 3s
缓存读取。行缺失（新工具上线）→ 代码默认：admin/developer 放行，其余拒绝。
DB 读取失败 → 分层回落：kefu-guest 用代码白名单（fail-closed，公开身份
最后防线），guest 用 manifest 的 read 工具集，其余角色放行（fail-open，
DB 抖动不打瘫核心会话）。白名单同时是矩阵种子的 kefu 默认值。"""
import os
import time

PUBLIC_KEFU_ROLE = "kefu-guest"
READONLY_ROLES = frozenset({"guest", "kefu-guest"})
# 公开匿名客服只允许的只读工具：数据查询 + 会话级上传读取。
KEFU_TOOL_ALLOWLIST = frozenset({"query_collection", "list_collections", "read_upload"})

# 模型自主登记门禁（入口 C）：默认关闭，且批任务子会话永久禁用。
# 原因：该工具让模型在无人值守的批任务里给自己加期望——批配置与策略库都
# 看不到，终态却按它核对，表现为「莫名其妙多出来的门禁」。交互会话如确需
# 使用，显式设置 MCP_ENABLE_REGISTER_ACTION_CHECK=1；批子会话即使打开也不放行。
REGISTER_ACTION_CHECK_TOOL = "register_action_check"
_REGISTER_ACTION_CHECK_ENV = "MCP_ENABLE_REGISTER_ACTION_CHECK"

# 与 server/init_db.py 内置角色种子对齐；动态自建角色不进矩阵（spec §6）
ROLES = ("admin", "developer", "guest", "kefu-guest")

RULES_TTL_SECONDS = 3.0

_rules_cache = None        # (role, tool_name) -> allowed
_rules_cached_at = 0.0


def is_readonly(role: str) -> bool:
    return role in READONLY_ROLES


def is_public_kefu(role: str) -> bool:
    return role == PUBLIC_KEFU_ROLE


def _fetch_rules() -> dict:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT role, tool_name, allowed FROM ai_mcp_tool_rules")
            return {(r, t): a for r, t, a in cur.fetchall()}


def _reset_cache() -> None:
    """测试与手动失效入口：清缓存，下次调用重读 DB。"""
    global _rules_cache, _rules_cached_at
    _rules_cache = None
    _rules_cached_at = 0.0


def _rules():
    """矩阵快照；DB 失败返回 None（调用方走分层回落）。TTL 3s。"""
    global _rules_cache, _rules_cached_at
    now = time.monotonic()
    if _rules_cache is None or now - _rules_cached_at > RULES_TTL_SECONDS:
        try:
            _rules_cache = _fetch_rules()
        except Exception:
            _rules_cache = None
        _rules_cached_at = now
    return _rules_cache


def _fallback_allowed(name: str, role: str) -> bool:
    """DB 失败时的分层回落（spec §5.2）。"""
    if is_public_kefu(role):
        return name in KEFU_TOOL_ALLOWLIST
    if role == "guest":
        from tools.registry import meta_of
        m = meta_of(name)
        return bool(m) and m[1] == "read"
    # admin/developer/未知角色：fail-open
    return True


def tool_allowed(name: str, role: str) -> bool:
    rules = _rules()
    if rules is None:                       # DB 失败 → 分层回落
        return _fallback_allowed(name, role)
    hit = rules.get((role, name))
    if hit is not None:
        return bool(hit)
    # 行缺失（新工具/行被删）→ 代码默认：受限身份一律拒绝（spec §4.3）
    return role in ("admin", "developer")


def register_action_check_enabled() -> bool:
    """模型自主登记门禁的全局开关（默认关）。"""
    return os.getenv(_REGISTER_ACTION_CHECK_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def _session_in_batch(session_id: str) -> bool:
    """该 MCP 会话是否是批任务子会话（ai_chat_sessions.batch_id 非空）。

    fail-closed：查库异常按批会话处理——宁可交互会话少一个工具，也不让
    无人值守的批任务重新获得自登记路径。"""
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT batch_id FROM ai_chat_sessions WHERE id = %s",
                    (session_id,))
                row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        return True


def tool_globally_available(name: str) -> bool:
    """不依赖会话身份的工具可用性（启动快照 / list_tools 兜底用）。

    只处理全局下架的工具；角色矩阵与批会话限制由 tool_allowed_for_context
    负责。"""
    if name == REGISTER_ACTION_CHECK_TOOL:
        return register_action_check_enabled()
    return True


def tool_allowed_for_context(name: str, ctx) -> bool:
    """角色矩阵 + 会话维度的工具准入（list_tools 与 call_tool 共用）。

    比 tool_allowed 多两条仅针对 register_action_check 的硬规则：
    1. 全局开关默认关（模型不能自动登记门禁）；
    2. 批任务子会话永久禁用（即使全局开关打开）——批任务的门禁只应来自
       批配置/策略/模板/管理员补挂，不能由执行中的模型自加。"""
    if not tool_allowed(name, ctx.role):
        return False
    if name == REGISTER_ACTION_CHECK_TOOL:
        if not register_action_check_enabled():
            return False
        if _session_in_batch(getattr(ctx, "session_id", "")):
            return False
    return True
