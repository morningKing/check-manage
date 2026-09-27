"""Runtime Adapter（ai-harness-p2 spec §8）。

上层执行引擎不得直接依赖 OpenCode 客户端——统一经 AgentRuntime 抽象。
P2 首发只提供 OpenCodeLocalRuntime（包装既有 opencode_client，行为保持
byte-for-byte 兼容）；Docker / Windows Job / K8s 适配器按环境启用（后续
Phase E，接口已预留 capabilities()）。
"""
from utils.runtime.base import AgentRuntime, RuntimeCapabilityError
from utils.runtime.opencode_local import OpenCodeLocalRuntime

__all__ = ['AgentRuntime', 'RuntimeCapabilityError', 'OpenCodeLocalRuntime',
           'get_runtime']


_default: AgentRuntime | None = None


def get_runtime() -> AgentRuntime:
    """默认 runtime（env AI_AGENT_RUNTIME 可切换；目前仅 opencode_local）。"""
    global _default
    if _default is None:
        import os
        kind = os.getenv('AI_AGENT_RUNTIME', 'opencode_local')
        if kind != 'opencode_local':
            raise RuntimeCapabilityError(
                f'runtime {kind} 尚未在此环境启用（Phase E 按环境开启）')
        _default = OpenCodeLocalRuntime()
    return _default
