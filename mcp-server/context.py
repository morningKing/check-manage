"""ToolContext: per-call user identity passed to each tool handler."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    user_id: str
    role: str


def context_from_token(token: str) -> ToolContext:
    from auth import validate_session_token
    d = validate_session_token(token)
    return ToolContext(
        session_id=d["session_id"],
        user_id=d["user_id"],
        role=d["role"],
    )
