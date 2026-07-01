"""公开 kefu 身份的 MCP 访问钳制。

设计修正：kefu 会话 bot 用户角色为 'kefu-guest'（公开匿名身份）。此前危险工具
只守卫字面量 'guest'，导致 kefu-guest 绕过。此模块集中定义只读/公开身份判定与
公开身份的工具白名单，供分发口（防护 a）与各工具守卫（防护 b）共用。"""

PUBLIC_KEFU_ROLE = "kefu-guest"
READONLY_ROLES = frozenset({"guest", "kefu-guest"})
# 公开匿名客服只允许的只读工具：数据查询 + 会话级上传读取。
KEFU_TOOL_ALLOWLIST = frozenset({"query_collection", "list_collections", "read_upload"})


def is_readonly(role: str) -> bool:
    return role in READONLY_ROLES


def is_public_kefu(role: str) -> bool:
    return role == PUBLIC_KEFU_ROLE


def tool_allowed(name: str, role: str) -> bool:
    """公开 kefu 身份仅允许白名单工具；其余身份不受此层限制。"""
    if is_public_kefu(role):
        return name in KEFU_TOOL_ALLOWLIST
    return True
