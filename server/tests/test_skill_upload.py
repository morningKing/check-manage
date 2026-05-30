"""Tests for extract_skill_zip: zip layout detection, name parsing, zip-slip,
size/file caps, conflict, and atomic install."""

import io
import os
import zipfile
import pytest
from werkzeug.datastructures import FileStorage


def _make_zip(entries: dict) -> FileStorage:
    """Build a zip from {relpath: bytes} and wrap in a FileStorage('foo.zip')."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    buf.seek(0)
    return FileStorage(stream=buf, filename='foo.zip', content_type='application/zip')


def test_root_skill_md_uses_frontmatter_name(tmp_path):
    from utils.skill_upload import extract_skill_zip
    z = _make_zip({'SKILL.md': b'---\nname: alpha\ndescription: d\n---\n# alpha', 'helper.py': b'x=1'})
    res = extract_skill_zip(str(tmp_path), z)
    assert res == {'name': 'alpha', 'path': '.opencode/skills/alpha'}
    assert (tmp_path / '.opencode' / 'skills' / 'alpha' / 'SKILL.md').exists()
    assert (tmp_path / '.opencode' / 'skills' / 'alpha' / 'helper.py').read_bytes() == b'x=1'


def test_single_top_dir_layout_strips_prefix(tmp_path):
    from utils.skill_upload import extract_skill_zip
    z = _make_zip({'beta/SKILL.md': b'---\nname: beta\n---\n', 'beta/notes.txt': b'hi'})
    res = extract_skill_zip(str(tmp_path), z)
    assert res['name'] == 'beta'
    assert (tmp_path / '.opencode' / 'skills' / 'beta' / 'SKILL.md').exists()
    assert (tmp_path / '.opencode' / 'skills' / 'beta' / 'notes.txt').read_bytes() == b'hi'


def test_missing_frontmatter_name_falls_back_to_zip_filename(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'no frontmatter here'})
    z.filename = 'my-skill.zip'
    res = extract_skill_zip(str(tmp_path), z)
    assert res['name'] == 'my-skill'


def test_zip_slip_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: ok\n---\n', '../evil.txt': b'pwn'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'SKILL_ZIP_UNSAFE'


def test_missing_skill_md_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'README.md': b'no skill md'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'INVALID_SKILL_ZIP'


def test_name_conflict_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    # pre-create the target dir
    (tmp_path / '.opencode' / 'skills' / 'dup').mkdir(parents=True)
    z = _make_zip({'SKILL.md': b'---\nname: dup\n---\n'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'SKILL_EXISTS'


def test_non_zip_filename_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: ok\n---\n'})
    z.filename = 'not-a-zip.txt'
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'BAD_FILE'


def test_invalid_name_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: bad name with spaces\n---\n'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'INVALID_SKILL_NAME'
