"""Validate and extract an OpenCode skill zip into a session workspace.

A skill is a folder containing SKILL.md (YAML frontmatter with `name`) plus
helper files. OpenCode auto-discovers skills at
`<workspace>/.opencode/skills/<name>/` (verified via GET /skill?directory=ws).
"""

import io
import os
import re
import secrets
import shutil
import zipfile

from werkzeug.datastructures import FileStorage

MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_ZIP_ENTRIES = 200
SKILLS_SUBDIR = ".opencode/skills"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SkillUploadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_join(base: str, rel: str) -> str:
    """Join base+rel and refuse if the result escapes base (zip-slip guard)."""
    final = os.path.abspath(os.path.join(base, rel))
    base_abs = os.path.abspath(base)
    if final != base_abs and not final.startswith(base_abs + os.sep):
        raise SkillUploadError("SKILL_ZIP_UNSAFE", "zip contains path traversal")
    return final


def _parse_name_from_md(md_bytes: bytes) -> str | None:
    """Parse `name` from SKILL.md YAML frontmatter (returns None if absent)."""
    text = md_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^name\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def extract_skill_zip(workspace_path: str, file_storage: FileStorage) -> dict:
    """Extract `file_storage` (a .zip) into <workspace>/.opencode/skills/<name>/.

    Returns {"name": <name>, "path": ".opencode/skills/<name>"}.
    Raises SkillUploadError on any validation failure.
    """
    fname = (file_storage.filename or "").lower()
    if not fname.endswith(".zip"):
        raise SkillUploadError("BAD_FILE", "file must be a .zip")

    data = file_storage.read()
    if len(data) > MAX_ZIP_BYTES:
        raise SkillUploadError("SKILL_ZIP_TOO_LARGE", "zip exceeds 5 MiB")
    try:
        zf_ctx = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillUploadError("SKILL_ZIP_INVALID", "zip is corrupted")

    with zf_ctx as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > MAX_ZIP_ENTRIES:
            raise SkillUploadError("SKILL_ZIP_TOO_MANY_FILES", "too many entries")

        norm_names = [m.filename.replace("\\", "/") for m in members]
        skill_at_root = any(n.lower() == "skill.md" for n in norm_names)
        strip_prefix = ""
        if not skill_at_root:
            top_dirs = {n.split("/", 1)[0] for n in norm_names if "/" in n}
            root_files = [n for n in norm_names if "/" not in n]
            if len(top_dirs) == 1 and not root_files:
                top = next(iter(top_dirs))
                if any(n.lower() == f"{top.lower()}/skill.md" for n in norm_names):
                    strip_prefix = top + "/"
                else:
                    raise SkillUploadError("INVALID_SKILL_ZIP", "missing SKILL.md")
            else:
                raise SkillUploadError("INVALID_SKILL_ZIP", "missing SKILL.md")

        target_md = (strip_prefix + "SKILL.md").lower()
        md_member = next(
            (m for m in members if m.filename.replace("\\", "/").lower() == target_md),
            None,
        )
        if md_member is None:  # pragma: no cover - guarded above
            raise SkillUploadError("INVALID_SKILL_ZIP", "missing SKILL.md")
        name = _parse_name_from_md(zf.read(md_member))
        if not name:
            name = os.path.splitext(os.path.basename(file_storage.filename or ""))[0]
        if not _NAME_RE.match(name):
            raise SkillUploadError("INVALID_SKILL_NAME", "name must match [A-Za-z0-9_-]{1,64}")

        skills_root = os.path.join(workspace_path, *SKILLS_SUBDIR.split("/"))
        final_dir = os.path.join(skills_root, name)
        if os.path.exists(final_dir):
            raise SkillUploadError("SKILL_EXISTS", f"skill {name!r} already exists in this session")

        os.makedirs(skills_root, exist_ok=True)
        tmp_dir = os.path.join(skills_root, ".tmp-" + secrets.token_hex(6))
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            for m in members:
                rel = m.filename.replace("\\", "/")
                if strip_prefix and rel.startswith(strip_prefix):
                    rel = rel[len(strip_prefix):]
                if not rel:
                    continue
                dest = _safe_join(tmp_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(m) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
            os.rename(tmp_dir, final_dir)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    return {"name": name, "path": f"{SKILLS_SUBDIR}/{name}"}
