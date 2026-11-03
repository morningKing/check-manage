"""Decode zip entry filenames that were stored without the UTF-8 flag.

Python's zipfile decodes entry filenames as CP437 when the UTF-8 flag
(flag_bits & 0x800) is absent. Zips produced by Chinese Windows tools
store GBK-encoded names, so those entries surface as mojibake. This
module recovers the original name by re-encoding via CP437 and trying
UTF-8 first (tools that store UTF-8 without setting the flag), then GBK.
"""

import os
import shutil
import zipfile


def decoded_zip_name(info: zipfile.ZipInfo) -> str:
    """Return the entry filename, re-decoding legacy non-UTF8 names."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def safe_extract_decoded(zf: zipfile.ZipFile, dest_dir: str,
                         on_error=None) -> None:
    """Extract all entries into dest_dir with decoded filenames.

    Refuses path traversal (zip-slip). Entries whose name cannot be
    decoded keep the zipfile default; on_error(name, exc) may raise to
    abort or return to skip.
    """
    base = os.path.abspath(dest_dir)
    for info in zf.infolist():
        try:
            name = decoded_zip_name(info).replace("\\", "/")
            target = os.path.abspath(os.path.join(base, name))
            if target != base and not target.startswith(base + os.sep):
                raise ValueError("zip contains path traversal")
            if name.endswith("/"):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        except Exception as exc:
            if on_error is not None:
                on_error(decoded_zip_name(info), exc)
            else:
                raise
