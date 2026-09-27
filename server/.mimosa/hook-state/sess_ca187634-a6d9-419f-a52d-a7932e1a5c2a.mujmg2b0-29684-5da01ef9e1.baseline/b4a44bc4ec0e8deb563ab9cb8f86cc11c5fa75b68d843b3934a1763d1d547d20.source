"""AgentRuntime 统一抽象（ai-harness-p2 spec §8.1）。

执行运行时的接口契约。实现方必须保证（Docker/Job 实现，Phase E）：
- 进程树可整体 kill（不留孤儿）；
- 网络默认拒绝，按 allowlist 放开；
- secret 临时注入不落 workspace；
- 退出后 attempt 必须收敛。
"""


class RuntimeCapabilityError(RuntimeError):
    """请求的能力当前 runtime 不支持。"""


class AgentRuntime:
    """执行运行时的统一抽象。上层执行引擎不得直接依赖 OpenCode 客户端。"""

    kind = 'abstract'

    def capabilities(self) -> dict:
        """支持能力声明：checkpoint / pause / network_isolation ..."""
        raise NotImplementedError

    def create_session(self, directory: str, title: str = '') -> str:
        raise NotImplementedError

    def dispatch(self, oc_session_id: str, prompt: str, *, directory: str = '',
                 agent: str = '', model: str = '') -> None:
        raise NotImplementedError

    def list_messages(self, oc_session_id: str, directory: str = '') -> list:
        raise NotImplementedError

    def get_messages(self, oc_session_id: str, directory: str = '') -> list:
        raise NotImplementedError

    def abort(self, oc_session_id: str, directory: str = '') -> None:
        raise NotImplementedError

    def health(self) -> dict:
        raise NotImplementedError
