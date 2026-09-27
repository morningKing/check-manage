"""Filesystem-safe filename sanitizer that preserves Unicode (e.g. Chinese).

`werkzeug.utils.secure_filename` strips every non-ASCII character, so a name
like ``巡检报告 2026.pdf`` collapses to ``2026.pdf`` (and a purely-Chinese name
becomes empty). This helper keeps Unicode letters/digits while removing the
things that actually make a name unsafe as a final path component: directory
separators, parent-dir traversal, control characters, Windows-reserved device
names, and over-long names.

It is meant for the **last path segment only** — callers must still place the
result inside an already-constrained directory (unique id dir / staging dir /
``safe_resolve``), which this module does not do.
"""
import os
import re
import unicodedata
import uuid

# Windows reserved device names (case-insensitive, with or without extension).
_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}

# Control characters are removed outright; visible illegal chars become "_".
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
_BAD = re.compile(r'[<>:"/\\|?*]')

_MAX_BYTES = 200  # leave headroom under the typical 255-byte limit


def safe_filename(filename: str, fallback_prefix: str = 'upload_') -> str:
    """Return a filesystem-safe, Unicode-preserving basename.

    Always returns a non-empty string; falls back to ``<prefix><hex>`` when the
    input has no usable characters.
    """
    # Keep only the basename — drop any path the client may have sent.
    name = os.path.basename(str(filename or '').replace('\\', '/')).strip()
    # Normalize so visually-identical names store/compare consistently.
    name = unicodedata.normalize('NFC', name)
    # Drop control characters outright; replace visible illegal chars with "_".
    name = _BAD.sub('_', _CONTROL.sub('', name))
    # Collapse whitespace; strip leading dots/spaces (hidden files / traversal).
    name = re.sub(r'\s+', ' ', name).strip().lstrip('.').strip()
    if not name:
        return fallback_prefix + uuid.uuid4().hex[:8]
    # Guard Windows reserved device names (matched on the stem).
    if name.split('.', 1)[0].upper() in _RESERVED:
        name = '_' + name
    # Cap byte-length, preserving the extension.
    if len(name.encode('utf-8')) > _MAX_BYTES:
        root, ext = os.path.splitext(name)
        while root and len((root + ext).encode('utf-8')) > _MAX_BYTES:
            root = root[:-1]
        name = (root + ext) or (fallback_prefix + uuid.uuid4().hex[:8])
    return name
