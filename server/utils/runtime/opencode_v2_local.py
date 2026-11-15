"""OpenCode 2.x 本地 Runtime（OC2 适配阶段一）。

与 OpenCodeLocalRuntime 同形：方法签名、返回形状完全一致，底层换成
OpenCodeClientV2（/api/* 契约 + V2→V1 事件桥），base URL 走
OPENCODE2_BASE_URL 独立栈。上层经 registry.runtime_for('v2') 取用。

事件桥（subscribe_events → V1 形态帧）在 client_v2 内部完成——本类与
opencode_local 一样只做透传，语义与 batch_engine._OpenCodeFacade 一致。
"""
from __future__ import annotations

import requests

from utils.runtime.base import AgentRuntime


class OpenCodeV2LocalRuntime(AgentRuntime):
    kind = 'opencode_v2_local'

    def capabilities(self) -> dict:
        return {'checkpoint': True, 'pause': True, 'abort': True,
                'revert': True,
                'network_isolation': False, 'process_tree_kill': False}

    def _client(self):
        from utils.opencode_client_v2 import OpenCodeClientV2
        from config import OPENCODE2_BASE_URL
        from utils.opencode_launch import oc2_known_password
        return OpenCodeClientV2(OPENCODE2_BASE_URL,
                                password=oc2_known_password())

    def get_client(self):
        """底层 OpenCodeClientV2 公开取用点（与 v1 runtime.get_client 同形）。"""
        return self._client()

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
        """透传（client_v2.get_messages 已把 V2 投影消息翻译成 V1 {info,parts}
        形状）；完成判定映射仍在 batch facade，保持单份判定逻辑。"""
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

    def revert_stage(self, oc_session_id: str, oc_message_id: str,
                     directory: str = '') -> dict:
        return self._client().revert_stage(oc_session_id, oc_message_id,
                                           directory=directory)

    def revert_clear(self, oc_session_id: str, directory: str = '') -> None:
        self._client().revert_clear(oc_session_id, directory=directory)

    def health(self) -> dict:
        try:
            self._client().list_agents()
            return {'ok': True}
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': str(e)[:200]}
