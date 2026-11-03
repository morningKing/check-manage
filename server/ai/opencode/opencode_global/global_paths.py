"""Management layer for OpenCode's GLOBAL skill/agent files.

The admin「OpenCode 运行时」page edits the files that `opencode serve` loads
from its global config directory at startup:

    <OPENCODE_GLOBAL_DIR>/skill/<name>/SKILL.md     (skills)
    <OPENCODE_GLOBAL_DIR>/agent/<name>.md           (agents)

Facts verified against OpenCode v1.15.1 that shape this module:
- There is NO CRUD HTTP API and NO hot reload: file writes are invisible to a
  running serve, and PATCH /global/config does not reload either (spike-tested
  2026-09-13). A serve restart is the ONLY way changes take effect, so every
  entry carries a `runtime` status derived by diffing the files against the
  live GET /agent and GET /skill responses.
- Agent frontmatter allows name/mode/description/model/temperature/top_p/
  disable (+ others); the markdown BODY is the agent's prompt. `disable: true`
  on a file that shadows a built-in agent is how built-ins get turned off.
- Skill name must match its directory name; SKILL.md without a `description`
  is silently filtered by OpenCode, so writes enforce a non-empty one.
- `GET /skill` items carry `location` (absolute SKILL.md path or `<built-in>`)
  which is the authoritative "is my file loaded" signal; `GET /agent` has no
  path field, so agents are matched by name.

Security: everything stays inside OPENCODE_GLOBAL_DIR (resolve_under), names
pass a strict charset regex (no separators, no leading dot), writes are atomic
(temp file + os.replace), and the serve restart kills strictly by the port
OPENCODE_BASE_URL listens on.
"""

import os

import config
from config import OPENCODE_GLOBAL_DIR

from .global_guard import resolve_under, validate_name

# ---------------------------------------------------------------------------
# Paths & names
# ---------------------------------------------------------------------------

def global_dir() -> str:
    """The OpenCode global config directory. Read through `config` at call
    time (not import time) so tests can monkeypatch config.OPENCODE_GLOBAL_DIR."""
    return config.OPENCODE_GLOBAL_DIR


def skills_dir() -> str:
    """主技能目录（OpenCode 官方文档的 skill/，单数）。新建技能落在这里。"""
    return os.path.join(global_dir(), 'skill')


def skill_roots() -> list[str]:
    """受管技能根目录列表：skill/（单数，主）+ skills/（复数）。

    OpenCode 实际会同时扫描全局配置区里的两个目录（v1.2 实测），机器上的
    技能常装在复数的 skills/ 里。两个根都在 OPENCODE 全局配置区内（受控
    目录，Spec §13），因此都可在线编辑/上传覆盖/删除；配置区之外的技能
    （如 .cache 里的插件技能）仍只读。
    """
    roots = [skills_dir(), os.path.join(global_dir(), 'skills')]
    out: list[str] = []
    seen: set[str] = set()
    for r in roots:
        rp = os.path.realpath(os.path.abspath(r))
        if rp not in seen:
            seen.add(rp)
            out.append(r)
    return out


def find_skill_dir(name: str) -> str | None:
    """按名字在受管根目录里找已存在的技能目录；主目录优先。"""
    name = validate_name(name)
    for root in skill_roots():
        d = resolve_under(root, name)
        if os.path.isdir(d):
            return d
    return None


def agents_dir() -> str:
    return os.path.join(global_dir(), 'agent')

__all__ = [
    'OPENCODE_GLOBAL_DIR',
    'agents_dir',
    'find_skill_dir',
    'global_dir',
    'skill_roots',
    'skills_dir',
]
