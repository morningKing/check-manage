"""会话→OC 栈路由注册表（OC2 适配阶段一）。

ai_chat_sessions.oc_runtime（'v1'|'v2'，迁移默认 'v1'）决定会话绑定哪套
OpenCode 运行时栈。fail-closed 原则：行缺失 / 列 NULL / 未知值 / DB 异常
一律回落 'v1'——存量会话与异常场景下 V1 行为逐字节等价，绝不静默切栈。

既有 ai.runtime.get_runtime() 语义保持不变（默认栈 V1 单例）；本模块
只服务双栈路由消费方（facade / 路由层接线，后续提交接入）。
"""
import logging
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RUNTIMES = {}  # kind ('v1'|'v2') -> AgentRuntime 实例缓存

_KINDS = ('v1', 'v2')


def _sanitize(kind) -> str:
    return kind if kind in _KINDS else 'v1'


def session_runtime_kind(platform_sid: str) -> str:
    """平台会话 id → 绑定栈（'v1'|'v2'）。任何异常/缺失 fail-closed 'v1'。"""
    if not platform_sid:
        return 'v1'
    try:
        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT oc_runtime FROM ai_chat_sessions WHERE id = %s",
                        (platform_sid,))
            row = cur.fetchone()
    except Exception as e:  # noqa: BLE001
        logger.warning('oc_runtime lookup failed sid=%s: %s', platform_sid, e)
        return 'v1'
    return _sanitize(((row[0] if row else '') or 'v1').strip().lower())


def runtime_for(kind: str):
    """按栈取缓存的 AgentRuntime 实例（懒构造，双检锁防多实例脑裂——
    与 ai.runtime.get_runtime() 同一纪律）。未知 kind 抛
    RuntimeCapabilityError（显式误配必须炸出来，不静默）。"""
    kind = (kind or 'v1').strip().lower()
    if kind not in _KINDS:
        from ai.runtime.base import RuntimeCapabilityError
        raise RuntimeCapabilityError(f'unknown oc runtime kind: {kind!r}')
    rt = _RUNTIMES.get(kind)
    if rt is not None:
        return rt
    with _LOCK:
        rt = _RUNTIMES.get(kind)
        if rt is None:
            if kind == 'v1':
                from ai.runtime.opencode_local import OpenCodeLocalRuntime
                rt = OpenCodeLocalRuntime()
            else:
                from ai.runtime.opencode_v2_local import OpenCodeV2LocalRuntime
                rt = OpenCodeV2LocalRuntime()
            _RUNTIMES[kind] = rt
    return rt


def client_for_session(platform_sid: str):
    """平台会话 id → 该会话绑定栈的底层 client。"""
    return runtime_for(session_runtime_kind(platform_sid)).get_client()


def default_kind() -> str:
    """新会话默认落栈（AI_OC_RUNTIME_DEFAULT 灰度开关；非法值 fail-closed
    'v1'——与 session_runtime_kind 同一防呆纪律）。"""
    from config import AI_OC_RUNTIME_DEFAULT
    return AI_OC_RUNTIME_DEFAULT if AI_OC_RUNTIME_DEFAULT in _KINDS else 'v1'


def client_for_default():
    """默认栈的 client——无会话上下文的全局端点用（模型/agent 目录等）。"""
    return runtime_for(default_kind()).get_client()


def runtime_kind_by_oc_id(oc_session_id: str) -> str:
    """OC 会话 id → 绑定栈；查不到行/异常返回 ''（区别于 session_runtime_kind
    的 fail-closed v1）——供 batch facade 的兜底链判断：查到绑定才路由，
    查不到走原有 get_runtime() 链（测试打桩点保持有效）。"""
    if not oc_session_id:
        return ''
    try:
        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT oc_runtime FROM ai_chat_sessions "
                        "WHERE opencode_session_id = %s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (oc_session_id,))
            row = cur.fetchone()
    except Exception as e:  # noqa: BLE001
        logger.warning('oc_runtime lookup by oc id failed sid=%s: %s',
                       oc_session_id, e)
        return ''
    kind = ((row[0] if row else '') or '').strip().lower()
    return kind if kind in _KINDS else ''


def client_for_oc_id(oc_session_id: str):
    """OC 会话 id（opencode_session_id）→ 绑定栈 client。

    供 batch facade 等只拿得到 OC id 的调用点路由；查不到行/异常
    fail-closed 'v1'。同 OC id 理论上唯一映射一行，ORDER BY 防御重复。"""
    if not oc_session_id:
        return runtime_for('v1').get_client()
    try:
        from db import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT oc_runtime FROM ai_chat_sessions "
                        "WHERE opencode_session_id = %s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (oc_session_id,))
            row = cur.fetchone()
    except Exception as e:  # noqa: BLE001
        logger.warning('oc_runtime lookup by oc id failed sid=%s: %s',
                       oc_session_id, e)
        row = None
    kind = _sanitize(((row[0] if row else '') or 'v1').strip().lower())
    return runtime_for(kind).get_client()
