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

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests

import config
from config import OPENCODE_GLOBAL_DIR

logger = logging.getLogger(__name__)

# Wide enough for platform-library skill names (which allow uppercase, `_`)
# yet safe: no path separators, no leading dot, so `..` can never form.
_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
_WIN_RESERVED = {'con', 'prn', 'aux', 'nul'} | {f'com{i}' for i in range(1, 10)} \
    | {f'lpt{i}' for i in range(1, 10)}

SKILL_NAME_MISMATCH_HELP = (
    'OpenCode 要求 SKILL.md frontmatter 的 name 与目录名一致，请保持两者相同')


class OpenCodeGlobalError(Exception):
    """User-facing error (message is safe to return as-is)."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


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


def validate_name(name: str) -> str:
    name = (name or '').strip()
    if not _NAME_RE.match(name):
        raise OpenCodeGlobalError(
            'BAD_NAME',
            '名称仅允许字母、数字、点、下划线、连字符，且以字母或数字开头（≤64 字符）')
    if name.lower() in _WIN_RESERVED:
        raise OpenCodeGlobalError('BAD_NAME', '该名称是 Windows 保留设备名，不允许使用')
    return name


def resolve_under(base: str, *parts: str) -> str:
    """Join and refuse any result that escapes `base` (traversal guard).

    Hardened per Spec §13: the containment check runs on REAL paths — a
    symlinked component that points outside `base` (symlink escape) is rejected
    exactly like a literal `..`. Absolute paths can't smuggle through either:
    os.path.join discards the base when a later part is absolute, and the
    resulting path then fails the prefix check."""
    base_abs = os.path.realpath(os.path.abspath(base))
    final = os.path.realpath(os.path.abspath(os.path.join(base, *parts)))
    if final != base_abs and not final.startswith(base_abs + os.sep):
        raise OpenCodeGlobalError('PATH_UNSAFE', '路径越界')
    return final


def _reject_symlink_components(base: str, *parts: str) -> None:
    """Refuse managed paths that traverse a symlink below `base`.

    os.walk-following readers and writers must not be able to reach outside the
    controlled dir via a link dropped into the global dir (Spec §13 禁止软链接
    越界). `base` itself may legitimately be a link (deployment layouts); only
    components introduced *under* it are attacker-controllable.
    """
    current = os.path.realpath(os.path.abspath(base))
    for part in parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise OpenCodeGlobalError(
                'PATH_UNSAFE', '受控目录内不允许软链接')


# Managed-content size caps (Spec §13 限制单文件和总目录大小). The zip path is
# already capped by utils.skill_upload (5 MiB archive / 200 entries); these cap
# the raw-editor routes, which bypass zip extraction entirely.
MAX_MANAGED_FILE_BYTES = 1024 * 1024
MAX_MANAGED_TOTAL_BYTES = 64 * 1024 * 1024


def _check_content_size(content: str) -> None:
    encoded = content.encode('utf-8')
    if len(encoded) > MAX_MANAGED_FILE_BYTES:
        raise OpenCodeGlobalError(
            'FILE_TOO_LARGE',
            f'内容超过 {MAX_MANAGED_FILE_BYTES // 1024} KB 上限', 413)


def _check_dir_size(dir_path: str, extra_bytes: int = 0) -> None:
    total = extra_bytes
    for dirpath, _dirnames, filenames in os.walk(dir_path):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                continue
            if total > MAX_MANAGED_TOTAL_BYTES:
                raise OpenCodeGlobalError(
                    'DIR_TOO_LARGE',
                    f'受控目录总量超过 {MAX_MANAGED_TOTAL_BYTES // (1024 * 1024)} MB 上限', 413)


def _atomic_write(path: str, data: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.tmp-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---------------------------------------------------------------------------
# Frontmatter (simple scalar kv — matches what this module writes; complex
# frontmatter written by hand keeps round-tripping via the raw content)
# ---------------------------------------------------------------------------

_FM_SPLIT_RE = re.compile(r'^---\s*$')


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return ({scalar metadata}, body) for a `---` frontmatter document.
    Non-scalar lines (lists, nested maps) are skipped, not errors."""
    lines = text.replace('\r\n', '\n').split('\n')
    if not lines or not _FM_SPLIT_RE.match(lines[0]):
        return {}, text
    meta: dict = {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if _FM_SPLIT_RE.match(line):
            end = i
            break
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$', line)
        if not m:
            continue
        raw = m.group(2).strip()
        if raw.startswith(('[', '{', '*', '&', '|', '>')):
            continue  # complex value — leave it to raw editing
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
            raw = raw[1:-1]
        elif raw.lower() in ('true', 'false'):
            raw = raw.lower() == 'true'
        else:
            try:
                raw = int(raw)
            except ValueError:
                try:
                    raw = float(raw)
                except ValueError:
                    pass
        meta[m.group(1)] = raw
    body = '\n'.join(lines[end + 1:]) if end is not None else text
    return meta, body


def serialize_frontmatter(meta: dict, body: str) -> str:
    """Render `---` frontmatter + body. String values are JSON-quoted so
    descriptions with colons/quotes survive; bool/int/float stay bare."""

    def _val(v) -> str:
        if isinstance(v, bool):
            return 'true' if v else 'false'
        if isinstance(v, (int, float)):
            return str(v)
        import json as _json
        return _json.dumps(str(v), ensure_ascii=False)

    lines = ['---']
    lines += [f'{k}: {_val(v)}' for k, v in meta.items() if v is not None]
    lines += ['---', body.rstrip('\n'), '']
    return '\n'.join(lines)


def _read_text(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


# ---------------------------------------------------------------------------
# Skills (global dir): <skills_dir>/<name>/SKILL.md
# ---------------------------------------------------------------------------

def _skill_dir(name: str) -> str:
    """技能目录：已存在则返回实际所在根（主目录优先），新建回落主目录。"""
    return find_skill_dir(name) or resolve_under(skills_dir(), name)


def _skill_md_path(name: str) -> str:
    return resolve_under(_skill_dir(name), 'SKILL.md')


def list_skill_files() -> list[dict]:
    """Scan all managed roots' */SKILL.md → [{name, description, size, mtime, path}].

    同名技能主目录优先（复数 skills/ 下的同名目录会被遮蔽，不重复列出）。"""
    items: list[dict] = []
    by_name: dict[str, dict] = {}
    for root in skill_roots():
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            if entry in by_name:
                continue  # 主目录优先，遮蔽后续根里的同名技能
            md = os.path.join(root, entry, 'SKILL.md')
            if not os.path.isfile(md):
                continue
            try:
                meta, _body = parse_frontmatter(_read_text(md))
            except OSError:
                continue
            st = os.stat(md)
            by_name[entry] = {
                'name': entry,
                'frontmatterName': meta.get('name') or '',
                'description': str(meta.get('description') or ''),
                'size': st.st_size,
                'mtime': int(st.st_mtime),
                'path': md,
            }
    items = sorted(by_name.values(), key=lambda it: it['name'])
    return items


def read_skill(name: str) -> dict:
    name = validate_name(name)
    d = find_skill_dir(name)
    if not d:
        raise OpenCodeGlobalError('NOT_FOUND', f'全局 skill「{name}」不存在', 404)
    _reject_symlink_components(d, 'SKILL.md')
    md = os.path.join(d, 'SKILL.md')
    content = _read_text(md)
    meta, body = parse_frontmatter(content)
    st = os.stat(md)
    return {'name': name, 'description': str(meta.get('description') or ''),
            'body': body, 'content': content,
            'size': st.st_size, 'mtime': int(st.st_mtime)}


def write_skill(name: str, description: str | None = None, body: str | None = None,
                content: str | None = None) -> dict:
    """Create/update a skill. Either `{description, body}` (structured — the
    frontmatter is regenerated) or full `content` (raw round-trip)."""
    name = validate_name(name)
    if content is None:
        description = (description or '').strip()
        if not description:
            raise OpenCodeGlobalError(
                'DESCRIPTION_REQUIRED', 'description 必填：OpenCode 会过滤没有描述的 skill')
        content = serialize_frontmatter(
            {'name': name, 'description': description}, body or '')
    else:
        meta, _ = parse_frontmatter(content)
        fm_name = str(meta.get('name') or '').strip()
        if fm_name and fm_name != name:
            raise OpenCodeGlobalError('NAME_MISMATCH', SKILL_NAME_MISMATCH_HELP, 400)
        if not str(meta.get('description') or '').strip():
            raise OpenCodeGlobalError(
                'DESCRIPTION_REQUIRED', 'description 必填：OpenCode 会过滤没有描述的 skill')
    _check_content_size(content)
    target_dir = _skill_dir(name)
    _check_dir_size(target_dir, extra_bytes=len(content.encode('utf-8')))
    _reject_symlink_components(target_dir, 'SKILL.md')
    md = os.path.join(target_dir, 'SKILL.md')
    if os.path.isfile(md) and _read_text(md) == content:
        return {'name': name, 'changed': False}
    _atomic_write(md, content)
    return {'name': name, 'changed': True}


def delete_skill(name: str) -> None:
    name = validate_name(name)
    d = find_skill_dir(name)
    if not d:
        raise OpenCodeGlobalError('NOT_FOUND', f'全局 skill「{name}」不存在', 404)
    shutil.rmtree(d)


def install_skill_zip_bytes(data: bytes, zip_filename: str,
                            overwrite: bool = False) -> str:
    """Install an uploaded skill zip into the global skill dir.

    Reuses utils.skill_upload.extract_skill_zip wholesale — same validation
    contract (5 MiB / 200 entries / SKILL.md / name charset / zip-slip guard /
    single-top-level-dir strip / name fallback to the zip filename) — by
    extracting into a throwaway workspace and moving the produced
    `<tmp>/.opencode/skills/<name>` into the global skill dir.

    `overwrite=True`（上传覆盖）: 同名技能（任一受管根目录）先整体移除再安装，
    实现"上传即替换"；默认 False 维持同名 409。
    """
    import io
    import tempfile

    from utils.skill_upload import SkillUploadError, extract_skill_zip

    fs = _make_file_storage(data, zip_filename)
    _check_dir_size(skills_dir(), extra_bytes=len(data))
    tmp_ws = tempfile.mkdtemp(prefix='ocglobal-')
    try:
        info = extract_skill_zip(tmp_ws, fs)  # raises SkillUploadError
        name = info['name']
        src = os.path.join(tmp_ws, '.opencode', 'skills', name)
        existing = find_skill_dir(name)
        if existing and not overwrite:
            raise OpenCodeGlobalError(
                'ALREADY_EXISTS', f'全局 skill「{name}」已存在', 409)
        dest = resolve_under(skills_dir(), name)
        if existing:
            # 覆盖：移除旧目录（可能在复数 skills/ 根下），统一装入主目录
            shutil.rmtree(existing)
        os.makedirs(skills_dir(), exist_ok=True)
        shutil.move(src, dest)
        return name
    except SkillUploadError as e:
        raise OpenCodeGlobalError(e.code, e.message, 400)
    finally:
        shutil.rmtree(tmp_ws, ignore_errors=True)


def _make_file_storage(data: bytes, filename: str):
    import io
    from werkzeug.datastructures import FileStorage
    return FileStorage(stream=io.BytesIO(data),
                       filename=filename or 'skill.zip')


def _tree_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                continue
    return total


def publish_platform_skill(platform_skill_dir: str, name: str,
                           overwrite: bool = False) -> str:
    """Copy a platform global-skill directory (utils.global_skills storage)
    into the OpenCode global skill dir. Returns the installed name."""
    name = validate_name(name)
    src_md = os.path.join(platform_skill_dir, 'SKILL.md')
    if not os.path.isfile(src_md):
        raise OpenCodeGlobalError('PLATFORM_SKILL_INVALID', '平台技能目录缺少 SKILL.md', 400)
    meta, _ = parse_frontmatter(_read_text(src_md))
    fm_name = str(meta.get('name') or '').strip()
    if fm_name and fm_name != name:
        raise OpenCodeGlobalError('NAME_MISMATCH', SKILL_NAME_MISMATCH_HELP, 400)
    _check_dir_size(skills_dir(), extra_bytes=_tree_size(platform_skill_dir))
    dest = resolve_under(skills_dir(), name)
    existing = find_skill_dir(name)
    if existing and not overwrite:
        raise OpenCodeGlobalError('ALREADY_EXISTS', f'全局 skill「{name}」已存在', 409)
    if existing:
        shutil.rmtree(existing)
    os.makedirs(skills_dir(), exist_ok=True)
    shutil.copytree(platform_skill_dir, dest)
    return name


# ---------------------------------------------------------------------------
# Skill auxiliary files (scripts, templates, assets — everything besides the
# SKILL.md managed by the editor above)
# ---------------------------------------------------------------------------

FILE_READ_MAX_BYTES = 256 * 1024


def _resolve_skill_file(name: str, rel_path: str) -> str:
    name = validate_name(name)
    raw = (rel_path or '').replace('\\', '/')
    if raw.startswith('/'):
        # A leading separator signals an absolute-path attempt — refuse it
        # outright instead of silently stripping it into a relative path.
        raise OpenCodeGlobalError('PATH_UNSAFE', '非法的文件路径')
    rel = raw.strip('/')
    parts = rel.split('/') if rel else []
    if not parts or any(p in ('', '.', '..') for p in parts):
        raise OpenCodeGlobalError('PATH_UNSAFE', '非法的文件路径')
    root = _skill_dir(name)
    # 软链接越界防护（Spec §13）：目录名或中间路径任一环节是链接即拒绝
    _reject_symlink_components(root, *parts)
    return resolve_under(root, *parts)


def _is_binary_file(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            return b'\x00' in f.read(8192)
    except OSError:
        return False


def skill_dir_files(name: str) -> list[dict]:
    """Recursively list every file in a global skill directory (relative
    forward-slash paths, sorted) — the auxiliary files the editor can't reach."""
    name = validate_name(name)
    skill_dir = find_skill_dir(name)
    if not skill_dir:
        raise OpenCodeGlobalError('NOT_FOUND', f'全局 skill「{name}」不存在', 404)
    files: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d != '.git']
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if os.path.isfile(fp):
                rel = os.path.relpath(fp, skill_dir).replace(os.sep, '/')
                st = os.stat(fp)
                files.append({'name': fn, 'path': rel, 'size': st.st_size,
                              'mtime': int(st.st_mtime)})
    files.sort(key=lambda f: f['path'])
    return files


def read_skill_file(name: str, rel_path: str) -> dict:
    """Read one auxiliary file: {content, truncated, binary, size, mtime}.
    Binary files return empty content + binary=True; text is capped at
    FILE_READ_MAX_BYTES (truncated=True above that — callers must treat
    truncated content as read-only, saving it back would lose data)."""
    abs_path = _resolve_skill_file(name, rel_path)
    if not os.path.isfile(abs_path):
        raise OpenCodeGlobalError('NOT_FOUND', '文件不存在', 404)
    st = os.stat(abs_path)
    if _is_binary_file(abs_path):
        return {'content': '', 'truncated': False, 'binary': True,
                'size': st.st_size, 'mtime': int(st.st_mtime)}
    with open(abs_path, 'rb') as f:
        raw = f.read(FILE_READ_MAX_BYTES + 1)
    truncated = len(raw) > FILE_READ_MAX_BYTES
    content = raw[:FILE_READ_MAX_BYTES].decode('utf-8', errors='replace')
    return {'content': content, 'truncated': truncated, 'binary': False,
            'size': st.st_size, 'mtime': int(st.st_mtime)}


def write_skill_file(name: str, rel_path: str, content: str) -> dict:
    """Create or overwrite one auxiliary text file. Guards: the target must
    not be binary (can't round-trip through the text editor) and, when it
    already exists, not larger than the read cap — otherwise an editor that
    only loaded the truncated view could silently shrink the file."""
    _check_content_size(content)
    abs_path = _resolve_skill_file(name, rel_path)
    if os.path.isdir(abs_path):
        raise OpenCodeGlobalError('PATH_IS_DIR', '目标是目录，不能作为文件写入', 400)
    if os.path.islink(abs_path):
        raise OpenCodeGlobalError('PATH_UNSAFE', '受控目录内不允许软链接')
    if os.path.isfile(abs_path):
        if _is_binary_file(abs_path):
            raise OpenCodeGlobalError(
                'BINARY_FILE', '二进制文件不支持在线编辑，请本地修改后重新上传技能', 400)
        if os.path.getsize(abs_path) > FILE_READ_MAX_BYTES:
            raise OpenCodeGlobalError(
                'FILE_TOO_LARGE', f'文件超过 {FILE_READ_MAX_BYTES // 1024} KB，'
                '在线编辑只支持较小文件，请本地修改后重新上传技能', 413)
    _check_dir_size(os.path.dirname(abs_path), extra_bytes=len(content.encode('utf-8')))
    _atomic_write(abs_path, content)
    return {'path': rel_path.replace('\\', '/').strip('/'), 'changed': True}


def delete_skill_file(name: str, rel_path: str) -> None:
    abs_path = _resolve_skill_file(name, rel_path)
    if not os.path.isfile(abs_path):
        raise OpenCodeGlobalError('NOT_FOUND', '文件不存在', 404)
    os.remove(abs_path)


# ---------------------------------------------------------------------------
# Agents (global dir): <agents_dir>/<name>.md
# ---------------------------------------------------------------------------

_AGENT_WRITE_FIELDS = ('name', 'description', 'mode', 'model',
                       'temperature', 'top_p', 'disable')


def _agent_md_path(name: str) -> str:
    return resolve_under(agents_dir(), f'{name}.md')


def list_agent_files() -> list[dict]:
    root = agents_dir()
    items: list[dict] = []
    if not os.path.isdir(root):
        return items
    for fn in sorted(os.listdir(root)):
        if not fn.lower().endswith('.md'):
            continue
        md = os.path.join(root, fn)
        if not os.path.isfile(md):
            continue
        try:
            meta, body = parse_frontmatter(_read_text(md))
        except OSError:
            continue
        name = fn[:-3]
        st = os.stat(md)
        # A "pure disable switch" file shadows a built-in with disable:true and
        # carries no prompt of its own — the UI treats it as an off toggle.
        meta_wo = {k: v for k, v in meta.items() if k != 'disable'}
        is_switch = meta.get('disable') is True and not body.strip() \
            and set(meta_wo) <= {'name', 'description'}
        items.append({
            'name': str(meta.get('name') or name),
            'fileName': name,
            'description': str(meta.get('description') or ''),
            'mode': str(meta.get('mode') or ''),
            'model': str(meta.get('model') or ''),
            'disable': meta.get('disable') is True,
            'isDisableSwitch': is_switch,
            'size': st.st_size,
            'mtime': int(st.st_mtime),
            'path': md,
        })
    return items


def read_agent(name: str) -> dict:
    name = validate_name(name)
    _reject_symlink_components(agents_dir(), f'{name}.md')
    md = _agent_md_path(name)
    if not os.path.isfile(md):
        raise OpenCodeGlobalError('NOT_FOUND', f'全局 agent「{name}」不存在', 404)
    content = _read_text(md)
    meta, body = parse_frontmatter(content)
    st = os.stat(md)
    return {'name': name, 'meta': meta, 'body': body, 'content': content,
            'size': st.st_size, 'mtime': int(st.st_mtime)}


def _agent_frontmatter(name: str, fields: dict) -> dict:
    mode = (fields.get('mode') or '').strip()
    if mode and mode not in ('primary', 'subagent', 'all'):
        raise OpenCodeGlobalError('BAD_MODE', 'mode 仅允许 primary / subagent / all')
    meta: dict = {'name': name}
    description = (fields.get('description') or '').strip()
    if not description:
        raise OpenCodeGlobalError('DESCRIPTION_REQUIRED', 'description 必填')
    meta['description'] = description
    if mode:
        meta['mode'] = mode
    model = (fields.get('model') or '').strip()
    if model:
        meta['model'] = model
    for key in ('temperature', 'top_p'):
        val = fields.get(key)
        if val is not None and val != '':
            try:
                meta[key] = float(val)
            except (TypeError, ValueError):
                raise OpenCodeGlobalError('BAD_NUMBER', f'{key} 必须是数字')
    return meta


def write_agent(name: str, fields: dict | None = None,
                body: str | None = None, content: str | None = None) -> dict:
    """Create/update an agent. Either `{fields, body}` (frontmatter is
    regenerated) or full raw `content` (round-trips hand-written files)."""
    name = validate_name(name)
    if content is None:
        if fields is None:
            raise OpenCodeGlobalError('BAD_REQUEST', '缺少 fields 或 content')
        meta = _agent_frontmatter(name, fields)
        content = serialize_frontmatter(meta, body or '')
    _check_content_size(content)
    _check_dir_size(agents_dir(), extra_bytes=len(content.encode('utf-8')))
    _reject_symlink_components(agents_dir(), f'{name}.md')
    md = _agent_md_path(name)
    if os.path.isfile(md) and _read_text(md) == content:
        return {'name': name, 'changed': False}
    _atomic_write(md, content)
    return {'name': name, 'changed': True}


def delete_agent(name: str) -> None:
    name = validate_name(name)
    md = _agent_md_path(name)
    if not os.path.isfile(md):
        raise OpenCodeGlobalError('NOT_FOUND', f'全局 agent「{name}」不存在', 404)
    os.remove(md)


def set_agent_disabled(name: str, disabled: bool) -> dict:
    """Toggle any agent (typically a built-in).

    disable: a file that already exists gets the `disable: true` key added in
    place (its prompt/frontmatter is preserved); a built-in with no file gets
    a pure switch file created. enable: removes the key from a real file, or
    deletes a pure switch file outright.
    """
    name = validate_name(name)
    md = _agent_md_path(name)
    if disabled:
        if os.path.isfile(md):
            meta, body = parse_frontmatter(_read_text(md))
            meta.setdefault('name', name)
            meta.setdefault('description', f'禁用 {name}')
            meta['disable'] = True
            _atomic_write(md, serialize_frontmatter(meta, body))
        else:
            content = serialize_frontmatter(
                {'name': name, 'description': f'禁用 {name}', 'disable': True}, '')
            _atomic_write(md, content)
        return {'name': name, 'disable': True}
    if not os.path.isfile(md):
        raise OpenCodeGlobalError('NOT_FOUND', f'全局 agent「{name}」没有禁用文件', 404)
    content = _read_text(md)
    meta, body = parse_frontmatter(content)
    if meta.get('disable') is True and not body.strip():
        os.remove(md)  # pure switch file — enabling means removing it
    else:
        meta.pop('disable', None)
        _atomic_write(md, serialize_frontmatter(meta, body))
    return {'name': name, 'disable': False}


# ---------------------------------------------------------------------------
# Runtime state: merge files with the live serve's view
# ---------------------------------------------------------------------------

def _runtime_lists() -> tuple[list, list]:
    """Best-effort (skill_list, agent_list) from the running serve; empty
    lists when it's down — runtime status then just reports 'serveOffline'."""
    from utils.opencode_client import OpenCodeClient
    client = OpenCodeClient(config.OPENCODE_BASE_URL)
    try:
        skills = client.list_skills() or []
    except Exception:
        skills = []
    try:
        agents = client.list_agents() or []
    except Exception:
        agents = []
    return skills, agents


def serve_health() -> dict:
    """GET /global/health → {healthy, version}; False + None when unreachable."""
    try:
        resp = requests.get(f'{config.OPENCODE_BASE_URL.rstrip("/")}/global/health',
                            timeout=3)
        data = resp.json() if resp.ok else {}
        return {'healthy': bool(data.get('healthy')), 'version': data.get('version')}
    except Exception:
        return {'healthy': False, 'version': None}


def merged_skills() -> dict:
    """Filesystem entries ∪ live serve view → items with `source` and
    `runtime` ('loaded' | 'pending' | 'serveOffline')."""
    files = {it['name']: it for it in list_skill_files()}
    live, _agents = _runtime_lists()
    loaded_locations = {os.path.normpath(s.get('location') or '')
                        for s in live if s.get('location')}
    live_by_name = {s.get('name'): s for s in live}
    runtime_offline = not live

    items: list[dict] = []
    for name, f in files.items():
        is_loaded = os.path.normpath(f['path']) in loaded_locations
        items.append({
            'name': name,
            'description': f['description'],
            'source': 'global',
            'runtime': 'serveOffline' if runtime_offline else
                       ('loaded' if is_loaded else 'pending'),
            'size': f['size'], 'mtime': f['mtime'],
        })
    for sname, s in live_by_name.items():
        if sname in files:
            continue
        loc = str(s.get('location') or '')
        if loc == '<built-in>':
            source = 'builtin'
        elif os.path.realpath(loc).startswith(
                os.path.realpath(global_dir()) + os.sep):
            # Loaded from the OPENCODE global config area (skill/、skills/ 等
            # 受管根，或文件已被删但 serve 还在内存里持着 —— 下次重启消失）。
            # 配置区内的一切技能都可在线编辑，归为 global 而非 external。
            source = 'global'
            # 但仅在受管技能根目录下的才有 location 字段意义；给出 location
            # 供 UI 显示，read/write 走 location 所在目录的多根解析。
        else:
            source = 'external'
        items.append({
            'name': sname,
            'description': str(s.get('description') or ''),
            'source': source,
            'runtime': 'loaded',
            'location': loc,
            'size': None, 'mtime': None,
        })
    items.sort(key=lambda it: (it['source'] != 'global', it['name'].lower()))
    return {'items': items,
            'pendingCount': sum(1 for it in items if it['runtime'] == 'pending')}


def merged_agents() -> dict:
    files = {it['fileName']: it for it in list_agent_files()}
    _live, live_agents = _runtime_lists()
    live_by_name = {a.get('name'): a for a in live_agents}
    runtime_offline = not live_agents

    items: list[dict] = []
    for fname, f in files.items():
        live = live_by_name.get(f['name'])
        if f['isDisableSwitch']:
            runtime = 'disabled'
        else:
            runtime = 'serveOffline' if runtime_offline else \
                ('loaded' if live else 'pending')
        items.append({
            'name': f['name'],
            'fileName': fname,
            'description': f['description'],
            'mode': f['mode'] or (live or {}).get('mode') or '',
            'model': f['model'] or (((live or {}).get('model') or {}).get('modelID')
                                    if isinstance((live or {}).get('model'), dict) else '') or '',
            'source': 'file',
            'native': bool(live.get('native')) if live else False,
            'runtime': runtime,
            'size': f['size'], 'mtime': f['mtime'],
        })
    for aname, a in live_by_name.items():
        if any(it['name'] == aname for it in items):
            continue
        items.append({
            'name': aname,
            'fileName': None,
            'description': str(a.get('description') or ''),
            'mode': str(a.get('mode') or ''),
            'model': ((a.get('model') or {}).get('modelID')
                      if isinstance(a.get('model'), dict) else '') or '',
            'source': 'builtin' if a.get('native') else 'plugin',
            'native': bool(a.get('native')),
            'runtime': 'loaded',
            'size': None, 'mtime': None,
        })
    order = {'file': 0, 'builtin': 1, 'plugin': 2}
    items.sort(key=lambda it: (order.get(it['source'], 9), it['name'].lower()))
    return {'items': items,
            'pendingCount': sum(1 for it in items if it['runtime'] == 'pending')}


# ---------------------------------------------------------------------------
# Active workload + serve restart
# ---------------------------------------------------------------------------

def active_workload() -> dict:
    """Counts the work a serve restart would interrupt: batch/open-api child
    sessions not yet terminal + live interactive sessions."""
    from db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT "
                    " (SELECT count(*) FROM ai_chat_sessions "
                    "   WHERE (batch_id IS NOT NULL OR api_key_id IS NOT NULL) "
                    "     AND status IN ('pending','running')) AS batch_children, "
                    " (SELECT count(*) FROM ai_chat_sessions "
                    "   WHERE batch_id IS NULL AND api_key_id IS NULL "
                    "     AND status = 'active') AS interactive_sessions, "
                    " (SELECT count(*) FROM ai_chat_batches "
                    "   WHERE status IN ('pending','running')) AS active_batches"
                )
                row = cur.fetchone()
        return {'batchChildren': row[0], 'interactiveSessions': row[1],
                'activeBatches': row[2]}
    except Exception:
        logger.warning('active_workload query failed', exc_info=True)
        return {'batchChildren': 0, 'interactiveSessions': 0, 'activeBatches': 0}


def serve_port() -> int:
    """The port OPENCODE_BASE_URL listens on (restart + ownership checks)."""
    from urllib.parse import urlparse
    return urlparse(config.OPENCODE_BASE_URL).port or 4096


def serve_listener_pids(port: int | None = None) -> list[int]:
    """Public wrapper: PIDs with a LISTEN socket on the serve port."""
    return _serve_pids_on_port(port if port is not None else serve_port())


def config_generation() -> str:
    """Deterministic hash of the managed config tree (skill/ + agent/).

    Covers path, size and content of every file under both controlled dirs —
    the "configGeneration" the admin UI can compare against the runtime's
    loaded state (Spec §2 配置生效语义 / §7.3 config_generation). Unreadable
    entries contribute their path only; an empty/missing tree hashes to the
    empty-string sentinel so the UI can tell "nothing managed" apart."""
    import hashlib

    root = global_dir()
    h = hashlib.sha256()
    seen = False
    for sub in ('skill', 'agent'):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for fn in sorted(filenames):
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, root).replace(os.sep, '/')
                h.update(rel.encode('utf-8', 'replace'))
                try:
                    st = os.stat(fp)
                    h.update(f'|{st.st_size}|'.encode('ascii'))
                    with open(fp, 'rb') as f:
                        h.update(hashlib.sha256(f.read()).digest())
                    seen = True
                except OSError:
                    h.update(b'|?|')
    return h.hexdigest() if seen else ''


def _serve_pids_on_port(port: int) -> list[int]:
    """Find PIDs with a LISTEN socket on `port`. Windows: netstat -ano;
    POSIX: lsof, falling back to fuser."""
    pids: list[int] = []
    try:
        if sys.platform == 'win32':
            out = subprocess.run(['netstat', '-ano', '-p', 'TCP'],
                                 capture_output=True, timeout=15).stdout.decode(
                                     'utf-8', 'replace')
            for line in out.splitlines():
                parts = line.split()
                # Proto LocalAddress ForeignAddress State PID
                if len(parts) >= 5 and parts[3].upper() == 'LISTENING' \
                        and parts[1].rsplit(':', 1)[-1] == str(port):
                    try:
                        pids.append(int(parts[4]))
                    except ValueError:
                        pass
        else:
            out = subprocess.run(['lsof', '-t', '-i', f':{port}'],
                                 capture_output=True, timeout=15)
            if out.returncode == 0:
                pids = [int(p) for p in out.stdout.decode().split()]
            else:
                out = subprocess.run(['fuser', f'{port}/tcp'],
                                     capture_output=True, timeout=15)
                pids = [int(p) for p in out.stdout.decode().split()]
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        pass
    return sorted(set(p for p in pids if p > 0))


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                               capture_output=True, timeout=15)
            else:
                import signal
                os.kill(pid, signal.SIGTERM)
        except (subprocess.SubprocessError, OSError):
            logger.warning('failed to kill opencode serve pid=%s', pid, exc_info=True)


def restart_serve() -> dict:
    """Restart `opencode serve` so freshly written skill/agent files load.

    Ownership-gated (Spec §11 进程治理): only a listener the platform itself
    launched (recorded by utils.opencode_ownership at spawn time) may be
    killed. An unknown/external listener — hand-launched serve, service
    manager, another developer's instance — is never touched; the admin stops
    it by hand, then retries.

    After the spawn, the new listening PID is discovered and recorded so the
    next restart stays platform-owned, and OPENCODE_GLOBAL_DIR is passed to
    the child explicitly (Spec P0: 自定义全局目录必须与 serve 实际读取目录
    一致，不能依赖子进程对默认路径的自行推断). Raises
    OpenCodeGlobalError('RESTART_FAILED') if health never comes back — the
    admin then restarts by hand.
    """
    from utils import opencode_launch
    from utils import opencode_ownership as ownership

    port = serve_port()

    pids = _serve_pids_on_port(port)
    if pids:
        if not ownership.platform_owned(port, listener_pids=pids):
            st = ownership.status(port, listener_pids=pids)
            raise OpenCodeGlobalError(
                'EXTERNAL_PROCESS',
                f'端口 {port} 上的 OpenCode 进程（PID {pids[0]}）不是平台启动的，'
                '为避免误杀外部实例已拒绝重启；请先手动停止该进程（模式：'
                f'{st.get("mode")}）', 409)
        _kill_pids(pids)
        time.sleep(1.0)

    target, use_shell = opencode_launch.serve_launch()
    creationflags = 0
    if sys.platform == 'win32':
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # 与 proxy.py 同一编码策略：UTF-8 执行环境（见 opencode_launch.child_env）；
    # 并显式钉死 OPENCODE_GLOBAL_DIR —— config 里的值（.env 可自定义）就是
    # serve 必须读取的目录，两侧永不漂移。
    env = opencode_launch.child_env()
    env['OPENCODE_GLOBAL_DIR'] = global_dir()
    subprocess.Popen(target, shell=use_shell, cwd=opencode_launch.serve_cwd(),
                     env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=creationflags,
                     start_new_session=(sys.platform != 'win32'))

    deadline = time.time() + max(10, config.OPENCODE_RESTART_TIMEOUT_SEC)
    last = {'healthy': False, 'version': None}
    new_pid: int | None = None

    def _discover_new_pid() -> int | None:
        """Find a listener PID that isn't one of the killed ones."""
        fresh = [p for p in _serve_pids_on_port(port) if p not in pids]
        return fresh[0] if fresh else None

    while time.time() < deadline:
        time.sleep(1.5)
        if new_pid is None:
            new_pid = _discover_new_pid()
            if new_pid is not None:
                try:
                    ownership.record(new_pid, port)
                except OSError:
                    logger.warning('failed to persist serve ownership', exc_info=True)
        last = serve_health()
        if last['healthy']:
            # A warm serve can pass health before its listener shows up in
            # netstat — keep polling briefly so ownership still gets recorded
            # (an unrecorded serve would be kill-forbidden on the NEXT restart).
            for _ in range(10):
                if new_pid is not None:
                    break
                time.sleep(1.0)
                new_pid = _discover_new_pid()
                if new_pid is not None:
                    try:
                        ownership.record(new_pid, port)
                    except OSError:
                        logger.warning('failed to persist serve ownership',
                                       exc_info=True)
            logger.info('opencode serve restarted, version=%s pid=%s',
                        last.get('version'), new_pid)
            return {'ok': True, 'version': last.get('version'),
                    'pid': new_pid, 'killedPids': pids,
                    'policy': config.OPENCODE_RESTART_POLICY}
    logger.error('opencode serve restart failed health check: %s', last)
    raise OpenCodeGlobalError(
        'RESTART_FAILED',
        f'OpenCode 已重新拉起但 {config.OPENCODE_RESTART_TIMEOUT_SEC}s 内未通过健康检查，'
        f'请手动确认 serve 状态（命令：{opencode_launch.serve_cmd_display()}）', 502)
