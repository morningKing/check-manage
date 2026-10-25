# -*- coding: utf-8 -*-
"""工具访问钳制：角色×工具矩阵（DB）+ 代码默认/分层回落（spec §5.2）。

矩阵表 ai_mcp_tool_rules（全量行）由管理端 PUT 全量替换；本模块 TTL 3s
缓存读取。行缺失（新工具上线）→ 代码默认：admin/developer 放行，其余拒绝。
DB 读取失败 → 分层回落：kefu-guest 用代码白名单（fail-closed，公开身份
最后防线），guest 用 manifest 的 read 工具集，其余角色放行（fail-open，
DB 抖动不打瘫核心会话）。白名单同时是矩阵种子的 kefu 默认值。"""
import time

PUBLIC_KEFU_ROLE = "kefu-guest"
READONLY_ROLES = frozenset({"guest", "kefu-guest"})
# 公开匿名客服只允许的只读工具：数据查询 + 会话级上传读取。
KEFU_TOOL_ALLOWLIST = frozenset({"query_collection", "list_collections", "read_upload"})

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
