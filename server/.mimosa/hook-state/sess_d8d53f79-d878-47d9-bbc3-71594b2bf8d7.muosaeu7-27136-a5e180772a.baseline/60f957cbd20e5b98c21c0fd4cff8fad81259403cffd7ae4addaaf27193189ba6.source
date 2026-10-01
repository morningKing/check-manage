"""Parse ``@<path>`` file mentions out of a chat message and inline them for the agent.

Agent mentions (``@<name>``, validated against the agent list and sent to the
route structured as ``agentMentions``) are handled elsewhere; this module deals
with *file* mentions — an ``@``-token that refers to a real file inside the
session workspace (``@uploads/data.csv``, ``@outputs/report.json`` …).

Two responsibilities:

* :func:`find_file_mentions` — pure regex/string parsing: candidate relative
  paths in order, de-duplicated, with agent names and bare words filtered out.
* :func:`inline_file_mentions` — resolves those candidates against the
  workspace (traversal-safe) and builds the prompt suffix: utf-8 text files are
  inlined in a fenced block; binary / oversized files that exist produce a short
  pointer telling the agent to read them with its tools; missing or escaping
  paths are skipped silently (the ``@`` marker stays in the message as-is).
"""
import os
import re

from utils.workspace import safe_resolve

# ``@`` must follow start-of-string or whitespace (so ``a@b.com`` emails don't
# match); the token body allows word chars, path separators, dots and CJK.
_MENTION_RE = re.compile(r'(?:^|\s)@([A-Za-z0-9_./\-\u4e00-\u9fa5]+)')
MAX_MENTIONED_FILES = 10
_MAX_INLINE_BYTES = 200_000


def find_file_mentions(text, agent_names=(), already_attached=()):
    """Return de-duped, order-preserving candidate relative paths from ``@path``.

    ``agent_names`` are skipped (structured agent mentions); tokens with neither
    ``/`` nor ``.`` are bare words (agent/command names), not files; tokens in
    ``already_attached`` are skipped so attachments aren't inlined twice.
    """
    agents = {a for a in (agent_names or ()) if a}
    attached = {a for a in (already_attached or ()) if a}
    seen: set[str] = set()
    out: list[str] = []
    for m in _MENTION_RE.finditer(text or ''):
        tok = m.group(1)
        if tok in agents:
            continue
        # A bare token (no separator/extension) is an agent/command, not a path.
        if '/' not in tok and '.' not in tok:
            continue
        stripped = tok.rstrip('.')  # tolerate "@file." at end of a sentence
        tok = stripped or tok
        if not tok or tok in attached or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= MAX_MENTIONED_FILES:
            break
    return out


def _read_text_if_inlineable(abs_path: str, max_bytes: int):
    """Return decoded utf-8 text, or None if missing/binary/oversized."""
    try:
        if os.path.getsize(abs_path) > max_bytes:
            return None
        with open(abs_path, 'rb') as f:
            return f.read().decode('utf-8')
    except (UnicodeDecodeError, OSError):
        return None


def inline_file_mentions(workspace_path, content, agent_names=(),
                         already_attached=(), max_bytes=_MAX_INLINE_BYTES):
    """Build the prompt suffix that inlines files referenced via ``@<path>``.

    Text files (utf-8, ≤ ``max_bytes``) are inlined in a fenced block; binary or
    oversized files that exist produce a short pointer (absolute path included)
    telling the agent to read them with its tools. Missing / traversal /
    directory tokens are skipped silently. Returns '' when nothing applies.
    """
    if not workspace_path or not content:
        return ''
    blocks = []
    for rel in find_file_mentions(
        content, agent_names=agent_names, already_attached=already_attached
    ):
        try:
            abs_path = safe_resolve(workspace_path, rel)
        except Exception:
            continue  # escapes workspace (or otherwise unresolvable) → skip
        if not os.path.isfile(abs_path):
            continue
        text = _read_text_if_inlineable(abs_path, max_bytes)
        if text is not None:
            blocks.append(
                f"\n\n[用户用 @ 引用的文件 {rel}，内容如下]\n```\n{text}\n```"
            )
        else:
            blocks.append(
                f"\n\n[用户用 @ 引用的文件 {rel} 为二进制或超过内联大小限制，"
                f"如需请直接用工具读取，工作区绝对路径：{abs_path}]"
            )
    return ''.join(blocks)
