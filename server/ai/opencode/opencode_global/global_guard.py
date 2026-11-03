"""Validation & traversal guards for the OpenCode global config dir.

Verbatim split out of utils/opencode_global.py (backend restructure
batch 2); the module background docstring lives in global_paths.py.
Grouping axis: name/path validation, symlink escape & size caps
(Spec §13)."""

import os
import re

# Wide enough for platform-library skill names (which allow uppercase, `_`)
# yet safe: no path separators, no leading dot, so `..` can never form.
_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
_WIN_RESERVED = {'con', 'prn', 'aux', 'nul'} | {f'com{i}' for i in range(1, 10)} \
    | {f'lpt{i}' for i in range(1, 10)}

class OpenCodeGlobalError(Exception):
    """User-facing error (message is safe to return as-is)."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

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

__all__ = [
    'MAX_MANAGED_FILE_BYTES',
    'MAX_MANAGED_TOTAL_BYTES',
    'OpenCodeGlobalError',
    '_NAME_RE',
    '_WIN_RESERVED',
    '_check_content_size',
    '_check_dir_size',
    '_reject_symlink_components',
    'resolve_under',
    'validate_name',
]
