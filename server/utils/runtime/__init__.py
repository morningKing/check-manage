"""Runtime Adapter（ai-harness-p2 spec §8）。

上层执行引擎不得直接依赖 OpenCode 客户端——统一经 AgentRuntime 抽象。
P2 首发只提供 OpenCodeLocalRuntime（包装既有 opencode_client，行为保持
byte-for-byte 兼容）；Docker / Windows Job / K8s 适配器按环境启用（后续
Phase E，接口已预留 capabilities()）。
"""
import threading

from utils.runtime.base import AgentRuntime, RuntimeCapabilityError
from utils.runtime.opencode_local import OpenCodeLocalRuntime

__all__ = ['AgentRuntime', 'RuntimeCapabilityError', 'OpenCodeLocalRuntime',
           'get_runtime']


_default: AgentRuntime | None = None
_default_lock = threading.Lock()


def get_runtime() -> AgentRuntime:
    """默认 runtime（env AI_AGENT_RUNTIME 可切换；目前仅 opencode_local）。

    懒初始化用双检锁：外层无锁快路径（已初始化时零开销），锁内重查
    `_default`。无锁时多线程首调会竞态各构造一个实例——stub 是有状态
    单例，两个实例即两份内存会话字典脑裂（A 实例 create_session、
    B 实例 get_messages → KeyError，子任务被误判 failed）。
    """
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                import os
                kind = os.getenv('AI_AGENT_RUNTIME', 'opencode_local')
                if kind == 'stub':
                    # 防呆（压测终审 M8）：stub 会让所有批任务"静默成功"，
                    # 误配到生产等于 AI 全体放假——必须显式放行。
                    if os.getenv('AI_STUB_ALLOW', '') != '1':
                        raise RuntimeCapabilityError(
                            'AI_AGENT_RUNTIME=stub 需要显式 AI_STUB_ALLOW=1 '
                            '（防误配：stub 会假成功所有 agent 任务）')
                    from utils.runtime.stub import StubRuntime
                    _default = StubRuntime()      # profile 由 AI_STUB_PROFILE 注入
                elif kind != 'opencode_local':
                    raise RuntimeCapabilityError(
                        f'runtime {kind} 尚未在此环境启用（Phase E 按环境开启）')
                else:
                    _default = OpenCodeLocalRuntime()
    return _default
