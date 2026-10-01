"""OpenCode 本地 Runtime（P2 首发默认实现，spec §8.2）。

包装既有 opencode_client（utils/opencode_client.py），语义与 batch_engine
的 _OpenCodeFacade 完全一致——引擎上层经 AgentRuntime 抽象访问，测试打桩
点（batch_engine.opencode_client 模块名）保持不变。
"""
from __future__ import annotations

import requests

from utils.runtime.base import AgentRuntime


class OpenCodeLocalRuntime(AgentRuntime):
    kind = 'opencode_local'

    def capabilities(self) -> dict:
        return {'checkpoint': True, 'pause': True, 'abort': True,
                'network_isolation': False, 'process_tree_kill': False}

    def _client(self):
        from utils.opencode_client import OpenCodeClient
        from config import OPENCODE_BASE_URL
        return OpenCodeClient(OPENCODE_BASE_URL)

    def create_session(self, directory: str, title: str = '') -> str:
        return self._client().create_session(directory=directory, title=title)

    def dispatch(self, oc_session_id: str, prompt: str, *, directory: str = '',
                 agent: str = '', model: str = '') -> None:
        from config import get_default_chat_model
        self._client().send_prompt_async(
            oc_session_id, prompt,
            model=model or get_default_chat_model(),
            directory=directory, agent=agent)

    def list_messages(self, oc_session_id: str, directory: str = '') -> list:
        """batch_engine._OpenCodeFacade.list_messages 的完成判定映射在同处
        （facade 保留，runtime 只做透传，避免两份判定逻辑漂移）。"""
        try:
            return self._client().get_messages(oc_session_id,
                                               directory=directory) or []
        except requests.RequestException:
            return []

    def get_messages(self, oc_session_id: str, directory: str = '') -> list:
        return self._client().get_messages(oc_session_id,
                                           directory=directory) or []

    def abort(self, oc_session_id: str, directory: str = '') -> None:
        self._client().abort_session(oc_session_id, directory=directory)

    def health(self) -> dict:
        try:
            self._client().list_agents()
            return {'ok': True}
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': str(e)[:200]}
